import math
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://www.googleapis.com/youtube/v3"
GAMING_CATEGORY_ID = "20"

st.set_page_config(page_title="YouTube Trend Analyzer", page_icon="📈", layout="wide")


def api_get(path: str, params: dict, api_key: str) -> dict:
    params = {**params, "key": api_key}
    r = requests.get(f"{API_BASE}/{path}", params=params, timeout=30)
    if not r.ok:
        try:
            detail = r.json().get("error", {}).get("message", r.text)
        except Exception:
            detail = r.text
        raise RuntimeError(f"YouTube API error {r.status_code}: {detail}")
    return r.json()


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
    rows = []
    for i in range(0, len(video_ids), 50):
        ids = video_ids[i:i+50]
        data = api_get("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(ids),
            "maxResults": 50,
        }, api_key)
        for item in data.get("items", []):
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
        return df
    ids = df["channel_id"].dropna().unique().tolist()
    channel_map: Dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        data = api_get("channels", {
            "part": "statistics",
            "id": ",".join(ids[i:i+50]),
            "maxResults": 50,
        }, api_key)
        for item in data.get("items", []):
            s = item.get("statistics", {})
            channel_map[item["id"]] = {
                "subscribers": int(s.get("subscriberCount", 0) or 0),
                "channel_total_views": int(s.get("viewCount", 0) or 0),
            }
    out = df.copy()
    out["subscribers"] = out["channel_id"].map(lambda x: channel_map.get(x, {}).get("subscribers", 0))
    out["views_vs_subscribers"] = out.apply(
        lambda r: r["views"] / r["subscribers"] if r["subscribers"] else 0, axis=1
    )
    return out


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
        15 * (breakout.rank(pct=True))
    ).round(1)
    return out.sort_values(["trend_score", "views_per_hour"], ascending=False)


def search_videos(query: str, days: int, max_results: int, api_key: str,
                  region: str = "JP", category_id: str = GAMING_CATEGORY_ID) -> pd.DataFrame:
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
    return add_channel_stats(df, api_key)


def most_popular(max_results: int, api_key: str, region: str = "JP") -> pd.DataFrame:
    data = api_get("videos", {
        "part": "snippet,statistics,contentDetails",
        "chart": "mostPopular",
        "regionCode": region,
        "videoCategoryId": GAMING_CATEGORY_ID,
        "maxResults": min(max_results, 50),
    }, api_key)
    ids = [x["id"] for x in data.get("items", [])]
    df = video_details(ids, api_key)
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


def monthly_long_term(query: str, months: int, samples_per_month: int, api_key: str, region: str) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    rows = []
    for offset in range(months - 1, -1, -1):
        end = now - timedelta(days=30 * offset)
        start = end - timedelta(days=30)
        df = search_period(query, start, end, samples_per_month, api_key, region)
        rows.append({
            "month": start.strftime("%Y-%m"),
            "query": query,
            "videos_sampled": len(df),
            "sample_views": int(df["views"].sum()) if not df.empty else 0,
            "median_views": float(df["views"].median()) if not df.empty else 0,
            "median_views_per_hour": float(df["views_per_hour"].median()) if not df.empty else 0,
            "active_channels": int(df["channel_id"].nunique()) if not df.empty else 0,
        })
    return pd.DataFrame(rows)


def dataframe_download(df: pd.DataFrame, name: str):
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("CSVをダウンロード", csv, file_name=name, mime="text/csv")


st.title("📈 YouTube Trend Analyzer")
st.caption("YouTube Data API v3を使った、日本向けゲーム動画のトレンド分析MVP")

with st.sidebar:
    st.header("設定")
    secret_key = ""
    try:
        secret_key = st.secrets.get("YOUTUBE_API_KEY", "")
    except Exception:
        pass
    api_key = st.text_input("YouTube Data API Key", value=os.getenv("YOUTUBE_API_KEY", secret_key), type="password")
    region = st.text_input("地域コード", value="JP", max_chars=2).upper()
    st.caption("APIキーはこのアプリ内でのみ使用します。公開リポジトリに書き込まないでください。")

if not api_key:
    st.info("左側に YouTube Data API Key を入力すると分析を開始できます。")

trend_tab, search_tab, games_tab, opportunity_tab, long_tab = st.tabs([
    "🔥 人気動画", "🔎 キーワード分析", "🎮 ゲーム比較", "🎯 狙い目ランキング", "📅 長期トレンド"
])

