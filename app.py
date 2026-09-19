import os
from datetime import datetime, timedelta, timezone
import pandas as pd
import streamlit as st
from analyzer import score_df
from matching import collect_matching

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
    languages = {"すべて": "", "日本語": "ja", "英語": "en", "韓国語": "ko", "中国語（簡体字）": "zh-Hans", "中国語（繁体字）": "zh-Hant", "スペイン語": "es", "ポルトガル語": "pt", "フランス語": "fr", "ドイツ語": "de", "ヒンディー語": "hi", "インドネシア語": "id", "タイ語": "th", "ベトナム語": "vi", "ロシア語": "ru", "アラビア語": "ar"}
    language = languages[st.selectbox("動画の言語", list(languages))]
    include_unknown_language = st.checkbox("言語が未登録の動画も含める", value=False, disabled=not language)
    st.caption("音声の登録言語を優先し、なければタイトル・説明の登録言語で判定します。音声の自動解析ではありません。")
    max_pages = st.number_input("1回の分析の探索上限（ページ）", min_value=1, max_value=100, value=20, step=1)
    st.caption("1ページは最大50候補。比較・長期分析では全対象で上限を共有します。厳しい条件ほど検索回数が増えます。")
    search_order = {"再生数が多い順": "viewCount", "関連性が高い順": "relevance", "新しい順": "date"}[st.selectbox("候補を探す順番", ["再生数が多い順", "関連性が高い順", "新しい順"])]
    st.header("全タブ共通の取得条件")
    preset = st.selectbox("登録者数の規模", list(PRESETS))
    sub_bounds = bounds("登録者数", "subs", True) if preset == "任意入力" else PRESETS[preset]
    unknown = st.selectbox("登録者数が非公開・取得不可の動画", ["条件なしの場合のみ含める", "常に除外", "非公開・取得不可のみ"])
    filters = {"subscribers": sub_bounds, "unknown": {"条件なしの場合のみ含める": "include", "常に除外": "exclude", "非公開・取得不可のみ": "only"}[unknown], "channel_total_views": bounds("チャンネル総再生数", "total"), "channel_video_count": bounds("チャンネル動画本数", "count"), "format": st.selectbox("動画の形式", ["すべて", "Shorts候補", "長尺"])}
    filters.update(language=language, include_unknown_language=include_unknown_language)
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
    st.caption("条件を設定してから実行してください。条件変更後は再実行が必要です。任意入力の上下限は境界値を含みます。")
    if unknown == "非公開・取得不可のみ" and (sub_bounds != (None, None) or "min_ratio" in filters):
        st.warning("登録者数不明の動画は、登録者数・登録者比の数値条件を満たせないため表示されません。")

if not api_key:
    st.info("左側にAPIキーを入力すると分析できます。APIキーをコードやGitHubへ保存しないでください。")
st.info("条件一致の動画が指定件数に達するまで追加検索します。APIの検索結果終了・探索上限・エラーで届かない場合は、取得できた件数と停止理由を表示します。表示件数は形式の合計です。特定の形式を指定した場合は、その形式だけで指定件数を探します。")
st.caption("登録者比＝動画再生数÷現在の登録者数。非公開・取得不可・0人の場合は算出しません。登録者数はAPIの丸め値です。地域コードは視聴地域で、日本語動画だけに限定する指定ではありません。")
LABELS = {"duration_sec": "長さ（秒）", "language": "登録言語", "language_source": "言語の判定元", "trend_score": "トレンドスコア", "title": "動画タイトル", "channel": "チャンネル", "format": "形式", "views": "動画再生数", "views_per_hour": "再生数/時間", "like_rate_pct": "高評価率（%）", "comment_rate_pct": "コメント率（%）", "subscribers": "登録者数", "subscriber_status": "登録者数の公開状態", "channel_total_views": "チャンネル総再生数", "channel_video_count": "チャンネル動画本数", "views_vs_subscribers": "登録者比（倍）", "url": "動画URL"}
SUMMARY_LABELS = {"game": "ゲーム", "period": "投稿期間", "videos_fetched": "取得動画数", "videos_sampled": "条件一致動画数", "sample_views": "サンプル総再生数", "median_views": "再生数の中央値", "median_views_per_hour": "再生速度の中央値", "active_channels": "チャンネル数", "median_like_rate_pct": "高評価率の中央値（%）", "breakout_ratio": "登録者比の中央値（倍）", "median_subscribers": "登録者数の中央値", "engagement": "反応率の指標", "game_trend_score": "ゲームトレンドスコア", "opportunity_score": "狙い目スコア"}


def download(df, name):
    st.download_button("CSVをダウンロード", df.to_csv(index=False).encode("utf-8-sig"), file_name=name, mime="text/csv", key="csv_" + name)


