import os
from datetime import datetime, timedelta, timezone
import pandas as pd
import streamlit as st
from analyzer import add_channel_stats, filter_videos, most_popular, score_df, search_period, search_videos

st.set_page_config(page_title="YouTube Trend Analyzer", page_icon="📈", layout="wide")
st.title("📈 YouTube Trend Analyzer")
st.caption("ゲーム動画のトレンドと、自分に近い規模のチャンネルで伸びている動画を探す")
PRESETS = {"指定なし": (None, None), "0〜1,000人未満": (0, 999), "1,000〜1万人未満": (1000, 9999), "1万〜10万人未満": (10000, 99999), "10万〜100万人未満": (100000, 999999), "100万人以上": (1000000, None), "任意入力": (None, None)}


def bounds(label, key, enabled=False):
    if not enabled and not st.checkbox(f"{label}で絞り込む", key=key + "_on"):
        return None, None
    low = st.number_input(f"{label}：最小", min_value=0, value=0, step=1, key=key + "_min")
    unlimited = st.checkbox(f"{label}：上限なし", value=True, key=key + "_unlimited")
    high = None if unlimited else st.number_input(f"{label}：最大", min_value=0, value=10000, step=1, key=key + "_max")
    if high is not None and low > high:
        st.error(f"{label}の最小値は最大値以下にしてください。")
        st.stop()
    return low, high


with st.sidebar:
    st.header("接続設定")
    try:
        secret_key = st.secrets.get("YOUTUBE_API_KEY", "")
    except Exception:
        secret_key = ""
    # Never put the deployed owner's secret in a browser widget.
    configured_key = os.getenv("YOUTUBE_API_KEY", "") or secret_key
    api_key = configured_key or st.text_input("YouTube Data API Key", type="password")
    if configured_key:
        st.caption("保存済みのAPIキーを使用中")
    region = st.text_input("地域コード", value="JP", max_chars=2).upper()
    st.header("全タブ共通の絞り込み")
    preset = st.selectbox("登録者数の規模", list(PRESETS))
    sub_bounds = bounds("登録者数", "subs", True) if preset == "任意入力" else PRESETS[preset]
    unknown = st.selectbox("登録者数が非公開・取得不可の動画", ["条件なしの場合のみ含める", "常に除外", "非公開・取得不可のみ"])
    filters = {"subscribers": sub_bounds, "unknown": {"条件なしの場合のみ含める": "include", "常に除外": "exclude", "非公開・取得不可のみ": "only"}[unknown], "channel_total_views": bounds("チャンネル総再生数", "total"), "channel_video_count": bounds("チャンネル動画本数", "count"), "format": st.selectbox("動画の形式", ["すべて", "Shorts候補", "長尺"])}
    if st.checkbox("登録者比の下限を指定"):
        filters["min_ratio"] = st.number_input("登録者比：最小（倍）", min_value=0.0, value=1.0, step=0.5)
    filters["min_views"] = st.number_input("動画再生数：最小", min_value=0, value=0, step=100)
    breakout_mode = st.checkbox("小規模で伸びている動画を探す")
    if breakout_mode:
        filters["small_max"] = st.number_input("小規模とする登録者数の上限", min_value=1, value=10000, step=1000)
        ratio = st.number_input("小規模動画の登録者比：最小（倍）", min_value=0.1, value=3.0, step=0.5)
        filters["min_ratio"] = max(filters.get("min_ratio", 0), ratio)
    sort_label = st.selectbox("動画ランキング順", ["トレンドスコア", "登録者比", "再生速度", "動画再生数"], index=1 if breakout_mode else 0)
    sort_col = {"トレンドスコア": "trend_score", "登録者比": "views_vs_subscribers", "再生速度": "views_per_hour", "動画再生数": "views"}[sort_label]
    st.caption("条件変更は取得済みデータに即時反映され、再検索しません。任意入力の上下限は境界値を含みます。")
    if unknown == "非公開・取得不可のみ" and (sub_bounds != (None, None) or "min_ratio" in filters):
        st.warning("登録者数不明の動画は、登録者数・登録者比の数値条件を満たせないため表示されません。")