with trend_tab:
    st.subheader("現在の人気ゲーム動画")
    count = st.slider("取得件数", 10, 50, 30, 10, key="popular_count")
    if st.button("人気動画を取得", disabled=not api_key):
        try:
            with st.spinner("取得中..."):
                df = most_popular(count, api_key, region)
            st.session_state["popular_df"] = df
        except Exception as e:
            st.error(str(e))
    df = st.session_state.get("popular_df", pd.DataFrame())
    if not df.empty:
        cols = ["trend_score", "title", "channel", "format", "views", "views_per_hour", "like_rate_pct", "comments", "subscribers", "url"]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)
        st.bar_chart(df.head(15).set_index("title")["trend_score"])
        dataframe_download(df, "youtube_popular_games.csv")

with search_tab:
    st.subheader("キーワード別トレンド動画")
    c1, c2, c3 = st.columns(3)
    query = c1.text_input("検索キーワード", value="マイクラ")
    days = c2.selectbox("対象期間", [1, 3, 7, 14, 30, 90], index=2)
    max_r = c3.selectbox("取得件数", [10, 25, 50, 100], index=2)
    if st.button("分析する", disabled=not api_key, key="search_btn"):
        try:
            with st.spinner("検索・分析中..."):
                df = score_df(search_videos(query, days, max_r, api_key, region))
            st.session_state["search_df"] = df
        except Exception as e:
            st.error(str(e))
    df = st.session_state.get("search_df", pd.DataFrame())
    if not df.empty:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("取得動画", len(df))
        m2.metric("総再生数", f"{int(df['views'].sum()):,}")
        m3.metric("中央値再生数", f"{int(df['views'].median()):,}")
        m4.metric("チャンネル数", int(df["channel_id"].nunique()))
        st.dataframe(df[["trend_score", "title", "channel", "format", "views", "views_per_hour", "like_rate_pct", "comment_rate_pct", "views_vs_subscribers", "url"]], use_container_width=True, hide_index=True)
        dataframe_download(df, f"youtube_trend_{query}.csv")

with games_tab:
    st.subheader("ゲームタイトル比較")
    default_games = "Minecraft\nFortnite\nVALORANT\nApex Legends\nRoblox"
    games_text = st.text_area("1行に1タイトル", value=default_games, height=150)
    c1, c2 = st.columns(2)
    game_days = c1.selectbox("比較期間（日）", [1, 3, 7, 14, 30], index=2)
    sample = c2.selectbox("1タイトルあたり取得数", [10, 25, 50], index=1)
    if st.button("ゲームを比較", disabled=not api_key):
        games = [x.strip() for x in games_text.splitlines() if x.strip()]
        if len(games) > 10:
            st.warning("API使用量を抑えるため、一度に10タイトルまでを推奨します。")
        results = []
        try:
            prog = st.progress(0)
            for idx, game in enumerate(games):
                df = search_videos(game, game_days, sample, api_key, region)
                if not df.empty:
                    results.append({
                        "game": game,
                        "videos_sampled": len(df),
                        "sample_views": int(df["views"].sum()),
                        "median_views": float(df["views"].median()),
                        "median_views_per_hour": float(df["views_per_hour"].median()),
                        "active_channels": int(df["channel_id"].nunique()),
                        "median_like_rate_pct": float(df["like_rate_pct"].median()),
                    })
                prog.progress((idx + 1) / max(len(games), 1))
            rdf = pd.DataFrame(results)
            if not rdf.empty:
                # Cross-game score favors velocity, sustained channel breadth, and engagement.
                rdf["game_trend_score"] = (
                    55 * rdf["median_views_per_hour"].rank(pct=True) +
                    25 * rdf["active_channels"].rank(pct=True) +
                    20 * rdf["median_like_rate_pct"].rank(pct=True)
                ).round(1)
                rdf = rdf.sort_values("game_trend_score", ascending=False)
            st.session_state["games_df"] = rdf
        except Exception as e:
            st.error(str(e))
    rdf = st.session_state.get("games_df", pd.DataFrame())
    if not rdf.empty:
        st.dataframe(rdf, use_container_width=True, hide_index=True)
        st.bar_chart(rdf.set_index("game")["game_trend_score"])
        dataframe_download(rdf, "youtube_game_ranking.csv")

