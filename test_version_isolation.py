import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import yt_analyzer_v21 as a
from test_analyzer import fake_api


class VersionIsolationTests(unittest.TestCase):
    def test_search_ignores_loaded_legacy_modules(self):
        legacy = types.ModuleType("analyzer")
        def old_add_channel_stats(df, api_key):
            raise AssertionError("旧関数を呼び出してはいけません")
        legacy.add_channel_stats = old_add_channel_stats
        old_matching = types.ModuleType("matching")
        with patch.dict(sys.modules, {"analyzer": legacy, "matching": old_matching}), patch.dict(os.environ, {"YOUTUBE_API_KEY": "test"}), patch.object(a, "api_get", side_effect=fake_api):
            app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
            app.number_input(key="search_count").set_value(2).run()
            app.button(key="search_button").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            self.assertEqual(len(app.session_state["search_result"]["result"]["videos"]), 2)
            self.assertTrue(any("バージョン 2.1" in c.value for c in app.caption))
