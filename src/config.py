"""Small, environment-based configuration; paths do not depend on the caller's cwd."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
# China-only LCR/LCP questions can use mixed-case slugs, e.g. xoh6Oh.
SLUG_PATTERN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


@dataclass(frozen=True)
class Config:
    username: str
    data_dir: Path = ROOT_DIR / "data"
    site: str = "com"

    def __post_init__(self) -> None:
        if self.site not in {"com", "cn"}:
            raise ValueError("LEETCODE_SITE must be 'com' or 'cn'.")

    @classmethod
    def from_env(cls) -> "Config":
        username = os.environ.get("LEETCODE_USERNAME", "").strip()
        if not username or username == "your_username":
            raise ValueError("Set LEETCODE_USERNAME to your profile URL username/user slug.")
        site = os.environ.get("LEETCODE_SITE", "com").strip().lower() or "com"
        return cls(username=username, site=site)
