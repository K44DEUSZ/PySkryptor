# app/model/sources/probe.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeAlias

from app.model.core.config.config import AppConfig
from app.model.core.domain.errors import AppError
from app.model.core.infrastructure.command_runner import CommandRunner
from app.model.core.runtime.ffmpeg import resolve_ffmpeg_tool
from app.model.download.domain import DownloadError
from app.model.download.policy import DownloadPolicy

_URL_RE = re.compile(r"^(?:https?://|ftp://)", re.IGNORECASE)


@dataclass(frozen=True)
class LocalMediaMetadata:
    """Resolved local-media metadata used by source probing."""

    path: Path
    duration: float | None
    size: int | None


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
    audio_tracks: list[dict[str, Any]] | None = None
    probe_diagnostics: dict[str, Any] | None = None

    def as_files_row(self) -> dict[str, Any]:
        """Row shape expected by Files table."""
        return {
            "name": self.title,
            "source": self.source,
            "path": self.path,
            "size": self.size,
            "duration": self.duration,
            "audio_tracks": self.audio_tracks or [],
            "probe_diagnostics": self.probe_diagnostics or {},
        }


UrlProbeFn: TypeAlias = Callable[..., dict[str, Any]]


class MediaProbeReader:
    """Central place for building MediaProbe from local files and URLs."""

    def __init__(self, probe_url: UrlProbeFn) -> None:
        self._probe_url = probe_url

    @staticmethod
    def _is_nonblocking_probe_error(ex: DownloadError) -> bool:
        """Return True for URL probe errors that should degrade into partial metadata."""
        key = str(ex.key or "").strip()
        return key in {
            "error.download.authentication_required",
            "error.download.browser_cookies_unavailable",
            "error.download.no_downloadable_formats",
            "error.download.extended_access_required",
        }

    @staticmethod
    def _fallback_url_probe(url: str, ex: DownloadError) -> MediaProbe:
        """Build a partial URL probe result when remote metadata access is blocked."""
        key = str(ex.key or "error.download.probe_failed")
        warning = "partial_metadata"
        if key == "error.download.authentication_required":
            warning = "authentication_required"
        elif key == "error.download.browser_cookies_unavailable":
            warning = "browser_cookies_unavailable"
        elif key == "error.download.no_downloadable_formats":
            warning = "no_public_formats"
        elif key == "error.download.extended_access_required":
            warning = "extended_access_required"
        details = {"detail": str(ex), "error_key": key}
        if warning == "extended_access_required":
            details["extractor_access_state"] = DownloadPolicy.EXTRACTOR_ACCESS_STATE_ENHANCED_REQUIRED
            details["extractor_action"] = DownloadPolicy.EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED
        return MediaProbe(
            source="URL",
            title=url,
            path=url,
            duration=None,
            size=None,
            service=None,
            formats=None,
            audio_tracks=None,
            probe_diagnostics={
                "warnings": [warning],
                "details": details,
            },
        )

    def from_url(
        self,
        url: str,
        *,
        interactive: bool = False,
        allow_degraded_probe: bool = True,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        access_mode_override: str | None = None,
    ) -> MediaProbe:
        """Probe remote media and normalize it into MediaProbe."""
        try:
            raw = self._probe_url(
                url,
                browser_cookies_mode_override=browser_cookies_mode_override,
                cookie_file_override=cookie_file_override,
                browser_policy_override=browser_policy_override,
                access_mode_override=access_mode_override,
                interactive=interactive,
            )
        except DownloadError as ex:
            if allow_degraded_probe and self._is_nonblocking_probe_error(ex):
                return self._fallback_url_probe(url, ex)
            raise
        size = raw.get("filesize") or raw.get("filesize_approx")
        dur = raw.get("duration")
        audio_tracks = raw.get("audio_tracks") or None
        return MediaProbe(
            source="URL",
            title=raw.get("title") or url,
            path=url,
            duration=float(dur) if dur is not None else None,
            size=int(size) if size is not None else None,
            service=raw.get("extractor_key") or raw.get("extractor"),
            formats=raw.get("formats") or [],
            audio_tracks=audio_tracks,
            probe_diagnostics=raw.get("probe_diagnostics") or None,
        )

    @staticmethod
    def from_local(path: Path) -> MediaProbe | None:
        """Build metadata for a local media file; returns None if file is invalid."""
        metadata = read_local_media_metadata(Path(path))
        if metadata is None:
            return None
        return MediaProbe(
            source="LOCAL",
            title=metadata.path.stem,
            path=str(metadata.path),
            duration=float(metadata.duration) if metadata.duration is not None else None,
            size=int(metadata.size) if metadata.size is not None else None,
        )


def is_url_source(value: str) -> bool:
    """Return True if value looks like a URL."""
    return bool(value) and bool(_URL_RE.match(value.strip()))


def probe_duration(path: Path) -> float | None:
    """Return media duration in seconds using ffprobe."""
    cmd = [
        resolve_ffmpeg_tool(AppConfig, "ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = CommandRunner.run(
            cmd,
            timeout_s=AppConfig.AUDIO_PROBE_TIMEOUT_S,
            error_key="error.audio.ffprobe_failed",
        )
        output = str(result.stdout or "").strip()
        return float(output) if output else None
    except (AppError, TypeError, ValueError):
        return None


def read_local_media_metadata(path: Path) -> LocalMediaMetadata | None:
    """Read local-media size and duration for supported source probing."""
    resolved_path = Path(path)
    if not resolved_path.exists() or not resolved_path.is_file():
        return None
    try:
        size = resolved_path.stat().st_size
    except OSError:
        size = None
    return LocalMediaMetadata(
        path=resolved_path,
        duration=probe_duration(resolved_path),
        size=int(size) if size is not None else None,
    )
