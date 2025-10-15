# pyskryptor/core/utils/logging.py
from __future__ import annotations

from typing import Callable


def gui_logger(append_fn: Callable[[str], None]) -> Callable[[str], None]:
    def _log(msg: str) -> None:
        append_fn(msg)
    return _log


class YtdlpProxyLogger:
    """Adapter that forwards yt_dlp logs to GUI logger function."""

    def __init__(self, log_fn: Callable[[str], None]) -> None:
        self._log = log_fn

    def debug(self, msg):  # noqa: D401
        pass

    def warning(self, msg):
        self._log(str(msg))

    def error(self, msg):
        self._log(str(msg))
