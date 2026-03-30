# app/model/core/utils/path_utils.py
from __future__ import annotations

import logging
import shutil
from pathlib import Path

_LOG = logging.getLogger(__name__)


def ensure_unique_path(path: Path) -> Path:
    """Return a non-conflicting path by appending ``(n)`` before the suffix."""
    try:
        candidate = Path(path)
    except (TypeError, ValueError):
        return Path(str(path))

    if not candidate.exists():
        return candidate

    parent = candidate.parent
    stem = str(candidate.stem or "").strip() or "output"
    suffix = str(candidate.suffix or "")

    idx = 1
    while True:
        next_path = parent / f"{stem} ({idx}){suffix}"
        if not next_path.exists():
            return next_path
        idx += 1


def clear_temp_dir(path: Path) -> None:
    """Remove a temp directory if it exists; ignore cleanup errors."""
    if not path:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as ex:
        _LOG.debug("Temp directory cleanup skipped. path=%s detail=%s", path, ex)
