"""Page through candidates until each query has enough actual matches."""
import pandas as pd
import analyzer as a

REASONS = {
    "target": "指定件数に到達",
    "exhausted": "APIが返す次のページがありません",
    "budget": "今回の探索上限に到達",
    "repeat": "同じページが返されたため停止",
    "error": "APIエラーで停止（取得済みの一致動画を表示）",
}


def collect_matching(jobs, target, api_key, filters, region="JP", max_pages=20, progress=None):
    """One shared page budget, round-robin jobs, one channel map per run.

    Each job has a unique label and kind=search/popular; search jobs may set
    query/start/end/order. Returns exact target per job when enough matches exist.
    No underfilled query is silently described as complete.
    """
    if target < 1 or max_pages < 1:
        raise ValueError("件数と探索上限は1以上にしてください。")
    if len({job["label"] for job in jobs}) != len(jobs):
        raise ValueError("検索グループ名が重複しています。")
    states = [{"job": j, "token": None, "tokens": set(), "seen": set(), "parts": [], "pages": 0,
               "scanned": 0, "matched": 0, "reason": None} for j in jobs]
    used, channel_map, error = 0, {}, ""
    video_map = {}
    while used < max_pages and any(s["reason"] is None for s in states):
        pending = []
        for state in states:
            if state["reason"] is not None or used >= max_pages:
                continue
            job = state["job"]
            params = {"maxResults": 50, "regionCode": region, "videoCategoryId": a.GAMING_CATEGORY_ID}
            if state["token"]:
                params["pageToken"] = state["token"]
            popular = job.get("kind") == "popular"
            if popular:
                params.update(part="snippet,statistics,contentDetails", chart="mostPopular")
            else:
                params.update(part="snippet", type="video", q=job.get("query", ""), order=job.get("order", "viewCount"))
                for param, field in (("publishedAfter", "start"), ("publishedBefore", "end")):
                    if job.get(field):
                        params[param] = a.iso_z(job[field])
                if filters.get("language"):
                    params["relevanceLanguage"] = filters["language"]
                if filters.get("format") == "Shorts候補":
                    # API short means <4 minutes; enforce <=180 seconds locally.
                    params["videoDuration"] = "short"
                # Do not use API 'long': it means >20 min and would lose 3–20 min videos.
            used += 1
            state["pages"] += 1
            try:
                data = a.api_get("videos" if popular else "search", params, api_key)
            except RuntimeError as exc:
                error = str(exc)
                break
            token = data.get("nextPageToken")
            state["next_reason"] = "repeat" if token and token in state["tokens"] else (None if token else "exhausted")
            if token:
                state["tokens"].add(token)
            state["token"] = token
            ids = []
            for item in data.get("items", []):
                vid = item.get("id") if popular else item.get("id", {}).get("videoId")
                if not vid or vid in state["seen"]:
                    continue
                state["seen"].add(vid)
                ids.append(vid)
                if popular:
                    video_map[vid] = item
            state["scanned"] += len(ids)
            pending.append((state, ids))
        try:
            missing = sorted({v for _, ids in pending for v in ids} - set(video_map))
            for i in range(0, len(missing), 50):
                batch = missing[i:i+50]
                data = a.api_get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "maxResults": 50}, api_key)
                video_map.update({v: None for v in batch})
                video_map.update({item["id"]: item for item in data.get("items", [])})
            needed = list(dict.fromkeys(v for _, ids in pending for v in ids if video_map.get(v)))
            df = a.video_rows([video_map[v] for v in needed])
            df = a.add_channel_stats(df, api_key, channel_map)
            for state, ids in pending:
                page = df[df.video_id.isin(ids)] if not df.empty else pd.DataFrame()
                matches = a.filter_videos(page, filters)
                if not matches.empty:
                    # Keep API encounter order; ranking is within the selected sample.
                    order = {v: i for i, v in enumerate(ids)}
                    matches = matches.assign(_order=matches.video_id.map(order)).sort_values("_order").drop(columns="_order")
                    state["parts"].append(matches)
                    state["matched"] += len(matches)
                state["reason"] = "target" if state["matched"] >= target else state["next_reason"]
        except RuntimeError as exc:
            error = str(exc)
        if progress:
            progress(used, sum(min(s["matched"], target) for s in states), target * len(states))
        if error:
            break
    output, reports = [], []
    for state in states:
        if state["parts"]:
            df = pd.concat(state["parts"], ignore_index=True).head(target).copy()
            df["group"] = state["job"]["label"]
            output.append(df)
        reason = state["reason"] or ("error" if error else "budget")
        reports.append({"group": state["job"]["label"], "requested": target, "matched": min(state["matched"], target),
                        "candidates_checked": state["scanned"], "pages": state["pages"], "reason": reason, "status": REASONS[reason]})
    return {"videos": pd.concat(output, ignore_index=True) if output else pd.DataFrame(columns=["group"]),
            "report": pd.DataFrame(reports), "pages": used, "error": error}
