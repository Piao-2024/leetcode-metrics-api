"""Read public global/China data. Recent AC submissions are NOT a full AC list."""

from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import SLUG_PATTERN

RECENT_LIMIT = 20
USER_QUERY = """
query UserActivity($username: String!, $limit: Int!) {
  matchedUser(username: $username) {
    username
    submitStatsGlobal { acSubmissionNum { difficulty count } }
  }
  recentAcSubmissionList(username: $username, limit: $limit) {
    id title titleSlug timestamp
  }
}
"""

CN_PROFILE_QUERY = """
query UserProfile($userSlug: String!) {
  userProfilePublicProfile(userSlug: $userSlug) {
    profile { userSlug }
  }
  userProfileUserQuestionProgress(userSlug: $userSlug) {
    numAcceptedQuestions { difficulty count }
  }
}
"""
CN_RECENT_QUERY = """
query RecentAccepted($userSlug: String!) {
  recentACSubmissions(userSlug: $userSlug) {
    submissionId submitTime
    question { title titleSlug }
  }
}
"""


class LeetCodeError(RuntimeError):
    """Network, GraphQL, account, or response schema error."""


@dataclass(frozen=True)
class UserActivity:
    username: str
    total_solved: int
    submissions: list[dict]

    @property
    def solved_slugs(self) -> set[str]:
        """Unique accepted problems visible in the public recent window only."""
        return {submission["slug"] for submission in self.submissions}


class LeetCodeClient:
    def __init__(self, site: str = "com") -> None:
        if site not in {"com", "cn"}:
            raise ValueError("LeetCode site must be 'com' or 'cn'.")
        self.site = site
        self.base_url = f"https://leetcode.{site}"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "leetcode-metrics-api/1.0",
            "Referer": f"{self.base_url}/",
            "Content-Type": "application/json",
        })
        # Retrying POST is safe here: these GraphQL operations only read data.
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"POST"}),
            respect_retry_after_header=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def __enter__(self) -> "LeetCodeClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.session.close()

    def _query(self, query: str, variables: dict, *, path: str = "/graphql/") -> dict:
        try:
            response = self.session.post(
                self.base_url + path,
                json={"query": query, "variables": variables},
                timeout=(10, 30),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            detail = f"HTTP {status}" if status else "network error or timeout"
            raise LeetCodeError(f"LeetCode request failed ({detail}).") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise LeetCodeError("LeetCode returned invalid JSON.") from exc
        if not isinstance(payload, dict) or payload.get("errors"):
            raise LeetCodeError("LeetCode returned GraphQL errors.")
        if not isinstance(payload.get("data"), dict):
            raise LeetCodeError("LeetCode response is missing data.")
        return payload["data"]

    def _fetch_cn_data(self, username: str) -> dict:
        """CN profile and recent AC live on different GraphQL gateways."""
        variables = {"userSlug": username}
        profile_data = self._query(CN_PROFILE_QUERY, variables)
        try:
            public = profile_data["userProfilePublicProfile"]
            if public is None or public["profile"] is None:
                raise LeetCodeError("LeetCode user was not found or is unavailable.")
            canonical_name = public["profile"]["userSlug"]
            counts = profile_data["userProfileUserQuestionProgress"]["numAcceptedQuestions"]
            if not isinstance(counts, list) or len(counts) != 3:
                raise ValueError("Invalid difficulty counts")
            if {item["difficulty"] for item in counts} != {"EASY", "MEDIUM", "HARD"}:
                raise ValueError("Missing difficulty counts")
            if any(type(item["count"]) is not int or item["count"] < 0 for item in counts):
                raise ValueError("Invalid solved count")
            total = sum(item["count"] for item in counts)
            recent_data = self._query(CN_RECENT_QUERY, variables, path="/graphql/noj-go/")
            recent = recent_data["recentACSubmissions"]
            if not isinstance(recent, list):
                raise ValueError("Invalid submissions")
            normalized = []
            for item in recent:
                # CN returns numeric IDs/timestamps; normalize to the shared format.
                identifier, timestamp = item["submissionId"], item["submitTime"]
                if type(identifier) not in (str, int) or type(timestamp) not in (str, int):
                    raise ValueError("Invalid submission ID or timestamp")
                normalized.append({
                    "id": str(identifier), "timestamp": str(timestamp),
                    "title": item["question"]["title"],
                    "titleSlug": item["question"]["titleSlug"],
                })
            return {
                "matchedUser": {
                    "username": canonical_name,
                    "submitStatsGlobal": {"acSubmissionNum": [{"difficulty": "All", "count": total}]},
                },
                "recentAcSubmissionList": normalized,
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise LeetCodeError("LeetCode CN response schema is invalid or has changed.") from exc

    def fetch_user_activity(self, username: str) -> UserActivity:
        """Fetch solved count and the public recent AC window for the selected site."""
        if not username.strip():
            raise ValueError("Username must not be empty.")
        data = (
            self._fetch_cn_data(username) if self.site == "cn"
            else self._query(USER_QUERY, {"username": username, "limit": RECENT_LIMIT})
        )
        if "matchedUser" not in data:
            raise LeetCodeError("LeetCode response is missing matchedUser.")
        if data["matchedUser"] is None:
            raise LeetCodeError("LeetCode user was not found or is unavailable.")
        try:
            user = data["matchedUser"]
            canonical_name = user["username"]
            if not isinstance(canonical_name, str) or canonical_name.casefold() != username.casefold():
                raise ValueError("Unexpected account")
            counts = user["submitStatsGlobal"]["acSubmissionNum"]
            totals = [item["count"] for item in counts if item["difficulty"] == "All"]
            if len(totals) != 1 or type(totals[0]) is not int or totals[0] < 0:
                raise ValueError("Invalid solved count")
            raw_submissions = data["recentAcSubmissionList"]
            if not isinstance(raw_submissions, list):
                raise ValueError("Invalid submissions")
            submissions = []
            seen_ids = set()
            for item in raw_submissions:
                slug, title, identifier = item["titleSlug"], item["title"], item["id"]
                if not isinstance(slug, str) or not SLUG_PATTERN.fullmatch(slug):
                    raise ValueError("Invalid slug")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("Invalid title")
                if not isinstance(identifier, str) or not identifier.isdigit():
                    raise ValueError("Invalid submission ID")
                timestamp = item["timestamp"]
                if not isinstance(timestamp, str) or not timestamp.isdigit():
                    raise ValueError("Invalid timestamp")
                submitted_at = datetime.fromtimestamp(int(timestamp), timezone.utc)
                if identifier not in seen_ids:
                    submissions.append({
                        "id": identifier,
                        "title": title,
                        "slug": slug,
                        "submitted_at": submitted_at.isoformat().replace("+00:00", "Z"),
                    })
                    seen_ids.add(identifier)
            submissions.sort(key=lambda item: (item["submitted_at"], int(item["id"])), reverse=True)
            return UserActivity(canonical_name, totals[0], submissions)
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
            raise LeetCodeError("LeetCode response schema is invalid or has changed.") from exc