if not api_key:
    st.info("左側にAPIキーを入力すると分析できます。APIキーをコードやGitHubへ保存しないでください。")
st.info("取得した上位動画サンプルをチャンネル規模で絞り込みます。該当0件でもYouTube全体に存在しないとは限りません。取得件数やキーワードを調整してください。")
st.caption("登録者比＝動画再生数÷現在の登録者数。非公開・取得不可・0人の場合は算出しません。登録者数はAPIの丸め値です。地域コードは視聴地域で、日本語動画だけに限定する指定ではありません。")
LABELS = {"trend_score": "トレンドスコア", "title": "動画タイトル", "channel": "チャンネル", "format": "形式", "views": "動画再生数", "views_per_hour": "再生数/時間", "like_rate_pct": "高評価率（%）", "comment_rate_pct": "コメント率（%）", "subscribers": "登録者数", "subscriber_status": "登録者数の公開状態", "channel_total_views": "チャンネル総再生数", "channel_video_count": "チャンネル動画本数", "views_vs_subscribers": "登録者比（倍）", "url": "動画URL"}
SUMMARY_LABELS = {"game": "ゲーム", "period": "投稿期間", "videos_fetched": "取得動画数", "videos_sampled": "条件一致動画数", "sample_views": "サンプル総再生数", "median_views": "再生数の中央値", "median_views_per_hour": "再生速度の中央値", "active_channels": "チャンネル数", "median_like_rate_pct": "高評価率の中央値（%）", "breakout_ratio": "登録者比の中央値（倍）", "median_subscribers": "登録者数の中央値", "engagement": "反応率の指標", "game_trend_score": "ゲームトレンドスコア", "opportunity_score": "狙い目スコア"}


def download(df, name):
    st.download_button("CSVをダウンロード", df.to_csv(index=False).encode("utf-8-sig"), file_name=name, mime="text/csv", key="csv_" + name)


def show_videos(raw, name):
    df = score_df(filter_videos(raw, filters))
    st.caption(f"取得 {len(raw):,}件 → 条件一致 {len(df):,}件")
    if df.empty:
        st.info("条件に一致する動画がありません。条件を緩めるか、取得件数を増やして再取得してください。")
        return
    df = df.sort_values([sort_col, "views"], ascending=False, na_position="last")
    a, b, c, d = st.columns(4)
    a.metric("条件一致動画", len(df))
    b.metric("総再生数", f"{int(df.views.sum()):,}")
    c.metric("中央値再生数", f"{int(df.views.median()):,}")
    d.metric("チャンネル数", df.channel_id.nunique())
    st.dataframe(df[list(LABELS)], column_config={**LABELS, "url": st.column_config.LinkColumn("動画URL"), "views_vs_subscribers": st.column_config.NumberColumn("登録者比（倍）", format="%.2f")}, use_container_width=True, hide_index=True)
    st.bar_chart(df.head(15).set_index("title")[sort_col])
    download(df, name)


def collect_games(games, days, sample):
    parts = []
    progress = st.progress(0)
    for idx, game in enumerate(games):
        df = search_videos(game, days, sample, api_key, region, enrich=False)
        df["game"] = game
        parts.append(df)
        progress.progress((idx + 1) / len(games))
    return add_channel_stats(pd.concat(parts, ignore_index=True), api_key)


def summarize(df):
    def median(col):
        return df[col].median() if not df.empty and df[col].notna().any() else float("nan")
    return {"videos_sampled": len(df), "sample_views": int(df.views.sum()) if not df.empty else 0,
            "median_views": median("views"), "median_views_per_hour": median("views_per_hour"),
            "active_channels": df.channel_id.nunique() if not df.empty else 0,
            "median_like_rate_pct": median("like_rate_pct"), "breakout_ratio": median("views_vs_subscribers"),
            "median_subscribers": median("subscribers"),
            "engagement": (df.like_rate_pct + 5 * df.comment_rate_pct).median() if not df.empty else float("nan")}


