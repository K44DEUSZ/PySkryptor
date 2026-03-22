# app/model/domain/entities.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class SettingsSnapshot:
    """Immutable snapshot of validated application settings."""
    app: dict[str, Any]
    engine: dict[str, Any]
    model: dict[str, Any]
    transcription: dict[str, Any]
    translation: dict[str, Any]
    downloader: dict[str, Any]
    network: dict[str, Any]

@dataclass(frozen=True)
class TranscriptionSessionRequest:
    """Normalized request describing one transcription session and its outputs."""
    source_language: str
    target_language: str
    translate_after_transcription: bool
    output_formats: tuple[str, ...]
    download_audio_only: bool
    url_keep_audio: bool
    url_audio_ext: str
    url_keep_video: bool
    url_video_ext: str

@dataclass(frozen=True)
class PlaylistEntry:
    """Resolved single entry originating from a remote playlist."""
    entry_url: str
    title: str = ""
    duration_s: int | None = None
    uploader: str = ""
    position: int = 0

@dataclass(frozen=True)
class PlaylistResolveResult:
    """Resolved remote playlist ready to be expanded into queue items."""
    playlist_title: str
    playlist_url: str
    total_count: int
    entries: tuple[PlaylistEntry, ...]


def snapshot_to_dict(snap: SettingsSnapshot) -> dict[str, Any]:
    """Serialize a validated settings snapshot back to a plain dict."""

    return {
        "app": dict(snap.app),
        "engine": dict(snap.engine),
        "model": dict(snap.model),
        "transcription": dict(snap.transcription),
        "translation": dict(snap.translation),
        "downloader": dict(snap.downloader),
        "network": dict(snap.network),
    }
