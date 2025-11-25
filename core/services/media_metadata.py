# core/services/media_metadata.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.io.audio_extractor import AudioExtractor
from core.services.download_service import DownloadService


@dataclass
class MediaMetadata:
    """Unified metadata shape used across Files tab, URL and Downloader."""
    source: str                 # "LOCAL" | "URL"
    title: str
    path: str                   # filesystem path or URL
    duration: Optional[float]
    size: Optional[int]

    # extra info, mainly for downloader / URL
    service: Optional[str] = None          # e.g. "YouTube"
    formats: Optional[List[Dict[str, Any]]] = None
    audio_langs: Optional[List[Dict[str, Any]]] = None

    def as_files_row(self) -> Dict[str, Any]:
        """Row shape expected by Files table."""
        return {
            "name": self.title,
            "source": self.source,
            "path": self.path,
            "size": self.size,
            "duration": self.duration,
        }


class MediaMetadataService:
    """Central place for building MediaMetadata from local files and URLs."""

    def __init__(self, down: Optional[DownloadService] = None) -> None:
        self._down = down or DownloadService()

    # ----- URL / remote -----

    def from_url(self, url: str, *, log=lambda msg: None) -> MediaMetadata:
        """Probe remote media (yt_dlp) and normalize into MediaMetadata."""
        raw = self._down.probe(url, log=log)

        size = raw.get("filesize") or raw.get("filesize_approx")
        dur = raw.get("duration")

        return MediaMetadata(
            source="URL",
            title=raw.get("title") or url,
            path=url,
            duration=float(dur) if dur is not None else None,
            size=int(size) if size is not None else None,
            service=raw.get("extractor_key") or raw.get("extractor"),
            formats=raw.get("formats") or [],
            # jeśli w DownloadService.probe dorzucasz audio_langs, to tutaj:
            audio_langs=raw.get("audio_langs") or None,
        )

    # ----- Local file -----

    def from_local(self, path: Path) -> Optional[MediaMetadata]:
        """Build metadata for a local media file; returns None if file invalid."""
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None

        try:
            size = p.stat().st_size
        except Exception:
            size = None

        dur = AudioExtractor.probe_duration(p)

        return MediaMetadata(
            source="LOCAL",
            title=p.stem,
            path=str(p),
            duration=float(dur) if dur is not None else None,
            size=int(size) if size is not None else None,
        )
