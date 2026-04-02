# app/model/engines/manager.py
from __future__ import annotations

import logging
from typing import Any

from app.model.core.config.config import AppConfig
from app.model.core.domain.errors import AppError
from app.model.engines.client import TranscriptionEngineClient, TranslationEngineClient
from app.model.engines.contracts import TranscriptionEngineProtocol, TranslationEngineProtocol
from app.model.engines.resolution import EngineCatalog
from app.model.engines.types import EngineRuntimeState

_LOG = logging.getLogger(__name__)


class EngineManager:
    """Central owner of long-lived engine-host clients."""

    def __init__(self) -> None:
        transcription = TranscriptionEngineClient()
        translation = TranslationEngineClient()
        assert isinstance(transcription, TranscriptionEngineProtocol)
        assert isinstance(translation, TranslationEngineProtocol)
        self._transcription: TranscriptionEngineProtocol = transcription
        self._translation: TranslationEngineProtocol = translation

    @property
    def transcription_engine(self) -> TranscriptionEngineProtocol:
        return self._transcription

    @property
    def translation_engine(self) -> TranslationEngineProtocol:
        return self._translation

    def shutdown(self) -> None:
        for client in (self._translation, self._transcription):
            try:
                client.shutdown()
            except (AppError, OSError, RuntimeError, TypeError, ValueError) as ex:
                _LOG.debug("Engine shutdown skipped. role=%s detail=%s", client.role, ex)

    def warmup_role(self, role: str) -> EngineRuntimeState:
        if EngineCatalog.current_model_disabled(role):
            return EngineRuntimeState()

        model_dir = (
            AppConfig.PATHS.TRANSLATION_ENGINE_DIR
            if role == "translation"
            else AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR
        )
        if not model_dir.exists() or not model_dir.is_dir() or model_dir.name == AppConfig.MISSING_VALUE:
            error_key = (
                "error.model.translation_missing"
                if role == "translation"
                else "error.model.transcription_missing"
            )
            return EngineRuntimeState(False, error_key, {"path": str(model_dir)})

        client = self._translation if role == "translation" else self._transcription
        try:
            client.warmup()
        except AppError as ex:
            return EngineRuntimeState(False, str(ex.key), dict(ex.params or {}))
        return EngineRuntimeState(True, None, {})

    @staticmethod
    def settings_require_reload(previous: Any, current: Any) -> bool:
        prev_engine = getattr(previous, "engine", {}) if previous is not None else {}
        curr_engine = getattr(current, "engine", {}) if current is not None else {}
        prev_model = getattr(previous, "model", {}) if previous is not None else {}
        curr_model = getattr(current, "model", {}) if current is not None else {}
        return dict(prev_engine or {}) != dict(curr_engine or {}) or dict(prev_model or {}) != dict(curr_model or {})