def show_videos(raw, name):
    if raw.empty:
        st.info("条件に一致する動画を取得できませんでした。条件や探索上限を調整してください。")
        return
    df = score_df(raw).sort_values([sort_col, "views"], ascending=False, na_position="last")
    a, b, c, d = st.columns(4)
    a.metric("表示動画数", len(df))
    b.metric("総再生数", f"{int(df.views.sum()):,}")
    c.metric("中央値再生数", f"{int(df.views.median()):,}")
    d.metric("チャンネル数", df.channel_id.nunique())
    kinds = ["Shorts候補", "長尺"]
    if df["format"].eq("形式不明").any():
        kinds.append("形式不明")
    panels = st.tabs([f"{kind}（{int(df['format'].eq(kind).sum())}件）" for kind in kinds])
    for panel, kind in zip(panels, kinds):
        with panel:
            subset = df[df["format"].eq(kind)]
            if subset.empty:
                st.info("この形式の動画はありません。")
                continue
            columns = ["group"] + list(LABELS)
            st.dataframe(subset[columns], column_config={**LABELS, "group": "検索対象", "url": st.column_config.LinkColumn("動画URL"), "views_vs_subscribers": st.column_config.NumberColumn("登録者比（倍）", format="%.2f")}, use_container_width=True, hide_index=True)
            st.bar_chart(subset.head(15).set_index("title")[sort_col])
            suffix = {"Shorts候補": "shorts_candidates", "長尺": "long", "形式不明": "unknown"}[kind]
            download(subset, name.replace(".csv", "_" + suffix + ".csv"))
    download(df, name)


def summarize(df):
    def median(col):
        return df[col].median() if not df.empty and df[col].notna().any() else float("nan")
    return {"videos_sampled": len(df), "sample_views": int(df.views.sum()) if not df.empty else 0,
            "median_views": median("views"), "median_views_per_hour": median("views_per_hour"),
            "active_channels": df.channel_id.nunique() if not df.empty else 0,
            "median_like_rate_pct": median("like_rate_pct"), "breakout_ratio": median("views_vs_subscribers"),
            "median_subscribers": median("subscribers"),
            "engagement": (df.like_rate_pct + 5 * df.comment_rate_pct).median() if not df.empty else float("nan")}


def run_analysis(key, label, jobs, target, description, disabled=False):
    signature = {"jobs": jobs, "target": target, "filters": filters, "region": region, "max_pages": max_pages}
    if st.button(label, key=key + "_button", disabled=not api_key or disabled):
        try:
            with st.spinner("条件に一致する動画を探しています..."):
                status = st.empty()
                def progress(pages, matched, total):
                    status.caption(f"候補を探索中：{pages}/{max_pages}ページ、条件一致 {matched}/{total}件")
                result = collect_matching(jobs, target, api_key, filters, region, max_pages, progress)
                status.empty()
            st.session_state[key + "_result"] = {"signature": signature, "result": result}
        except Exception as exc:
            st.error(str(exc))
    saved = st.session_state.get(key + "_result")
    if not saved:
        return None
    if saved["signature"] != signature:
        st.warning("取得条件が変更されています。実行ボタンを押すと、新しい条件で指定件数まで探します。")
        return None
    result = saved["result"]
    st.caption(description)
    st.dataframe(result["report"][["group", "requested", "matched", "candidates_checked", "pages", "status"]],
                 column_config={"group": "対象", "requested": "指定件数", "matched": "条件一致・表示件数", "candidates_checked": "確認した候補数", "pages": "探索ページ数", "status": "取得結果"}, use_container_width=True, hide_index=True)
    if result["error"]:
        st.warning(result["error"])
    if (result["report"].matched < result["report"].requested).any():
        st.warning("指定件数に届いていない対象があります。上表に停止理由を表示しています。条件を緩めるか、探索上限を増やして再実行してください。")
    return result


# A stable 15-minute boundary makes repeated searches reuse API response caches.
now = pd.Timestamp.now(tz="UTC").floor("15min").to_pydatetime()
tabs = st.tabs(["🔥 人気動画", "🔎 キーワード分析", "🎮 ゲーム比較", "🎯 狙い目ランキング", "📅 長期トレンド"])
with tabs[0]:
    st.subheader("現在の人気ゲーム動画")
    count = st.number_input("表示したい件数", min_value=1, max_value=500, value=30, key="popular_count")
    st.caption("人気動画の一覧をページ送りして条件一致を探します。元の人気一覧が尽きた場合は、それ以上取得できません。")
    result = run_analysis("popular", "人気動画を取得", [{"label": "人気動画", "kind": "popular"}], count, f"地域 {region}・条件一致 {count}件を目標")
    if result is not None:
        show_videos(result["videos"], "youtube_popular_games.csv")

