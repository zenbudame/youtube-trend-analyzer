from datetime import datetime, timedelta, timezone
from typing import List

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://www.googleapis.com/youtube/v3"
GAMING_CATEGORY_ID = "20"




@st.cache_data(ttl=900, max_entries=256, show_spinner=False)
def api_get(path: str, params: dict, api_key: str) -> dict:
    params = {**params, "key": api_key}
    try:
        r = requests.get(f"{API_BASE}/{path}", params=params, timeout=30)
    except requests.RequestException:
        raise RuntimeError("YouTube APIに接続できませんでした。時間をおいて再試行してください。") from None
    if not r.ok:
        messages = {400: "検索条件またはAPIキーを確認してください。",
                    403: "APIの有効化・キーの制限・クォータ残量を確認してください。",
                    429: "アクセスが集中しています。時間をおいて再試行してください。"}
        raise RuntimeError(f"YouTube APIエラー ({r.status_code}): " + messages.get(r.status_code, "時間をおいて再試行してください。"))
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("YouTube APIから正しい応答を取得できませんでした。") from None



def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def duration_to_seconds(s: str) -> int:
    # Minimal ISO-8601 PT parser for YouTube durations.
    import re
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    h, mnt, sec = [int(x or 0) for x in m.groups()]
    return h * 3600 + mnt * 60 + sec


def video_details(video_ids: List[str], api_key: str) -> pd.DataFrame:
    items = []
    video_ids = list(dict.fromkeys(video_ids))
    for i in range(0, len(video_ids), 50):
        data = api_get("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(video_ids[i:i+50]), "maxResults": 50,
        }, api_key)
        items.extend(data.get("items", []))
    return video_rows(items)


def video_rows(items: list) -> pd.DataFrame:
    rows = []
    for item in items:
        sn = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        published = pd.to_datetime(sn.get("publishedAt"), utc=True)
        age_hours = max((pd.Timestamp.now(tz="UTC") - published).total_seconds() / 3600, 1.0)
        views = int(stats.get("viewCount", 0) or 0)
        likes = int(stats.get("likeCount", 0) or 0)
        comments = int(stats.get("commentCount", 0) or 0)
        sec = duration_to_seconds(content.get("duration", ""))
        rows.append({
            "video_id": item.get("id"),
            "title": sn.get("title", ""),
            "channel": sn.get("channelTitle", ""),
            "channel_id": sn.get("channelId", ""),
            "published_at": published,
            "age_hours": age_hours,
            "views": views,
            "likes": likes,
            "comments": comments,
            "duration_sec": sec,
            "format": "Shorts候補" if sec <= 180 else "長尺",
            "views_per_hour": views / age_hours,
            "like_rate_pct": (likes / views * 100) if views else 0,
            "comment_rate_pct": (comments / views * 100) if views else 0,
            "url": f"https://www.youtube.com/watch?v={item.get('id')}",
        })
    return pd.DataFrame(rows)


