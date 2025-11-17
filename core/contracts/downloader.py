# core/contracts/downloader.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Any


class Downloader(Protocol):
    """
    Minimal IO contract for media probing and downloading.

    - probe(url, log) -> dict : return lightweight metadata (service, title,
      duration, formats, suggested filename), without downloading.
    - download(urls, on_file_ready, log, *, kind, quality, ext, progress_cb)
      -> list[Path] : fetch media with optional format hints and progress.
    """

    def probe(self, url: str, log: Callable[[str], None]) -> Dict[str, Any]: ...

    def download(
        self,
        urls: List[str],
        on_file_ready: Optional[Callable[[Path], None]],
        log: Callable[[str], None],
        *,
        kind: Optional[str] = None,          # "audio" | "video" | None
        quality: Optional[str] = None,       # e.g. "auto", "1080p", "320k"
        ext: Optional[str] = None,           # e.g. "mp4", "webm", "m4a", "mp3"
        progress_cb: Optional[Callable[[int, str], None]] = None,  # percent, stage
    ) -> List[Path]: ...
