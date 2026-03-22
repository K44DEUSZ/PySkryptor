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


@dataclass(frozen=True)
class ExpandedSourceItem:
    """Single normalized source item produced by source expansion."""

    key: str
    source_kind: str
    title: str = ""
    duration_s: int | None = None

@dataclass(frozen=True)
class SourceExpansionResult:
    """Normalized outcome of expanding one user action into queueable sources."""

    origin_kind: str
    origin_label: str
    discovered_count: int
    items: tuple[ExpandedSourceItem, ...]
