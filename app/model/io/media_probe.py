# app/model/io/media_probe.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.model.io.audio_extractor import AudioExtractor
from app.model.services.download_service import DownloadService

_URL_RE = re.compile(r"^(?:https?://|ftp://)", re.IGNORECASE)

def is_url_source(value: str) -> bool:
    """Return True if value looks like a URL."""
    return bool(value) and bool(_URL_RE.match(value.strip()))


@dataclass
class MediaProbe:
    """Unified metadata shape used across Files tab, URL and Downloader."""

    source: str
    title: str
    path: str
    duration: float | None
    size: int | None

    service: str | None = None
    formats: list[dict[str, Any]] | None = None
    audio_langs: list[dict[str, Any]] | None = None
    probe_diag: dict[str, Any] | None = None

    def as_files_row(self) -> dict[str, Any]:
        """Row shape expected by Files table."""
        return {
            "name": self.title,
            "source": self.source,
            "path": self.path,
            "size": self.size,
            "duration": self.duration,
            "audio_tracks": self.audio_langs or [],
            "probe_diag": self.probe_diag or {},
        }


class MediaProbeService:
    """Central place for building MediaProbe from local files and URLs."""

    def __init__(self, down: DownloadService | None = None) -> None:
        self._down = down or DownloadService()

    # ----- URL / remote -----

    def from_url(self, url: str) -> MediaProbe:
        """Probe remote media (yt_dlp) and normalize into MediaProbe."""
        raw = self._down.probe(url)

        size = raw.get("filesize") or raw.get("filesize_approx")
        dur = raw.get("duration")

        audio_tracks = raw.get("audio_tracks") or raw.get("audio_langs") or None

        return MediaProbe(
            source="URL",
            title=raw.get("title") or url,
            path=url,
            duration=float(dur) if dur is not None else None,
            size=int(size) if size is not None else None,
            service=raw.get("extractor_key") or raw.get("extractor"),
            formats=raw.get("formats") or [],
            audio_langs=audio_tracks,
            probe_diag=raw.get("probe_diag") or None,
        )

    # ----- Local file -----

    @staticmethod
    def from_local(path: Path) -> MediaProbe | None:
        """Build metadata for a local media file; returns None if file is invalid."""
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None

        try:
            size = p.stat().st_size
        except OSError:
            size = None

        dur = AudioExtractor.probe_duration(p)

        return MediaProbe(
            source="LOCAL",
            title=p.stem,
            path=str(p),
            duration=float(dur) if dur is not None else None,
            size=int(size) if size is not None else None,
        )
