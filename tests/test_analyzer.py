import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.analyzer.hot100 import analyze, load_problem_list
from src.config import ROOT_DIR


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.problems = [
            {"title": "Two Sum", "slug": "two-sum", "difficulty": "Easy", "category": "Array"},
            {"title": "3Sum", "slug": "3sum", "difficulty": "Medium", "category": "Array"},
            {"title": "Word Search", "slug": "word-search", "difficulty": "Medium", "category": "Backtracking"},
        ]

    def test_matching_categories_and_rounding(self):
        result = analyze(self.problems, ["two-sum", "two-sum", "unrelated"])
        self.assertEqual(result["summary"], {"total": 3, "solved": 1, "percentage": 33.33})
        self.assertEqual(result["categories"]["Array"], {"total": 2, "solved": 1})
        self.assertEqual([p["slug"] for p in result["remaining"]], ["3sum", "word-search"])

    def test_empty_and_full_progress(self):
        self.assertEqual(analyze([], []) ["summary"]["percentage"], 0)
        self.assertEqual(analyze(self.problems, []) ["summary"]["solved"], 0)
        full = analyze(self.problems, [p["slug"] for p in self.problems])
        self.assertEqual(full["summary"]["percentage"], 100)
        self.assertEqual(full["remaining"], [])

    def test_bundled_hot100_has_exactly_100_unique_problems(self):
        problems = load_problem_list(ROOT_DIR / "data/hot100.json")
        self.assertEqual(len(problems), 100)
        self.assertIn("two-sum", {p["slug"] for p in problems})

    def test_bad_metadata_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "list.json"
            for payload in [[], {}, [self.problems[0]] * 2, [{"slug": "two-sum"}],
                            [{**self.problems[0], "difficulty": "Unknown"}]]:
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_problem_list(path)
