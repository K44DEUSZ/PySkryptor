# app/model/services/model_resolution_service.py
from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.model.config.app_config import AppConfig as Config
from app.model.config.model_registry import ModelRegistry


class ModelResolutionService:
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

        model_dir = Config.PATHS.AI_MODELS_DIR / name
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
        if not Config.PATHS.AI_MODELS_DIR.exists() or not Config.PATHS.AI_MODELS_DIR.is_dir():
            return tuple()

        out: list[dict[str, Any]] = []
        for path in sorted(Config.PATHS.AI_MODELS_DIR.iterdir(), key=lambda item: item.name.lower()):
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
            return Config.MISSING_VALUE
        if low == "auto":
            pick = cls.autoselect_engine_name(task=task)
            return pick if pick else Config.MISSING_VALUE

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
        return Config.MISSING_VALUE

    @classmethod
    def active_engine_name(cls, *, task: str) -> str:
        task_id = str(task or "").strip().lower()
        engine_dir = (
            Config.PATHS.TRANSLATION_ENGINE_DIR
            if task_id == "translation"
            else Config.PATHS.TRANSCRIPTION_ENGINE_DIR
        )
        name = str(getattr(engine_dir, "name", "") or "").strip()
        if not name or name == Config.MISSING_VALUE:
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
