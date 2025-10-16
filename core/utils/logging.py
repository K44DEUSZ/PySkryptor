# core/utils/logging.py
from __future__ import annotations

from typing import Callable


_NOISE = (
    "UNPLAYABLE formats",
    "developer option intended for debugging",
    "impersonation",
    "SABR streaming",
    "SABR-only",
)


def _noisy(msg: str) -> bool:
    m = str(msg)
    return any(k in m for k in _NOISE)


def gui_logger(append: Callable[[str], None]) -> Callable[[str], None]:
    def _log(msg: str) -> None:
        append(str(msg))
    return _log


class YtdlpProxyLogger:
    """Adapter to route yt_dlp logs into GUI with basic filtering."""

    def __init__(self, log_fn: Callable[[str], None]) -> None:
        self._log = log_fn

    def debug(self, msg):  # too chatty
        pass

    def warning(self, msg):
        if not _noisy(str(msg)):
            self._log(str(msg))

    def error(self, msg):
        if not _noisy(str(msg)):
            self._log(str(msg))