tabs = st.tabs(["🔥 人気動画", "🔎 キーワード分析", "🎮 ゲーム比較", "🎯 狙い目ランキング", "📅 長期トレンド"])
with tabs[0]:
    st.subheader("現在の人気ゲーム動画")
    count = st.slider("取得件数", 10, 50, 30, 10, key="popular_count")
    if st.button("人気動画を取得", disabled=not api_key):
        try:
            with st.spinner("取得中..."):
                st.session_state.popular_df = most_popular(count, api_key, region)
                st.session_state.popular_context = f"取得条件：地域 {region} / 最大 {count}件"
        except Exception as exc:
            st.error(str(exc))
    if "popular_df" in st.session_state:
        st.caption(st.session_state.popular_context)
        show_videos(st.session_state.popular_df, "youtube_popular_games.csv")

with tabs[1]:
    st.subheader("キーワード別トレンド動画")
    a, b, c = st.columns(3)
    query = a.text_input("検索キーワード", value="マイクラ")
    days = b.selectbox("対象期間", [1, 3, 7, 14, 30, 90], index=2)
    max_r = c.selectbox("取得件数（絞り込み前）", [10, 25, 50, 100, 200, 500], index=2)
    if st.button("分析する", disabled=not api_key or not query.strip(), key="search_btn"):
        try:
            with st.spinner("検索・分析中..."):
                st.session_state.search_df = search_videos(query.strip(), days, max_r, api_key, region)
                st.session_state.search_context = f"取得条件：{query.strip()} / 直近{days}日 / 地域 {region} / 最大 {max_r}件"
        except Exception as exc:
            st.error(str(exc))
    if "search_df" in st.session_state:
        st.caption(st.session_state.search_context)
        show_videos(st.session_state.search_df, "youtube_keyword_trend.csv")

for tab, mode in [(tabs[2], "games"), (tabs[3], "opportunity")]:
    with tab:
        st.subheader("ゲームタイトル比較" if mode == "games" else "狙い目ゲームランキング")
        text = st.text_area("1行に1タイトル（最大10件）", "Minecraft\nFortnite\nVALORANT\nApex Legends\nRoblox", key=mode + "_text")
        a, b = st.columns(2)
        days = a.selectbox("比較期間（日）", [1, 3, 7, 14, 30], index=2, key=mode + "_days")
        sample = b.selectbox("1タイトルあたり取得数", [10, 25, 50, 100, 200], index=1, key=mode + "_sample")
        games = list(dict.fromkeys(x.strip() for x in text.splitlines() if x.strip()))
        if len(games) > 10:
            st.warning("一度に分析できるのは10タイトルまでです。")
        if st.button("ゲームを比較" if mode == "games" else "狙い目を分析", key=mode + "_button", disabled=not api_key or not 1 <= len(games) <= 10):
            try:
                with st.spinner("動画とチャンネル情報を取得中..."):
                    raw = collect_games(games, days, sample)
                st.session_state[mode + "_raw"] = raw
                st.session_state[mode + "_names"] = games
                st.session_state[mode + "_context"] = f"取得条件：直近{days}日 / 地域 {region} / 各最大 {sample}件"
            except Exception as exc:
                st.error(str(exc))
        if mode + "_raw" in st.session_state:
            raw = st.session_state[mode + "_raw"]
            st.caption(st.session_state[mode + "_context"])
            filtered = filter_videos(raw, filters)
            rows = []
            for game in st.session_state[mode + "_names"]:
                df = filtered[filtered.game == game]
                rows.append({"game": game, "videos_fetched": len(raw[raw.game == game]), **summarize(df)})
            rdf = pd.DataFrame(rows)
            valid = rdf.videos_sampled > 0
            ranked = rdf.loc[valid]
            score_name = "game_trend_score" if mode == "games" else "opportunity_score"
            rdf[score_name] = float("nan")
            if not ranked.empty:
                if mode == "games":
                    scores = 55 * ranked.median_views_per_hour.rank(pct=True) + 25 * ranked.active_channels.rank(pct=True) + 20 * ranked.median_like_rate_pct.rank(pct=True)
                else:
                    scores = 40 * ranked.median_views_per_hour.rank(pct=True) + 25 * ranked.breakout_ratio.rank(pct=True).fillna(0) + 15 * ranked.engagement.rank(pct=True) + 20 * (1 - ranked.active_channels.rank(pct=True) + 1 / len(ranked))
                rdf.loc[valid, score_name] = scores.clip(0, 100).round(1)
            else:
                st.info("条件に一致する動画がありません。条件を緩めてください。")
            rdf = rdf.sort_values(score_name, ascending=False, na_position="last")
            st.dataframe(rdf, column_config=SUMMARY_LABELS, use_container_width=True, hide_index=True)
            if valid.any():
                st.bar_chart(rdf.set_index("game")[score_name].dropna())
            st.caption("スコアは条件一致サンプル内の相対評価です。0件は順位対象外。登録者比不明はその加点を0とします。競合数はサンプル内のチャンネル数です。")
            download(rdf, f"youtube_{mode}_ranking.csv")
            with st.expander("比較に使った動画とチャンネル規模を見る"):
                show_videos(raw, f"youtube_{mode}_videos.csv")

