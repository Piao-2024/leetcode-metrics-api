"""Fetch public AC activity, merge known problems, and publish static JSON files."""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
import sys

# Allow `python /path/to/scripts/sync.py` without installing a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analyzer.hot100 import analyze, load_problem_list
from src.config import Config, SLUG_PATTERN
from src.crawler.leetcode_client import LeetCodeClient, LeetCodeError
from src.generator.json_writer import write_json_files

logger = logging.getLogger(__name__)


def check_identity(payload: dict, username: str, site: str, label: str) -> None:
    # Outputs from the original global-only version have no site field.
    if payload.get("site", "com") != site:
        raise ValueError(f"Site mismatch in {label}; archive old data before switching sites.")
    if payload["username"].casefold() != username.casefold():
        raise ValueError(f"Account mismatch in {label}; do not mix users' data.")


def read_solved_slugs(path: Path, username: str, site: str = "com") -> set[str]:
    """Both the accumulated state and optional manual seed use the same format."""
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("username"), str):
        raise ValueError(f"Invalid account metadata in {path.name}.")
    check_identity(payload, username, site, path.name)
    slugs = payload.get("solved_slugs")
    if not isinstance(slugs, list) or any(
        not isinstance(slug, str) or not SLUG_PATTERN.fullmatch(slug) for slug in slugs
    ):
        raise ValueError(f"Invalid solved_slugs in {path.name}.")
    return set(slugs)


def run_sync(config: Config, *, now: datetime | None = None) -> dict:
    data_dir = config.data_dir
    problems = load_problem_list(data_dir / "hot100.json")
    previous = read_solved_slugs(data_dir / "solved.json", config.username, config.site)
    seed = read_solved_slugs(data_dir / "solved_seed.json", config.username, config.site)
    progress_path = data_dir / "progress.json"
    if progress_path.exists():
        old_progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if not isinstance(old_progress, dict) or not isinstance(old_progress.get("username"), str):
            raise ValueError("Invalid account metadata in progress.json.")
        old_username = old_progress["username"]
        if old_username:
            check_identity(old_progress, config.username, config.site, "progress.json")
        if old_username and not (data_dir / "solved.json").exists():
            raise ValueError("solved.json is missing; restore it from Git to preserve accumulated AC data.")

    with LeetCodeClient(config.site) as client:
        activity = client.fetch_user_activity(config.username)
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("Sync timestamp must include a timezone.")
    timestamp = timestamp.astimezone(timezone.utc)
    day = timestamp.date().isoformat()
    known = previous | seed | activity.solved_slugs
    result = analyze(problems, known)
    complete = len(known) == activity.total_solved
    progress = {
        "schema_version": 1,
        "site": config.site,
        "username": activity.username,
        "updated_at": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "hot100": result["summary"],
        "categories": result["categories"],
        "remaining": result["remaining"],
        "recent": {
            "submissions": activity.submissions,
            "newly_observed_slugs": sorted(activity.solved_slugs - previous - seed),
        },
        "coverage": {
            "source": "public_recent_ac_with_local_history",
            "is_complete": complete,
            "known_solved": len(known),
            "reported_solved": activity.total_solved,
        },
    }
    history_dir = data_dir / "history"
    days = {day}
    for path in history_dir.glob("????-??-??.json"):
        date.fromisoformat(path.stem)
        # Protect historical data even if the current state was manually removed.
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("username"), str):
            raise ValueError(f"Invalid historical snapshot: {path.name}.")
        check_identity(snapshot, activity.username, config.site, f"history/{path.name}")
        days.add(path.stem)
    write_json_files({
        data_dir / "solved.json": {
            "site": config.site,
            "username": activity.username,
            "solved_slugs": sorted(known),
        },
        history_dir / f"{day}.json": progress,
        history_dir / "index.json": sorted(days),
        progress_path: progress,
    })
    if not complete:
        logger.warning(
            "Partial coverage: %s known solved problems, %s reported by LeetCode. "
            "Hot100 progress is a lower bound; remaining problems are unconfirmed.",
            len(known), activity.total_solved,
        )
    logger.info(
        "Hot100: %s/%s (%s%%); snapshot: %s",
        result["summary"]["solved"], result["summary"]["total"],
        result["summary"]["percentage"], day,
    )
    return progress


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        run_sync(Config.from_env())
    except (LeetCodeError, ValueError, OSError) as exc:
        logger.error("Sync failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
