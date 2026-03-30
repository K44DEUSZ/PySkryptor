# app/model/engines/registry.py
from __future__ import annotations

import hashlib
import json
from typing import Any


class ModelRegistry:
    """Static model identifiers, signatures and engine-name rules."""

    MODEL_CONFIG_FILE: str = "config.json"
    TRANSCRIPTION_MODEL_TYPES: tuple[str, ...] = ("whisper",)
    TRANSLATION_MODEL_TYPES: tuple[str, ...] = ("m2m_100",)
    DISABLED_ENGINE_NAMES: tuple[str, ...] = ("none", "off", "disabled")

    @classmethod
    def normalize_model_type(cls, model_type: Any) -> str:
        return str(model_type or "").strip().lower()

    @classmethod
    def task_for_model_type(cls, model_type: Any) -> str:
        norm = cls.normalize_model_type(model_type)
        if norm in cls.TRANSCRIPTION_MODEL_TYPES:
            return "transcription"
        if norm in cls.TRANSLATION_MODEL_TYPES:
            return "translation"
        return ""

    @classmethod
    def model_signature(cls, config_data: dict[str, Any]) -> str:
        if not isinstance(config_data, dict) or not config_data:
            return ""

        stable = {
            key: value
            for key, value in config_data.items()
            if str(key) not in ("_name_or_path", "transformers_version")
        }
        try:
            payload = json.dumps(stable, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return ""
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest().lower()

    @classmethod
    def is_disabled_engine_name(cls, name: str) -> bool:
        token = str(name or "").strip().lower()
        return (not token) or token in cls.DISABLED_ENGINE_NAMES