with opportunity_tab:
    st.subheader("狙い目ゲームランキング")
    st.caption("需要の強さ・競合の少なさ・最近の勢い・エンゲージメントを組み合わせた独自指標です。")
    default_op = "Minecraft\nFortnite\nVALORANT\nApex Legends\nRoblox\nゼンレスゾーンゼロ"
    op_text = st.text_area("比較したいゲーム（1行1タイトル）", value=default_op, height=170, key="op_games")
    oc1, oc2 = st.columns(2)
    op_days = oc1.selectbox("対象期間（日）", [3, 7, 14, 30], index=1, key="op_days")
    op_sample = oc2.selectbox("1タイトルあたり取得数", [10, 25, 50], index=1, key="op_sample")
    if st.button("狙い目を分析", disabled=not api_key, key="op_btn"):
        games = [x.strip() for x in op_text.splitlines() if x.strip()][:10]
        rows = []
        try:
            prog = st.progress(0)
            for idx, game in enumerate(games):
                df = search_videos(game, op_days, op_sample, api_key, region)
                if not df.empty:
                    demand = float(df["views_per_hour"].median())
                    competition = max(int(df["channel_id"].nunique()), 1)
                    engagement = float((df["like_rate_pct"] + 5 * df["comment_rate_pct"]).median())
                    breakout = float(df["views_vs_subscribers"].replace([math.inf, -math.inf], 0).median())
                    rows.append({"game": game, "videos_sampled": len(df), "median_views": float(df["views"].median()), "demand_velocity": demand, "active_channels": competition, "engagement": engagement, "breakout_ratio": breakout})
                prog.progress((idx + 1) / max(len(games), 1))
            odf = pd.DataFrame(rows)
            if not odf.empty:
                # Higher demand/breakout/engagement is good; fewer active sampled channels is treated as lower competition.
                odf["opportunity_score"] = (
                    40 * odf["demand_velocity"].rank(pct=True) +
                    25 * odf["breakout_ratio"].rank(pct=True) +
                    15 * odf["engagement"].rank(pct=True) +
                    20 * (1 - odf["active_channels"].rank(pct=True) + 1/len(odf))
                ).clip(0, 100).round(1)
                odf = odf.sort_values("opportunity_score", ascending=False)
            st.session_state["opportunity_df"] = odf
        except Exception as e:
            st.error(str(e))
    odf = st.session_state.get("opportunity_df", pd.DataFrame())
    if not odf.empty:
        st.dataframe(odf[["opportunity_score", "game", "median_views", "demand_velocity", "active_channels", "engagement", "breakout_ratio"]], use_container_width=True, hide_index=True)
        st.bar_chart(odf.set_index("game")["opportunity_score"])
        st.info("狙い目スコアはYouTube公式指標ではありません。検索結果サンプル内での相対評価です。競合数もYouTube全体の投稿者総数ではなく、取得サンプル内のアクティブチャンネル数を使用しています。")
        dataframe_download(odf, "youtube_opportunity_ranking.csv")

with long_tab:
    st.subheader("長期トレンド（月別サンプリング）")
    st.warning("長期分析は search.list を月ごとに使用します。APIの検索クォータを多く消費するため、少数タイトルで試してください。")
    c1, c2, c3 = st.columns(3)
    long_q = c1.text_input("ゲームタイトル", value="Minecraft", key="long_q")
    months = c2.selectbox("期間", [3, 6, 12, 24], index=1, format_func=lambda x: f"{x}か月")
    per_month = c3.selectbox("各月の上位取得数", [5, 10, 25, 50], index=1)
    st.caption(f"この実行では概ね {months} 回の検索API呼び出しを行います。")
    if st.button("長期分析を実行", disabled=not api_key):
        try:
            with st.spinner("月ごとのデータを取得中..."):
                ldf = monthly_long_term(long_q, months, per_month, api_key, region)
            st.session_state["long_df"] = ldf
        except Exception as e:
            st.error(str(e))
    ldf = st.session_state.get("long_df", pd.DataFrame())
    if not ldf.empty:
        st.dataframe(ldf, use_container_width=True, hide_index=True)
        st.line_chart(ldf.set_index("month")[["median_views", "sample_views"]])
        st.line_chart(ldf.set_index("month")[["active_channels"]])
        dataframe_download(ldf, f"youtube_longterm_{long_q}.csv")

st.divider()
st.caption("Trend ScoreはYouTube公式指標ではなく、このツール独自の比較スコアです。公開データのサンプルから相対評価します。")
