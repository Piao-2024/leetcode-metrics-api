import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.sync import run_sync
from src.config import Config, ROOT_DIR
from src.crawler.leetcode_client import LeetCodeError, UserActivity
from src.generator.json_writer import write_json_files


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        shutil.copy(ROOT_DIR / "data/hot100.json", self.data / "hot100.json")
        self.config = Config("alice", self.data)
        self.now = datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc)
        self.client = patch("scripts.sync.LeetCodeClient").start().return_value.__enter__.return_value
        self.addCleanup(patch.stopall)
        self.set_activity(["two-sum"], total=3)

    def set_activity(self, slugs, total):
        self.client.fetch_user_activity.return_value = UserActivity(
            "Alice", total, [
                {"id": str(i), "title": slug, "slug": slug, "submitted_at": "2026-09-17T01:00:00Z"}
                for i, slug in enumerate(slugs)
            ],
        )

    def read(self, filename):
        return json.loads((self.data / filename).read_text(encoding="utf-8"))

    def snapshot_bytes(self):
        return {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob("*.json")}

    def test_incremental_history_and_same_day_overwrite(self):
        first = run_sync(self.config, now=self.now)
        self.assertEqual(first["hot100"]["solved"], 1)
        self.assertFalse(first["coverage"]["is_complete"])
        self.assertEqual(self.read("history/2026-09-17.json"), self.read("progress.json"))
        self.set_activity(["3sum"], total=3)
        second = run_sync(self.config, now=self.now)
        self.assertEqual(second["hot100"]["solved"], 2)
        self.assertEqual(second["recent"]["newly_observed_slugs"], ["3sum"])
        self.set_activity([], total=3)
        run_sync(self.config, now=self.now.replace(day=18))
        self.assertEqual(self.read("solved.json")["solved_slugs"], ["3sum", "two-sum"])
        self.assertEqual(self.read("history/index.json"), ["2026-09-17", "2026-09-18"])
        self.assertEqual(self.read("history/2026-09-17.json"), second)

    def test_manual_seed_and_count_coverage(self):
        (self.data / "solved_seed.json").write_text(json.dumps({
            "username": "alice", "solved_slugs": ["3sum", "word-search", "word-search"],
        }))
        result = run_sync(self.config, now=self.now)
        self.assertTrue(result["coverage"]["is_complete"])
        self.assertEqual(result["hot100"]["solved"], 3)

    def test_api_failure_preserves_all_existing_data(self):
        run_sync(self.config, now=self.now)
        before = self.snapshot_bytes()
        self.client.fetch_user_activity.side_effect = LeetCodeError("HTTP 403")
        with self.assertRaises(LeetCodeError):
            run_sync(self.config, now=self.now)
        self.assertEqual(self.snapshot_bytes(), before)

    def test_different_account_cannot_mix_data(self):
        run_sync(self.config, now=self.now)
        before = self.snapshot_bytes()
        with self.assertRaisesRegex(ValueError, "Account mismatch"):
            run_sync(Config("bob", self.data), now=self.now)
        self.assertEqual(self.snapshot_bytes(), before)

    def test_corrupt_state_is_not_reset(self):
        (self.data / "solved.json").write_text("broken")
        before = self.snapshot_bytes()
        with self.assertRaises(ValueError):
            run_sync(self.config, now=self.now)
        self.assertEqual(self.snapshot_bytes(), before)
        self.client.fetch_user_activity.assert_not_called()

    def test_missing_state_is_not_reset(self):
        run_sync(self.config, now=self.now)
        (self.data / "solved.json").unlink()
        with self.assertRaisesRegex(ValueError, "restore it from Git"):
            run_sync(self.config, now=self.now)

    def test_history_account_is_checked_before_writing(self):
        history = self.data / "history"
        history.mkdir()
        (history / "2026-09-16.json").write_text('{"username":"bob"}')
        before = self.snapshot_bytes()
        with self.assertRaisesRegex(ValueError, "Account mismatch"):
            run_sync(self.config, now=self.now)
        self.assertEqual(self.snapshot_bytes(), before)

    def test_same_inputs_produce_identical_files(self):
        run_sync(self.config, now=self.now)
        run_sync(self.config, now=self.now)
        before = self.snapshot_bytes()
        run_sync(self.config, now=self.now)
        self.assertEqual(self.snapshot_bytes(), before)

    def test_cn_first_sync_accepts_uninitialized_progress_and_site_seed(self):
        (self.data / "progress.json").write_text(json.dumps({
            "schema_version": 1,
            "site": "com",
            "username": "",
            "updated_at": None,
        }))
        (self.data / "solved_seed.json").write_text(json.dumps({
            "site": "cn", "username": "alice", "solved_slugs": ["3sum", "xoh6Oh"],
        }))
        result = run_sync(Config("alice", self.data, site="cn"), now=self.now)
        self.assertEqual(result["site"], "cn")
        self.assertEqual(result["hot100"]["solved"], 2)
        self.assertEqual(self.read("solved.json")["site"], "cn")
        self.assertIn("xoh6Oh", self.read("solved.json")["solved_slugs"])
        self.assertEqual(self.read("history/2026-09-17.json")["site"], "cn")

    def test_same_username_cannot_mix_sites(self):
        run_sync(self.config, now=self.now)
        before = self.snapshot_bytes()
        with self.assertRaisesRegex(ValueError, "Site mismatch"):
            run_sync(Config("alice", self.data, site="cn"), now=self.now)
        self.assertEqual(self.snapshot_bytes(), before)

    def test_legacy_state_and_history_remain_global(self):
        run_sync(self.config, now=self.now)
        for filename in ["solved.json", "progress.json", "history/2026-09-17.json"]:
            payload = self.read(filename)
            del payload["site"]
            (self.data / filename).write_text(json.dumps(payload))
        before = self.snapshot_bytes()
        with self.assertRaisesRegex(ValueError, "Site mismatch"):
            run_sync(Config("alice", self.data, site="cn"), now=self.now)
        self.assertEqual(self.snapshot_bytes(), before)
        result = run_sync(self.config, now=self.now.replace(day=18))
        self.assertEqual(result["site"], "com")

    def test_cn_rejects_global_history_even_without_current_state(self):
        history = self.data / "history"
        history.mkdir()
        (history / "2026-09-16.json").write_text('{"username":"alice"}')
        before = self.snapshot_bytes()
        with self.assertRaisesRegex(ValueError, "Site mismatch"):
            run_sync(Config("alice", self.data, site="cn"), now=self.now)
        self.assertEqual(self.snapshot_bytes(), before)


