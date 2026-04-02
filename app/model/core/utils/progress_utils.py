# app/model/core/utils/progress_utils.py
from __future__ import annotations

import threading
from typing import Callable

ProgressFn = Callable[[int], None]


def clamp_progress_pct(pct: int) -> int:
    """Clamp one progress value to the supported 0..100 range."""

    return max(0, min(100, int(pct)))


def parse_progress_pct(value: object) -> int | None:
    """Parse one raw progress value or return None when it is invalid."""

    try:
        return clamp_progress_pct(int(value))
    except (TypeError, ValueError):
        return None


def progress_pct_from_budget(*, completed: int, total: int) -> int:
    """Convert completed work and total work into one normalized percent."""

    safe_total = max(1, int(total))
    safe_completed = max(0, int(completed))
    pct = int(round((safe_completed / float(safe_total)) * 100.0))
    return clamp_progress_pct(pct)


def build_monotonic_progress_emitter(progress_cb: ProgressFn | None) -> ProgressFn:
    """Wrap one progress callback so emitted values never go backwards."""

    if progress_cb is None:
        return lambda _pct: None

    progress_lock = threading.Lock()
    last_pct = 0

    def _emit(pct: int) -> None:
        nonlocal last_pct
        normalized = parse_progress_pct(pct)
        if normalized is None:
            return
        with progress_lock:
            if normalized < last_pct:
                normalized = last_pct
            if normalized == last_pct:
                return
            last_pct = normalized
        progress_cb(normalized)

    return _emit
