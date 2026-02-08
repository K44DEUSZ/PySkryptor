# model/services/model_loader.py
from __future__ import annotations

import logging
from typing import Any, Callable

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from model.config.app_config import AppConfig as Config


class ModelLoader:
    """Builds the ASR pipeline using AppConfig / settings.json."""

    def __init__(self) -> None:
        self.pipeline: Any = None

    def load(self, log: Callable[[Any], None] | None = None) -> Any:
        """Create and return a transformers ASR pipeline."""
        logging.getLogger("transformers").setLevel(logging.ERROR)

        snap = Config.SETTINGS
        if snap is None:
            raise RuntimeError("error.runtime.settings_not_initialized")

        model_cfg = snap.model.get("transcription_model", {}) if isinstance(snap.model, dict) else {}
        engine_name = str(model_cfg.get("engine_name", "") or "unknown").strip() or "unknown"

        engine_cfg = snap.engine if isinstance(snap.engine, dict) else {}
        low_cpu_mem_usage = bool(engine_cfg.get("low_cpu_mem_usage", True))

        model_path = Config.TRANSCRIPTION_ENGINE_DIR
        use_safetensors = bool(Config.USE_SAFETENSORS)

        if log is not None:
            try:
                log(f"Loading ASR model '{engine_name}' from '{model_path}'")
            except Exception:
                pass

        device = getattr(Config, "DEVICE", torch.device("cpu"))
        dtype = getattr(Config, "DTYPE", torch.float32)

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_path,
            torch_dtype=dtype,
            low_cpu_mem_usage=low_cpu_mem_usage,
            use_safetensors=use_safetensors,
        )
        try:
            model = model.to(device)
        except Exception:
            model = model.to("cpu")

        processor = AutoProcessor.from_pretrained(model_path)

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
