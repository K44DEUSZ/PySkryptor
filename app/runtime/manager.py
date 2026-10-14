# app/runtime/manager.py
from __future__ import annotations

import logging
from typing import Any

from app.core.config.config import AppConfig
from app.core.domain.errors import AppError
from app.runtime.client import TranscriptionEngineClient, TranslationEngineClient
from app.runtime.contracts import TranscriptionEngineProtocol, TranslationEngineProtocol
from app.runtime.resolution import EngineCatalog
from app.runtime.types import EngineRuntimeState

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

  def close(self) -> None:
    for client in (self._translation, self._transcription):
      client.close()

  def warmup_role(self, role: str) -> EngineRuntimeState:
    if EngineCatalog.current_model_disabled(role):
      return EngineRuntimeState()

    model_dir = (
      AppConfig.PATHS.TRANSLATION_ENGINE_DIR if role == "translation" else AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR
    )
    if not model_dir.exists() or not model_dir.is_dir() or model_dir.name == AppConfig.MISSING_VALUE:
      error_key = "error.model.translation_missing" if role == "translation" else "error.model.transcription_missing"
      return EngineRuntimeState(False, error_key, {"path": str(model_dir)})

    client = self._translation if role == "translation" else self._transcription
    try:
      client.warmup()
      health = client.health()
      if not health.ready:
        raise AppError('error.model.not_ready', {})
      hardware = health.details.get('hardware', {})
      for key in (
        'DEVICE_ID',
        'DTYPE_ID',
        'DEVICE_KIND',
        'DEVICE_MODEL',
        'DEVICE_FRIENDLY_NAME',
        'HAS_CUDA',
        'BF16_SUPPORTED',
        'TF32_SUPPORTED',
        'TF32_ENABLED',
      ):
        if key in hardware:
          setattr(AppConfig, key, hardware[key])
    except AppError as ex:
      return EngineRuntimeState(False, str(ex.key), dict(ex.params or {}))
    return EngineRuntimeState(True, None, {}, dict(hardware))

  @staticmethod
  def settings_require_reload(previous: Any, current: Any) -> bool:
    prev_engine = getattr(previous, "engine", {}) if previous is not None else {}
    curr_engine = getattr(current, "engine", {}) if current is not None else {}
    prev_model = getattr(previous, "model", {}) if previous is not None else {}
    curr_model = getattr(current, "model", {}) if current is not None else {}
    if dict(prev_engine or {}) != dict(curr_engine or {}):
      return True
    for role in ('transcription_model', 'translation_model'):
      previous_selection = dict((prev_model or {}).get(role) or {})
      current_selection = dict((curr_model or {}).get(role) or {})
      for key in ('engine_name', 'engine_model_type', 'engine_signature'):
        if previous_selection.get(key) != current_selection.get(key):
          return True
    return False
