# app/model/core/domain/state.py
from __future__ import annotations

from dataclasses import dataclass, field

from app.model.core.domain.entities import SettingsSnapshot
from app.model.engines.types import EngineRuntimeState


@dataclass(frozen=True)
class AppRuntimeState:
    """Immutable runtime state resolved during application startup."""

    settings_snapshot: SettingsSnapshot | None = None
    transcription: EngineRuntimeState = field(default_factory=EngineRuntimeState)
    translation: EngineRuntimeState = field(default_factory=EngineRuntimeState)