class WriterTests(unittest.TestCase):
    def test_serialization_failure_does_not_replace_existing_files(self):
        with TemporaryDirectory() as directory:
            first, second = Path(directory) / "first.json", Path(directory) / "second.json"
            first.write_text("original")
            with self.assertRaises(ValueError):
                write_json_files({first: {"valid": 1}, second: {"invalid": float("nan")}})
            self.assertEqual(first.read_text(), "original")
            self.assertFalse(second.exists())

    def test_unicode_and_temp_cleanup(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            write_json_files({path: {"title": "两数之和"}})
            self.assertIn("两数之和", path.read_text(encoding="utf-8"))
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


class ConfigTests(unittest.TestCase):
    def test_site_configuration(self):
        for raw, expected in [("", "com"), ("com", "com"), (" CN ", "cn")]:
            with self.subTest(raw=raw), patch.dict("os.environ", {
                "LEETCODE_USERNAME": "alice", "LEETCODE_SITE": raw,
            }, clear=True):
                self.assertEqual(Config.from_env().site, expected)
        with patch.dict("os.environ", {"LEETCODE_USERNAME": "alice"}, clear=True):
            self.assertEqual(Config.from_env().site, "com")
        with patch.dict("os.environ", {"LEETCODE_USERNAME": "alice", "LEETCODE_SITE": "other"}):
            with self.assertRaises(ValueError):
                Config.from_env()

    def test_username_is_required(self):
        for value in ["", "   ", "your_username"]:
            with self.subTest(value=value), patch.dict("os.environ", {"LEETCODE_USERNAME": value}):
                with self.assertRaises(ValueError):
                    Config.from_env()
