"""Pure problem-list analysis: the same functions also work for other JSON lists."""

import json
from collections.abc import Iterable
from pathlib import Path

from src.config import SLUG_PATTERN


def load_problem_list(path: Path) -> list[dict]:
    problems = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(problems, list) or not problems:
        raise ValueError(f"{path.name} must contain a non-empty problem array.")
    seen = set()
    for problem in problems:
        if not isinstance(problem, dict) or any(
            not isinstance(problem.get(field), str) or not problem[field].strip()
            for field in ("title", "slug", "difficulty", "category")
        ):
            raise ValueError(f"Invalid problem metadata in {path.name}.")
        slug = problem["slug"]
        if not SLUG_PATTERN.fullmatch(slug) or slug in seen:
            raise ValueError(f"Invalid or duplicate problem slug in {path.name}: {slug}")
        if problem["difficulty"] not in {"Easy", "Medium", "Hard"}:
            raise ValueError(f"Invalid difficulty for {slug}.")
        seen.add(slug)
    return problems


def analyze(problems: list[dict], solved_slugs: Iterable[str]) -> dict:
    """Match by slug, not translated titles; counts represent known accepted problems."""
    accepted = set(solved_slugs)
    categories = {}
    remaining = []
    solved = 0
    for problem in problems:
        category = categories.setdefault(problem["category"], {"total": 0, "solved": 0})
        category["total"] += 1
        if problem["slug"] in accepted:
            category["solved"] += 1
            solved += 1
        else:
            remaining.append(dict(problem))
    total = len(problems)
    return {
        "summary": {
            "total": total,
            "solved": solved,
            "percentage": round(solved / total * 100, 2) if total else 0,
        },
        "categories": categories,
        "remaining": remaining,
    }
