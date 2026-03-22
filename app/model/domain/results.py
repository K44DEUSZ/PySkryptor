# app/model/domain/results.py
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class SessionResult:
    """Outcome of a transcription session."""

    session_dir: str
    processed_any: bool
    had_errors: bool
    was_cancelled: bool

@dataclass(frozen=True)
class LiveUpdate:
    """Incremental update produced by live transcription."""

    detected_language: str
    display_source_text: str
    display_target_text: str
    archive_source_text: str
    archive_target_text: str