with tabs[1]:
    st.subheader("キーワード別トレンド動画")
    a, b, c = st.columns(3)
    query = a.text_input("検索キーワード", value="マイクラ")
    days = b.selectbox("対象期間", [1, 3, 7, 14, 30, 90], index=2)
    target = c.number_input("表示したい件数", min_value=1, max_value=500, value=50, key="search_count")
    jobs = [{"label": query.strip(), "query": query.strip(), "start": now - timedelta(days=days), "order": search_order}]
    result = run_analysis("search", "分析する", jobs, target, f"{query.strip()} / 直近{days}日 / 地域 {region} / 条件一致 {target}件を目標", not query.strip())
    if result is not None:
        show_videos(result["videos"], "youtube_keyword_trend.csv")

for tab, mode in [(tabs[2], "games"), (tabs[3], "opportunity")]:
    with tab:
        st.subheader("ゲームタイトル比較" if mode == "games" else "狙い目ゲームランキング")
        text = st.text_area("1行に1タイトル（最大10件）", "Minecraft\nFortnite\nVALORANT\nApex Legends\nRoblox", key=mode + "_text")
        a, b = st.columns(2)
        days = a.selectbox("比較期間（日）", [1, 3, 7, 14, 30], index=2, key=mode + "_days")
        target = b.number_input("1タイトルあたりの条件一致件数", min_value=1, max_value=500, value=25, key=mode + "_sample")
        games = list(dict.fromkeys(x.strip() for x in text.splitlines() if x.strip()))
        if len(games) > 10:
            st.warning("一度に分析できるのは10タイトルまでです。")
        jobs = [{"label": game, "query": game, "start": now - timedelta(days=days), "order": search_order} for game in games]
        result = run_analysis(mode, "ゲームを比較" if mode == "games" else "狙い目を分析", jobs, target, f"直近{days}日 / 地域 {region} / 各{target}件を目標", not 1 <= len(games) <= 10)
        if result is not None:
            raw = result["videos"]
            rows = [{"game": game, **summarize(raw[raw.group == game])} for game in games]
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
            rdf = rdf.sort_values(score_name, ascending=False, na_position="last")
            st.dataframe(rdf, column_config=SUMMARY_LABELS, use_container_width=True, hide_index=True)
            if valid.any():
                st.bar_chart(rdf.set_index("game")[score_name].dropna())
            st.caption("スコアは取得した一致動画内の相対評価です。件数不足の対象は上表を確認してください。条件一致0件は順位対象外です。登録者比不明はその加点を0とします。")
            download(rdf, f"youtube_{mode}_ranking.csv")
            with st.expander("比較に使った動画を形式別に見る"):
                show_videos(raw, f"youtube_{mode}_videos.csv")

with tabs[4]:
    st.subheader("長期トレンド（30日区間ごとのサンプリング）")
    st.warning("各動画の現在の累計再生数と現在のチャンネル規模を集計します。当時の月間再生数・登録者数ではありません。")
    a, b, c = st.columns(3)
    query = a.text_input("ゲームタイトル", "Minecraft", key="long_query")
    months = b.selectbox("期間", [3, 6, 12, 24], index=1, format_func=lambda x: f"{x}か月")
    target = c.number_input("各区間の条件一致件数", min_value=1, max_value=500, value=10, key="long_count")
    jobs = []
    for offset in range(months - 1, -1, -1):
        end = now - timedelta(days=30 * offset)
        start = end - timedelta(days=30)
        label = start.strftime("%Y-%m-%d") + "〜" + end.strftime("%Y-%m-%d")
        jobs.append({"label": label, "query": query.strip(), "start": start, "end": end, "order": search_order})
    st.caption(f"全{months}区間で探索上限を共有します。各区間を1ページずつ交互に検索します。")
    result = run_analysis("long", "長期分析を実行", jobs, target, f"{query.strip()} / 地域 {region} / 各区間{target}件を目標", not query.strip())
    if result is not None:
        raw = result["videos"]
        ldf = pd.DataFrame([{"period": job["label"], **summarize(raw[raw.group == job["label"]])} for job in jobs])
        st.dataframe(ldf, column_config=SUMMARY_LABELS, use_container_width=True, hide_index=True)
        st.line_chart(ldf.set_index("period")[["median_views", "sample_views"]])
        st.line_chart(ldf.set_index("period")[["active_channels"]])
        download(ldf, "youtube_longterm.csv")
        with st.expander("長期分析に使った動画を形式別に見る"):
            show_videos(raw, "youtube_longterm_videos.csv")

st.divider()
st.caption("Shorts候補＝3分以下、長尺＝3分超という長さによる分類です。3分以下の通常動画がShorts候補に含まれます。厳密なShorts判定ではありません。独自スコアはYouTube公式指標ではありません。")
