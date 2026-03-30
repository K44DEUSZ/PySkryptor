# app/model/core/domain/state.py
from __future__ import annotations

from dataclasses import dataclass, field

from app.model.core.domain.entities import SettingsSnapshot


@dataclass(frozen=True)
class AppRuntimeState:
    """Immutable runtime state resolved during application startup."""

    settings_snapshot: SettingsSnapshot | None = None
    transcription_pipeline: object | None = None
    transcription_ready: bool = False
    transcription_error_key: str | None = None
    transcription_error_params: dict[str, object] = field(default_factory=dict)
    translation_ready: bool = False
    translation_error_key: str | None = None
    translation_error_params: dict[str, object] = field(default_factory=dict)