def add_channel_stats(df: pd.DataFrame, api_key: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    ids = sorted(set(df["channel_id"].dropna()) - {""})
    channel_map = {}
    for i in range(0, len(ids), 50):
        data = api_get("channels", {
            "part": "statistics", "id": ",".join(ids[i:i+50]), "maxResults": 50,
        }, api_key)
        for item in data.get("items", []):
            s = item.get("statistics", {})
            hidden = s.get("hiddenSubscriberCount", False)
            count = s.get("subscriberCount")
            channel_map[item["id"]] = {
                "subscribers": int(count) if not hidden and count is not None else None,
                "subscriber_status": "非公開" if hidden else ("公開" if count is not None else "取得不可"),
                "channel_total_views": int(s["viewCount"]) if s.get("viewCount") is not None else None,
                "channel_video_count": int(s["videoCount"]) if s.get("videoCount") is not None else None,
            }
    out = df.copy()
    for name in ("subscribers", "channel_total_views", "channel_video_count"):
        out[name] = pd.to_numeric(out["channel_id"].map(lambda x: channel_map.get(x, {}).get(name)), errors="coerce")
    out["subscriber_status"] = out["channel_id"].map(lambda x: channel_map.get(x, {}).get("subscriber_status", "取得不可"))
    out["views_vs_subscribers"] = out["views"] / out["subscribers"].where(out["subscribers"] > 0)
    return out


def filter_videos(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """All numeric bounds are inclusive. Unknown values never satisfy numeric bounds."""
    if df.empty:
        return df.copy()
    mask = pd.Series(True, index=df.index)
    for col in ("subscribers", "channel_total_views", "channel_video_count"):
        low, high = filters.get(col, (None, None))
        if low is not None:
            mask &= df[col].ge(low)
        if high is not None:
            mask &= df[col].le(high)
    if filters.get("unknown") == "exclude":
        mask &= df["subscribers"].notna()
    elif filters.get("unknown") == "only":
        mask &= df["subscribers"].isna()
    if filters.get("min_ratio") is not None:
        mask &= df["views_vs_subscribers"].ge(filters["min_ratio"])
    if filters.get("min_views") is not None:
        mask &= df["views"].ge(filters["min_views"])
    if filters.get("small_max") is not None:
        mask &= df["subscribers"].le(filters["small_max"])
    if filters.get("format", "すべて") != "すべて":
        mask &= df["format"].eq(filters["format"])
    return df.loc[mask.fillna(False)].copy()


def score_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    # Robust, interpretable score: velocity + engagement + breakout ratio.
    vph = out["views_per_hour"].clip(lower=0)
    breakout = out.get("views_vs_subscribers", pd.Series(0, index=out.index)).clip(lower=0)
    like = out["like_rate_pct"].clip(lower=0)
    comment = out["comment_rate_pct"].clip(lower=0)
    out["trend_score"] = (
        45 * (vph.rank(pct=True)) +
        25 * (like.rank(pct=True)) +
        15 * (comment.rank(pct=True)) +
        15 * (breakout.rank(pct=True).fillna(0))
    ).round(1)
    return out.sort_values(["trend_score", "views_per_hour"], ascending=False)


@st.cache_data(ttl=900, max_entries=128, show_spinner=False)
def search_videos(query: str, days: int, max_results: int, api_key: str,
                  region: str = "JP", category_id: str = GAMING_CATEGORY_ID, enrich: bool = True) -> pd.DataFrame:
    after = datetime.now(timezone.utc) - timedelta(days=days)
    ids = []
    token = None
    remaining = max_results
    while remaining > 0:
        n = min(50, remaining)
        params = {
            "part": "snippet",
            "type": "video",
            "q": query,
            "publishedAfter": iso_z(after),
            "order": "viewCount",
            "maxResults": n,
            "regionCode": region,
            "videoCategoryId": category_id,
        }
        if token:
            params["pageToken"] = token
        data = api_get("search", params, api_key)
        ids.extend([x.get("id", {}).get("videoId") for x in data.get("items", []) if x.get("id", {}).get("videoId")])
        remaining -= n
        token = data.get("nextPageToken")
        if not token:
            break
    df = video_details(list(dict.fromkeys(ids)), api_key)
    return add_channel_stats(df, api_key) if enrich else df


def most_popular(max_results: int, api_key: str, region: str = "JP") -> pd.DataFrame:
    data = api_get("videos", {
        "part": "snippet,statistics,contentDetails",
        "chart": "mostPopular",
        "regionCode": region,
        "videoCategoryId": GAMING_CATEGORY_ID,
        "maxResults": min(max_results, 50),
    }, api_key)
    df = video_rows(data.get("items", []))
    return score_df(add_channel_stats(df, api_key))


def search_period(query: str, start: datetime, end: datetime, max_results: int, api_key: str, region: str) -> pd.DataFrame:
    ids = []
    token = None
    remaining = max_results
    while remaining > 0:
        n = min(50, remaining)
        p = {
            "part": "snippet", "type": "video", "q": query,
            "publishedAfter": iso_z(start), "publishedBefore": iso_z(end),
            "order": "viewCount", "maxResults": n, "regionCode": region,
            "videoCategoryId": GAMING_CATEGORY_ID,
        }
        if token:
            p["pageToken"] = token
        data = api_get("search", p, api_key)
        ids += [x.get("id", {}).get("videoId") for x in data.get("items", []) if x.get("id", {}).get("videoId")]
        remaining -= n
        token = data.get("nextPageToken")
        if not token:
            break
    return video_details(list(dict.fromkeys(ids)), api_key)


