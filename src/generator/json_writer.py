"""UTF-8 JSON output with deterministic formatting and atomic per-file replacement."""

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def write_json_files(documents: dict[Path, Any]) -> None:
    # Serialize and stage every document before replacing any existing destination.
    serialized = {
        path: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        for path, value in documents.items()
    }
    staged = []
    try:
        for path, content in serialized.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, path))
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # NamedTemporaryFile defaults to 0600; static web servers need readable files.
            temporary.chmod(0o644)
        for temporary, path in staged:
            temporary.replace(path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
