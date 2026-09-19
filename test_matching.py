import os
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest
import analyzer as a
from matching import collect_matching
from test_analyzer import video


class FakePages:
    def __init__(self, pages, subscribers=None, languages=None, durations=None):
        self.pages = pages
        self.subscribers = subscribers or {}
        self.languages = languages or {}
        self.durations = durations or {}
        self.calls = []

    def __call__(self, path, params, key):
        self.calls.append((path, dict(params)))
        if path == "search" or params.get("chart"):
            index = int(params.get("pageToken", "0"))
            ids = self.pages[index]
            result = {"items": [self.item(v) if path == "videos" else {"id": {"videoId": v}} for v in ids]}
            if index + 1 < len(self.pages):
                result["nextPageToken"] = str(index + 1)
            return result
        if path == "videos":
            return {"items": [self.item(v) for v in params["id"].split(",")]}
        return {"items": [{"id": c, "statistics": {"subscriberCount": str(self.subscribers.get(c, 1000)), "viewCount": "500000", "videoCount": "50"}} for c in params["id"].split(",")]}

    def item(self, vid):
        item = video(vid, "c" + vid)
        if vid in self.languages:
            item["snippet"]["defaultAudioLanguage"] = self.languages[vid]
        item["contentDetails"]["duration"] = self.durations.get(vid, "PT5M")
        return item


