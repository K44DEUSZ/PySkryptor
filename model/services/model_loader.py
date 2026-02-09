# model/services/model_loader.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from model.config.app_config import AppConfig as Config


class ModelNotInstalledError(RuntimeError):
    """Raised when a required local model directory is missing."""


class ModelLoader:
    """Loads local AI models based on AppConfig and the current settings snapshot."""

    def __init__(self) -> None:
        self.pipeline: Any = None

    @staticmethod
    def _is_disabled_engine(name: str) -> bool:
        n = str(name or "").strip().lower()
        return (not n) or n in ("none", "off", "disabled")

    @staticmethod
    def _require_dir(path: Path, *, error_key: str) -> None:
        if path.exists() and path.is_dir() and path.name != "__missing__":
            return
        raise ModelNotInstalledError(f"{error_key}||{path}")

    def load_transcription(self, *, log: Optional[Callable[[Any], None]] = None) -> Any | None:
        """Create and return an ASR pipeline or None when disabled."""
        logging.getLogger("transformers").setLevel(logging.ERROR)

        snap = Config.SETTINGS
        if snap is None:
            raise RuntimeError("error.runtime.settings_not_initialized")

        model_cfg = snap.model.get("transcription_model", {}) if isinstance(snap.model, dict) else {}
        engine_name = str(model_cfg.get("engine_name", "none") or "none").strip()

        if self._is_disabled_engine(engine_name):
            self.pipeline = None
            return None

        model_path = Path(Config.TRANSCRIPTION_ENGINE_DIR)
        self._require_dir(model_path, error_key="error.model.transcription_missing")

        engine_cfg = snap.engine if isinstance(snap.engine, dict) else {}
        low_cpu_mem_usage = bool(engine_cfg.get("low_cpu_mem_usage", True))
        use_safetensors = bool(getattr(Config, "USE_SAFETENSORS", True))

        if log is not None:
            try:
                log(f"Loading ASR model '{engine_name}' from '{model_path}'")
            except Exception:
                pass

        device = getattr(Config, "DEVICE", torch.device("cpu"))
        dtype = getattr(Config, "DTYPE", torch.float32)

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(model_path),
            local_files_only=True,
            torch_dtype=dtype,
            low_cpu_mem_usage=low_cpu_mem_usage,
            use_safetensors=use_safetensors,
        )
        try:
            model = model.to(device)
        except Exception:
            model = model.to("cpu")

        processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)

        dev = getattr(model, "device", torch.device("cpu"))
        device_index = 0 if getattr(dev, "type", "cpu") == "cuda" else -1

        self.pipeline = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=device_index,
            torch_dtype=None if device_index == -1 else dtype,
        )
        return self.pipeline

    def warmup_translation(self, *, log: Optional[Callable[[str], None]] = None) -> bool:
        """Warm up the translation worker if enabled and installed."""
        snap = Config.SETTINGS
        if snap is None:
            raise RuntimeError("error.runtime.settings_not_initialized")

        model_cfg = snap.model.get("translation_model", {}) if isinstance(snap.model, dict) else {}
        engine_name = str(model_cfg.get("engine_name", "none") or "none").strip()

        if self._is_disabled_engine(engine_name):
            return False

        model_path = Path(Config.TRANSLATION_ENGINE_DIR)
        self._require_dir(model_path, error_key="error.model.translation_missing")

        # Delegate to TranslationService which encapsulates worker-process logic.
        from model.services.translation_service import TranslationService

        svc = TranslationService()
        return bool(svc.warmup(log=log))
