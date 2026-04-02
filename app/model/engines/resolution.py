# app/model/engines/resolution.py
from __future__ import annotations

import hashlib
import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.model.core.config.config import AppConfig


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


class EngineResolver:
    """Discovery and resolution helpers for locally installed AI models."""

    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError, TypeError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @classmethod
    def local_model_descriptor(cls, model_name: str) -> dict[str, Any]:
        name = str(model_name or "").strip()
        if not name or name.startswith("__"):
            return {}

        model_dir = AppConfig.PATHS.AI_MODELS_DIR / name
        if not model_dir.exists() or not model_dir.is_dir():
            return {}

        cfg_path = model_dir / ModelRegistry.MODEL_CONFIG_FILE
        if not cfg_path.exists() or not cfg_path.is_file():
            return {}

        cfg = cls._read_json_dict(cfg_path)
        model_type = ModelRegistry.normalize_model_type(cfg.get("model_type", ""))
        task = ModelRegistry.task_for_model_type(model_type)
        signature = ModelRegistry.model_signature(cfg)

        return {
            "name": model_dir.name,
            "path": model_dir,
            "config_path": cfg_path,
            "model_type": model_type,
            "task": task,
            "signature": signature,
        }

    @classmethod
    def local_model_descriptors(cls) -> tuple[dict[str, Any], ...]:
        if not AppConfig.PATHS.AI_MODELS_DIR.exists() or not AppConfig.PATHS.AI_MODELS_DIR.is_dir():
            return tuple()

        out: list[dict[str, Any]] = []
        for path in sorted(AppConfig.PATHS.AI_MODELS_DIR.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_dir() or path.name.startswith("__"):
                continue
            desc = cls.local_model_descriptor(path.name)
            if desc:
                out.append(desc)
        return tuple(out)

    @classmethod
    def local_models_for_task(cls, task: str) -> tuple[dict[str, Any], ...]:
        wanted = str(task or "").strip().lower()
        return tuple(desc for desc in cls.local_model_descriptors() if str(desc.get("task", "")) == wanted)

    @classmethod
    def local_model_names_for_task(cls, task: str) -> tuple[str, ...]:
        return tuple(str(desc.get("name", "")) for desc in cls.local_models_for_task(task) if desc.get("name"))

    @classmethod
    def autoselect_engine_name(cls, *, task: str) -> str:
        for desc in cls.local_models_for_task(task):
            name = str(desc.get("name", "")).strip()
            if name:
                return name
        return ""

    @classmethod
    def resolve_model_engine_name(cls, model_cfg: dict[str, Any], *, task: str) -> str:
        cfg = model_cfg if isinstance(model_cfg, dict) else {}
        raw = str(cfg.get("engine_name", "none") or "none").strip()
        low = raw.lower()

        if ModelRegistry.is_disabled_engine_name(low):
            return AppConfig.MISSING_VALUE
        if low == "auto":
            pick = cls.autoselect_engine_name(task=task)
            return pick if pick else AppConfig.MISSING_VALUE

        desc = cls.local_model_descriptor(raw)
        if desc and str(desc.get("task", "")) == str(task or "").strip().lower():
            return str(desc.get("name") or raw)

        sig = str(cfg.get("engine_signature", "") or "").strip().lower()
        model_type = ModelRegistry.normalize_model_type(cfg.get("engine_model_type", ""))
        matches: list[str] = []
        for cand in cls.local_models_for_task(task):
            cand_type = ModelRegistry.normalize_model_type(cand.get("model_type", ""))
            cand_sig = str(cand.get("signature", "") or "").strip().lower()
            if model_type and cand_type != model_type:
                continue
            if sig and cand_sig != sig:
                continue
            matches.append(str(cand.get("name", "")).strip())

        if sig and len(matches) == 1:
            return matches[0]
        return AppConfig.MISSING_VALUE

    @classmethod
    def active_engine_name(cls, *, task: str) -> str:
        task_id = str(task or "").strip().lower()
        engine_dir = (
            AppConfig.PATHS.TRANSLATION_ENGINE_DIR
            if task_id == "translation"
            else AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR
        )
        name = str(getattr(engine_dir, "name", "") or "").strip()
        if not name or name == AppConfig.MISSING_VALUE:
            return ""
        return name

    @classmethod
    def resolve_transcription_engine_name(cls, model: dict[str, Any]) -> str:
        cfg = model.get("transcription_model", {}) if isinstance(model, dict) else {}
        return cls.resolve_model_engine_name(cfg if isinstance(cfg, dict) else {}, task="transcription")

    @classmethod
    def resolve_translation_engine_name(cls, model: dict[str, Any]) -> str:
        cfg = model.get("translation_model", {}) if isinstance(model, dict) else {}
        return cls.resolve_model_engine_name(cfg if isinstance(cfg, dict) else {}, task="translation")


def _normalize_task(task: str) -> str:
    task_id = str(task or "").strip().lower()
    if task_id in ("transcription", "translation"):
        return task_id
    raise ValueError(f"Unsupported engine task: {task}")


class EngineCatalog:
    """Read-only helpers for resolved engine configuration and discovery."""

    @staticmethod
    def _raw_model_cfg(task: str) -> dict[str, Any]:
        if _normalize_task(task) == "translation":
            return AppConfig.translation_model_raw_cfg_dict()
        return AppConfig.transcription_model_raw_cfg_dict()

    @classmethod
    def current_model_cfg(cls, task: str) -> dict[str, Any]:
        task_id = _normalize_task(task)
        cfg = dict(cls._raw_model_cfg(task_id))
        engine_name = str(EngineResolver.active_engine_name(task=task_id) or cfg.get("engine_name", "") or "").strip()
        if not engine_name or engine_name == AppConfig.MISSING_VALUE:
            return cfg

        cfg["engine_name"] = engine_name
        desc = EngineResolver.local_model_descriptor(engine_name)
        if not desc:
            return cfg

        cfg["engine_model_type"] = str(desc.get("model_type", "") or "")
        cfg["engine_signature"] = str(desc.get("signature", "") or "")
        return cfg

    @classmethod
    def current_model_disabled(cls, task: str) -> bool:
        return cls.model_cfg_disabled(cls.current_model_cfg(task))

    @staticmethod
    def model_cfg_disabled(model_cfg: dict[str, Any] | None) -> bool:
        cfg = model_cfg if isinstance(model_cfg, dict) else {}
        return ModelRegistry.is_disabled_engine_name(str(cfg.get("engine_name", "") or ""))

    @staticmethod
    def local_model_names(task: str) -> tuple[str, ...]:
        return EngineResolver.local_model_names_for_task(_normalize_task(task))
