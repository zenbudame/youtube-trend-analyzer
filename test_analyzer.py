"""Offline regression tests: python -m unittest -v"""
import os
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import pandas as pd
import requests
from streamlit.testing.v1 import AppTest
import analyzer as a


def video(vid, channel="c1"):
    return {"id": vid, "snippet": {"channelId": channel, "channelTitle": channel, "title": "動画 " + vid, "publishedAt": "2026-01-01T00:00:00Z"}, "statistics": {"viewCount": "10000", "likeCount": "100", "commentCount": "5"}, "contentDetails": {"duration": "PT5M"}}


def fake_api(path, params, key):
    if path == "search":
        return {"items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}]}
    if path == "videos":
        return {"items": [video(v, "c1" if v == "v1" else "c2") for v in params.get("id", "v1,v2").split(",")]}
    return {"items": [{"id": cid, "statistics": {"subscriberCount": "1000" if cid == "c1" else "100000", "viewCount": "2000000", "videoCount": "100", "hiddenSubscriberCount": False}} for cid in params["id"].split(",")]}


class CoreTests(unittest.TestCase):
    def setUp(self):
        a.search_videos.clear()
        a.api_get.clear()

    def sample(self):
        with patch.object(a, "api_get", side_effect=fake_api):
            return a.search_videos("test", 7, 50, "test")

    def test_channel_batching_and_deduplication(self):
        df = pd.DataFrame({"channel_id": [f"c{i}" for i in range(101)] + ["c1"] * 20, "views": [100] * 121})
        with patch.object(a, "api_get", side_effect=fake_api) as api:
            result = a.add_channel_stats(df, "test")
        self.assertEqual([len(c.args[1]["id"].split(",")) for c in api.call_args_list], [50, 50, 1])
        self.assertEqual(len(result), 121)
        self.assertEqual(result.channel_video_count.iloc[0], 100)

    def test_unknown_zero_and_missing_channels(self):
        df = pd.DataFrame({"channel_id": ["hidden", "zero", "missing", "public", "no_count"], "views": [100] * 5})
        response = {"items": [{"id": "hidden", "statistics": {"hiddenSubscriberCount": True, "subscriberCount": "999"}}, {"id": "zero", "statistics": {"subscriberCount": "0"}}, {"id": "public", "statistics": {"subscriberCount": "10"}}, {"id": "no_count", "statistics": {}}]}
        with patch.object(a, "api_get", return_value=response):
            result = a.add_channel_stats(df, "test")
        self.assertTrue(result.views_vs_subscribers.iloc[[0, 1, 2, 4]].isna().all())
        self.assertEqual(result.views_vs_subscribers.iloc[3], 10)
        self.assertEqual(result.subscriber_status.tolist(), ["非公開", "公開", "取得不可", "公開", "取得不可"])
        self.assertEqual(len(a.filter_videos(result, {"subscribers": (0, 0)})), 1)
        self.assertEqual(len(a.filter_videos(result, {"unknown": "only"})), 3)
        self.assertEqual(len(a.filter_videos(result, {"min_ratio": 0})), 1)

    def test_inclusive_bounds(self):
        df = self.sample()
        self.assertEqual(len(a.filter_videos(df, {"subscribers": (1000, 1000)})), 1)
        self.assertEqual(len(a.filter_videos(df, {"subscribers": (0, 999)})), 0)
        self.assertEqual(len(a.filter_videos(df, {"subscribers": (100000, None)})), 1)

    def test_all_filters_and_breakout(self):
        df = self.sample()
        result = a.filter_videos(df, {"channel_total_views": (2000000, 2000000), "channel_video_count": (100, 100), "small_max": 10000, "min_ratio": 3, "min_views": 10000, "format": "長尺"})
        self.assertEqual(result.video_id.tolist(), ["v1"])
        self.assertTrue(a.filter_videos(df, {"channel_video_count": (101, None)}).empty)

    def test_all_preset_boundaries(self):
        df = pd.DataFrame({"subscribers": [0, 999, 1000, 9999, 10000, 99999, 100000, 999999, 1000000, float("nan")]})
        for bounds, expected in [((0, 999), [0, 999]), ((1000, 9999), [1000, 9999]), ((10000, 99999), [10000, 99999]), ((100000, 999999), [100000, 999999]), ((1000000, None), [1000000])]:
            self.assertEqual(a.filter_videos(df, {"subscribers": bounds}).subscribers.tolist(), expected)

    def test_csv_preserves_unknown_instead_of_zero(self):
        from io import StringIO
        df = self.sample()
        df.loc[0, "subscribers"] = float("nan")
        df.loc[0, "views_vs_subscribers"] = float("nan")
        reread = pd.read_csv(StringIO(df.to_csv(index=False)))
        self.assertTrue(pd.isna(reread.loc[0, "subscribers"]))
        self.assertTrue(pd.isna(reread.loc[0, "views_vs_subscribers"]))

    def test_unknown_scores_remain_finite(self):
        df = self.sample()
        df["views_vs_subscribers"] = float("nan")
        scored = a.score_df(df)
        self.assertTrue(scored.trend_score.notna().all())
        self.assertTrue(scored.views_vs_subscribers.isna().all())

    def test_search_pagination_and_video_dedup(self):
        calls = []
        def paged(path, params, key):
            calls.append((path, params))
            if path == "search":
                return {"items": [{"id": {"videoId": "v1"}}], **({"nextPageToken": "p2"} if "pageToken" not in params else {})}
            return fake_api(path, params, key)
        with patch.object(a, "api_get", side_effect=paged):
            result = a.search_videos("paged", 7, 100, "test")
        self.assertEqual(len(result), 1)
        self.assertEqual([p["maxResults"] for path, p in calls if path == "search"], [50, 50])
        self.assertEqual(sum(path == "channels" for path, _ in calls), 1)

    def test_empty_and_missing_video(self):
        with patch.object(a, "api_get", return_value={"items": []}) as api:
            self.assertTrue(a.search_videos("none", 7, 50, "test").empty)
            self.assertEqual(api.call_count, 1)
        self.assertTrue(a.filter_videos(pd.DataFrame(), {}).empty)

    def test_popular_reuses_video_details(self):
        with patch.object(a, "api_get", side_effect=fake_api) as api:
            result = a.most_popular(30, "test")
        self.assertEqual(len(result), 2)
        self.assertEqual([c.args[0] for c in api.call_args_list], ["videos", "channels"])

    def test_api_errors_do_not_expose_secret(self):
        with patch.object(a.requests, "get", side_effect=requests.ConnectionError("url?key=secret-test")):
            with self.assertRaises(RuntimeError) as exc:
                a.api_get("search", {}, "secret-test")
        self.assertNotIn("secret-test", str(exc.exception))
        with patch.object(a.requests, "get", return_value=Mock(ok=False, status_code=403)):
            with self.assertRaisesRegex(RuntimeError, "403"):
                a.api_get("search", {}, "secret-test")

    def test_cache_reuses_requests_but_separates_keys(self):
        with patch.object(a.requests, "get", return_value=Mock(ok=True, json=lambda: {"items": []})) as get:
            a.api_get("channels", {"id": "a"}, "key-a")
            a.api_get("channels", {"id": "a"}, "key-a")
            a.api_get("channels", {"id": "a"}, "key-b")
        self.assertEqual(get.call_count, 2)


class UITests(unittest.TestCase):
    def setUp(self):
        a.search_videos.clear()

    def app(self):
        return AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30)

    def test_initial_without_key(self):
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}):
            app = self.app().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.tabs), 5)
        self.assertTrue(all(b.disabled for b in app.button))

    def test_all_five_tabs_and_filter_without_refetch(self):
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "test-owner-key"}), patch.object(a, "api_get", side_effect=fake_api) as api:
            app = self.app().run()
            self.assertFalse(any(t.value == "test-owner-key" for t in app.text_input))
            for label in ["人気動画を取得", "分析する", "ゲームを比較", "狙い目を分析", "長期分析を実行"]:
                next(b for b in app.button if b.label == label).click().run()
                self.assertEqual(len(app.exception), 0, label)
                self.assertEqual(len(app.error), 0, label)
            before = api.call_count
            next(s for s in app.selectbox if s.label == "登録者数の規模").select("1,000〜1万人未満").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(api.call_count, before)
            next(s for s in app.selectbox if s.label == "登録者数の規模").select("100万人以上").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(api.call_count, before)

    def test_empty_results_all_tabs(self):
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "test"}), patch.object(a, "api_get", return_value={"items": []}):
            app = self.app().run()
            for label in ["人気動画を取得", "分析する", "ゲームを比較", "狙い目を分析", "長期分析を実行"]:
                next(b for b in app.button if b.label == label).click().run()
                self.assertEqual(len(app.exception), 0, label)
                self.assertEqual(len(app.error), 0, label)

    def test_invalid_custom_range(self):
        app = self.app().run()
        next(s for s in app.selectbox if s.label == "登録者数の規模").select("任意入力").run()
        app.checkbox(key="subs_unlimited").uncheck().run()
        app.number_input(key="subs_min").set_value(20000).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("最小値" in e.value for e in app.error))

    def test_group_channel_fetch_once(self):
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "group-test"}), patch.object(a, "api_get", side_effect=fake_api) as api:
            app = self.app().run()
            app.button(key="games_button").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(sum(c.args[0] == "channels" for c in api.call_args_list), 1)

    def test_hidden_channels_ui_and_breakout(self):
        def hidden(path, params, key):
            if path == "channels":
                return {"items": [{"id": c, "statistics": {"hiddenSubscriberCount": True, "viewCount": "10", "videoCount": "2"}} for c in params["id"].split(",")]}
            return fake_api(path, params, key)
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "hidden-test"}), patch.object(a, "api_get", side_effect=hidden) as api:
            app = self.app().run()
            for label in ["分析する", "狙い目を分析"]:
                next(b for b in app.button if b.label == label).click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            before = api.call_count
            next(c for c in app.checkbox if c.label == "小規模で伸びている動画を探す").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(api.call_count, before)


if __name__ == "__main__":
    unittest.main()
