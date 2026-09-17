import unittest
from unittest.mock import Mock, patch

import requests

from src.crawler.leetcode_client import LeetCodeClient, LeetCodeError


def response_data():
    return {"data": {
        "matchedUser": {
            "username": "Alice",
            "submitStatsGlobal": {"acSubmissionNum": [{"difficulty": "All", "count": 45}]},
        },
        "recentAcSubmissionList": [
            {"id": "12", "title": "Two Sum", "titleSlug": "two-sum", "timestamp": "1700000000"},
            {"id": "13", "title": "Two Sum", "titleSlug": "two-sum", "timestamp": "1700000001"},
        ],
    }}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = LeetCodeClient()
        self.addCleanup(self.client.session.close)
        self.post = patch.object(self.client.session, "post").start()
        self.addCleanup(patch.stopall)
        self.response = Mock()
        self.post.return_value = self.response
        self.response.json.return_value = response_data()

    def test_public_window_is_deduplicated_but_not_claimed_as_total(self):
        result = self.client.fetch_user_activity("alice")
        self.assertEqual(result.solved_slugs, {"two-sum"})
        self.assertEqual(result.total_solved, 45)
        self.assertEqual(result.submissions[0]["id"], "13")
        self.assertEqual(self.post.call_args.kwargs["timeout"], (10, 30))

    def test_timeout_and_http_errors(self):
        for error in [requests.Timeout(), requests.HTTPError(response=Mock(status_code=403))]:
            with self.subTest(error=error):
                self.post.side_effect = error
                with self.assertRaises(LeetCodeError):
                    self.client.fetch_user_activity("alice")

    def test_bad_json(self):
        self.response.json.side_effect = ValueError()
        with self.assertRaisesRegex(LeetCodeError, "invalid JSON"):
            self.client.fetch_user_activity("alice")

    def test_error_missing_user_and_schema(self):
        for payload in [
            {"errors": [{"message": "upstream error"}]},
            {"data": {"matchedUser": None}},
            {"data": {}},
            {"data": {"matchedUser": {"username": "Alice"}}},
            {"data": None},
        ]:
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                with self.assertRaises(LeetCodeError):
                    self.client.fetch_user_activity("alice")

    def test_empty_window_is_valid(self):
        payload = response_data()
        payload["data"]["recentAcSubmissionList"] = []
        self.response.json.return_value = payload
        self.assertEqual(self.client.fetch_user_activity("alice").solved_slugs, set())

    def test_malformed_submission_is_not_silently_dropped(self):
        payload = response_data()
        payload["data"]["recentAcSubmissionList"][0]["timestamp"] = "broken"
        self.response.json.return_value = payload
        with self.assertRaises(LeetCodeError):
            self.client.fetch_user_activity("alice")


class ChinaClientTests(unittest.TestCase):
    def setUp(self):
        self.client = LeetCodeClient("cn")
        self.addCleanup(self.client.session.close)
        self.post = patch.object(self.client.session, "post").start()
        self.addCleanup(patch.stopall)
        self.profile = {"data": {
            "userProfilePublicProfile": {"profile": {"userSlug": "alice"}},
            "userProfileUserQuestionProgress": {"numAcceptedQuestions": [
                {"difficulty": "EASY", "count": 2},
                {"difficulty": "MEDIUM", "count": 3},
                {"difficulty": "HARD", "count": 1},
            ]},
        }}
        self.recent = {"data": {"recentACSubmissions": [
            {"submissionId": 12, "submitTime": 1700000000,
             "question": {"title": "Two Sum", "titleSlug": "two-sum"}},
            {"submissionId": "13", "submitTime": "1700000001",
             "question": {"title": "Two Sum", "titleSlug": "two-sum"}},
        ]}}

    def fetch(self):
        self.post.side_effect = [
            Mock(json=Mock(return_value=self.profile)),
            Mock(json=Mock(return_value=self.recent)),
        ]
        return self.client.fetch_user_activity("alice")

    def test_cn_gateways_counts_and_normalization(self):
        activity = self.fetch()
        self.assertEqual(activity.total_solved, 6)
        self.assertEqual(activity.solved_slugs, {"two-sum"})
        self.assertEqual(activity.submissions[0]["id"], "13")
        self.assertEqual(activity.submissions[0]["submitted_at"], "2023-11-14T22:13:21Z")
        self.assertEqual([c.args[0] for c in self.post.call_args_list], [
            "https://leetcode.cn/graphql/", "https://leetcode.cn/graphql/noj-go/",
        ])
        self.assertEqual(self.client.session.headers["Referer"], "https://leetcode.cn/")
        self.assertEqual(self.post.call_args.kwargs["json"]["variables"], {"userSlug": "alice"})

    def test_cn_empty_window(self):
        self.recent["data"]["recentACSubmissions"] = []
        self.assertEqual(self.fetch().solved_slugs, set())

    def test_cn_mixed_case_problem_slug_is_preserved(self):
        self.recent["data"]["recentACSubmissions"][0]["question"]["titleSlug"] = "xoh6Oh"
        self.assertIn("xoh6Oh", self.fetch().solved_slugs)

    def test_cn_missing_user(self):
        self.profile["data"]["userProfilePublicProfile"] = None
        with self.assertRaisesRegex(LeetCodeError, "not found"):
            self.fetch()
        self.assertEqual(self.post.call_count, 1)

    def test_cn_incomplete_counts_are_rejected(self):
        self.profile["data"]["userProfileUserQuestionProgress"]["numAcceptedQuestions"].pop()
        with self.assertRaises(LeetCodeError):
            self.fetch()

    def test_cn_null_recent_is_not_treated_as_empty(self):
        self.recent["data"]["recentACSubmissions"] = None
        with self.assertRaises(LeetCodeError):
            self.fetch()

    def test_cn_invalid_numeric_values_are_rejected(self):
        for value in [True, 1.5, -1, "invalid"]:
            with self.subTest(value=value):
                self.recent["data"]["recentACSubmissions"][0]["submitTime"] = value
                with self.assertRaises(LeetCodeError):
                    self.fetch()

    def test_cn_second_gateway_failure(self):
        self.recent = {"errors": [{"message": "upstream failed"}]}
        with self.assertRaisesRegex(LeetCodeError, "GraphQL"):
            self.fetch()

    def test_unknown_site_rejected(self):
        with self.assertRaises(ValueError):
            LeetCodeClient("invalid")