class MatchingTests(unittest.TestCase):
    def run_collect(self, fake, target=2, filters=None, max_pages=20, jobs=None):
        with patch.object(a, "api_get", side_effect=fake):
            return collect_matching(jobs or [{"label": "test", "query": "game"}], target, "test", filters or {}, max_pages=max_pages)

    def test_reject_first_page_then_fill_requested_count(self):
        fake = FakePages([["a", "b"], ["c"], ["d", "e"], ["f"]], {"ca": 100000, "cb": 100000})
        result = self.run_collect(fake, filters={"subscribers": (0, 10000)})
        self.assertEqual(result["videos"].video_id.tolist(), ["c", "d"])
        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["report"].reason.tolist(), ["target"])
        self.assertEqual(result["report"].candidates_checked.tolist(), [5])

    def test_exact_target_when_page_has_extra_matches(self):
        fake = FakePages([[str(i) for i in range(50)], ["later"]])
        result = self.run_collect(fake, target=7)
        self.assertEqual(len(result["videos"]), 7)
        self.assertEqual(result["pages"], 1)

    def test_budget_and_exhaustion_are_distinct(self):
        fake = FakePages([["a"], ["b"]])
        result = self.run_collect(fake, target=3, max_pages=1)
        self.assertEqual(len(result["videos"]), 1)
        self.assertEqual(result["report"].reason.tolist(), ["budget"])
        result = self.run_collect(fake, target=3)
        self.assertEqual(len(result["videos"]), 2)
        self.assertEqual(result["report"].reason.tolist(), ["exhausted"])

    def test_empty_page_with_next_token_continues(self):
        result = self.run_collect(FakePages([[], ["a", "b"]]))
        self.assertEqual(len(result["videos"]), 2)
        self.assertEqual(result["pages"], 2)

    def test_duplicates_do_not_fill_target_or_refetch_channels(self):
        fake = FakePages([["a", "a"], ["a", "b"]])
        result = self.run_collect(fake)
        self.assertEqual(result["videos"].video_id.tolist(), ["a", "b"])
        ids = [p["id"] for path, p in fake.calls if path == "channels"]
        self.assertEqual(ids, ["ca", "cb"])

    def test_group_round_robin_and_shared_batch(self):
        fake = FakePages([["a"], ["b"]])
        jobs = [{"label": "one", "query": "one"}, {"label": "two", "query": "two"}]
        result = self.run_collect(fake, jobs=jobs)
        self.assertEqual(result["report"].matched.tolist(), [2, 2])
        self.assertEqual([p["q"] for path, p in fake.calls if path == "search"], ["one", "two", "one", "two"])
        self.assertEqual([p["id"] for path, p in fake.calls if path == "channels"], ["ca", "cb"])
        limited = self.run_collect(FakePages([["a"], ["b"]]), jobs=jobs, max_pages=1)
        self.assertEqual(limited["report"].pages.tolist(), [1, 0])
        self.assertEqual(limited["report"].reason.tolist(), ["budget", "budget"])

    def test_language_parameter_and_strict_filter(self):
        fake = FakePages([["en", "unknown"], ["ja", "ja2"]], languages={"en": "en-US", "ja": "ja-JP", "ja2": "ja"})
        result = self.run_collect(fake, filters={"language": "ja"})
        self.assertEqual(result["videos"].video_id.tolist(), ["ja", "ja2"])
        self.assertTrue(all(p["relevanceLanguage"] == "ja" for path, p in fake.calls if path == "search"))

    def test_unknown_language_opt_in_still_excludes_other_language(self):
        fake = FakePages([["en", "unknown", "ja"]], languages={"en": "en", "ja": "ja"})
        result = self.run_collect(fake, filters={"language": "ja", "include_unknown_language": True})
        self.assertEqual(result["videos"].video_id.tolist(), ["unknown", "ja"])

    def test_audio_language_takes_precedence_and_chinese_variants(self):
        item = video("a")
        item["snippet"].update(defaultAudioLanguage="en-US", defaultLanguage="ja")
        df = a.video_rows([item])
        self.assertEqual(df.language.iloc[0], "en-US")
        self.assertTrue(a.filter_videos(df, {"language": "ja"}).empty)
        self.assertEqual(a.normalize_language("zh-CN"), a.normalize_language("zh-Hans"))
        self.assertNotEqual(a.normalize_language("zh-Hant"), a.normalize_language("zh-Hans"))

    def test_short_duration_filter_and_boundary(self):
        fake = FakePages([["181", "unknown"], ["180", "60"]], durations={"181": "PT3M1S", "unknown": "", "180": "PT3M", "60": "PT1M"})
        result = self.run_collect(fake, filters={"format": "Shorts候補"})
        self.assertEqual(result["videos"].video_id.tolist(), ["180", "60"])
        self.assertTrue(all(p["videoDuration"] == "short" for path, p in fake.calls if path == "search"))

    def test_long_includes_three_to_twenty_minutes(self):
        fake = FakePages([["180", "181", "600"]], durations={"180": "PT3M", "181": "PT3M1S", "600": "PT10M"})
        result = self.run_collect(fake, filters={"format": "長尺"})
        self.assertEqual(result["videos"].video_id.tolist(), ["181", "600"])
        self.assertTrue(all("videoDuration" not in p for path, p in fake.calls if path == "search"))

    def test_popular_pagination_language_and_no_details_refetch(self):
        fake = FakePages([["en"], ["ja", "ja2"]], languages={"en": "en", "ja": "ja", "ja2": "ja"})
        result = self.run_collect(fake, filters={"language": "ja"}, jobs=[{"label": "popular", "kind": "popular"}])
        self.assertEqual(len(result["videos"]), 2)
        self.assertFalse(any(path == "videos" and "id" in p for path, p in fake.calls))
        self.assertTrue(all("relevanceLanguage" not in p for path, p in fake.calls))

    def test_partial_results_survive_api_failure(self):
        fake = FakePages([["a"], ["b"]])
        def fail(path, params, key):
            if path == "search" and params.get("pageToken"):
                raise RuntimeError("APIクォータエラー")
            return fake(path, params, key)
        result = self.run_collect(fail)
        self.assertEqual(result["videos"].video_id.tolist(), ["a"])
        self.assertEqual(result["report"].reason.tolist(), ["error"])
        self.assertIn("クォータ", result["error"])

    def test_repeated_page_token_stops(self):
        def repeated(path, params, key):
            if path == "search":
                return {"items": [], "nextPageToken": "same"}
            return {"items": []}
        result = self.run_collect(repeated)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["report"].reason.tolist(), ["repeat"])

    def test_ui_exact_count_language_split_and_stale_conditions(self):
        fake = FakePages([["bad"], ["short", "long", "extra"]], subscribers={"cbad": 999999}, languages={"bad": "ja", "short": "ja", "long": "ja", "extra": "ja"}, durations={"short": "PT1M"})
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "test"}), patch.object(a, "api_get", side_effect=fake):
            app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
            next(s for s in app.selectbox if s.label == "登録者数の規模").select("1,000〜1万人未満")
            next(s for s in app.selectbox if s.label == "動画の言語").select("日本語")
            app.number_input(key="search_count").set_value(2).run()
            app.button(key="search_button").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.session_state["search_result"]["result"]["videos"].video_id.tolist(), ["short", "long"])
            self.assertIn("Shorts候補（1件）", [t.label for t in app.tabs])
            self.assertIn("長尺（1件）", [t.label for t in app.tabs])
            for frame in app.dataframe:
                if "format" in frame.value.columns:
                    self.assertEqual(frame.value["format"].nunique(), 1)
            before = len(fake.calls)
            next(s for s in app.selectbox if s.label == "動画の言語").select("英語").run()
            self.assertEqual(len(fake.calls), before)
            self.assertTrue(any("取得条件が変更" in w.value for w in app.warning))
            self.assertFalse(any("format" in d.value.columns for d in app.dataframe))


if __name__ == "__main__":
    unittest.main()