with tabs[4]:
    st.subheader("長期トレンド（30日区間ごとのサンプリング）")
    st.warning("各動画の現在の累計再生数と現在のチャンネル規模を集計します。当時の月間再生数・登録者数ではありません。")
    a, b, c = st.columns(3)
    query = a.text_input("ゲームタイトル", "Minecraft", key="long_query")
    months = b.selectbox("期間", [3, 6, 12, 24], index=1, format_func=lambda x: f"{x}か月")
    sample = c.selectbox("各区間の上位取得数", [5, 10, 25, 50], index=1)
    st.caption(f"検索API呼び出しは最大 {months} 回。チャンネル情報は区間をまたいでまとめて取得します。")
    if st.button("長期分析を実行", disabled=not api_key or not query.strip()):
        try:
            with st.spinner("区間ごとに取得中..."):
                now = datetime.now(timezone.utc)
                parts, periods = [], []
                for offset in range(months - 1, -1, -1):
                    end = now - timedelta(days=30 * offset)
                    start = end - timedelta(days=30)
                    period = start.strftime("%Y-%m-%d") + "〜" + end.strftime("%Y-%m-%d")
                    df = search_period(query.strip(), start, end, sample, api_key, region)
                    df["period"] = period
                    periods.append(period)
                    parts.append(df)
                raw = add_channel_stats(pd.concat(parts, ignore_index=True), api_key)
            st.session_state.long_raw = raw
            st.session_state.long_periods = periods
            st.session_state.long_context = f"取得条件：{query.strip()} / 地域 {region} / 各区間最大 {sample}件"
        except Exception as exc:
            st.error(str(exc))
    if "long_raw" in st.session_state:
        st.caption(st.session_state.long_context)
        raw = st.session_state.long_raw
        filtered = filter_videos(raw, filters)
        rows = []
        for period in st.session_state.long_periods:
            df = filtered[filtered.period == period]
            rows.append({"period": period, "videos_fetched": len(raw[raw.period == period]), **summarize(df)})
        ldf = pd.DataFrame(rows)
        st.dataframe(ldf, column_config=SUMMARY_LABELS, use_container_width=True, hide_index=True)
        st.line_chart(ldf.set_index("period")[["median_views", "sample_views"]])
        st.line_chart(ldf.set_index("period")[["active_channels"]])
        download(ldf, "youtube_longterm.csv")
        with st.expander("長期分析に使った動画を見る"):
            show_videos(raw, "youtube_longterm_videos.csv")

st.divider()
st.caption("データ応答は15分間キャッシュします。Shorts候補は3分以下という長さだけの推定です。独自スコアはYouTube公式指標ではありません。")
