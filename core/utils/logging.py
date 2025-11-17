# core/utils/logging.py
from __future__ import annotations

from typing import Callable, Iterable


# Patterns we consider spammy/noisy and want to hide from the GUI
_NOISE_PATTERNS: tuple[str, ...] = (
    "UNPLAYABLE formats",
    "developer option intended for debugging",
    "impersonation",
    "SABR streaming",
    "SABR-only",
    "[debug]",
)


def _is_noisy(msg: str, extra_noise: Iterable[str] | None = None) -> bool:
    text = str(msg)
    for k in _NOISE_PATTERNS:
        if k in text:
            return True
    if extra_noise:
        for k in extra_noise:
            if k and str(k) in text:
                return True
    return False


def gui_logger(append: Callable[[str], None]) -> Callable[[str], None]:
    """Return a simple callable that appends messages to the GUI log box."""
    def _log(msg: str) -> None:
        append(str(msg))
    return _log


class YtdlpQtLogger:
    """
    Logger adapter for yt_dlp that routes messages to GUI with basic filtering.
    Use in YoutubeDL opts: {"logger": YtdlpQtLogger(gui_log_fn)}.
    """

    def __init__(self, log_fn: Callable[[str], None], *, extra_noise: Iterable[str] | None = None) -> None:
        self._log = log_fn
        self._extra_noise = tuple(extra_noise or ())

    # yt_dlp calls these methods if provided on the logger object.
    def debug(self, msg):  # very chatty; ignore entirely
        pass

    def info(self, msg):
        if not _is_noisy(str(msg), self._extra_noise):
            self._log(str(msg))

    def warning(self, msg):
        if not _is_noisy(str(msg), self._extra_noise):
            self._log(str(msg))

    def error(self, msg):
        if not _is_noisy(str(msg), self._extra_noise):
            self._log(str(msg))
