# core/transcription/model_loader.py
from __future__ import annotations

from typing import Callable, Any
import logging

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from core.config.app_config import AppConfig as Config


class ModelLoader:
    """Builds the ASR pipeline using AppConfig / settings.json."""

    def __init__(self) -> None:
        self.pipeline = None

    def load(self, log: Callable[[Any], None] | None = None) -> Any:
        """
        Build and return a transformers ASR pipeline.

        Respects model settings:
          - ai_engine_name
          - local_models_only
          - use_safetensors
          - low_cpu_mem_usage
        """
        logging.getLogger("transformers").setLevel(logging.ERROR)

        model_cfg = Config.model_settings()
        ai_name = str(model_cfg.get("ai_engine_name", "") or "").strip() or "unknown"
        low_cpu_mem_usage = bool(model_cfg.get("low_cpu_mem_usage", True))
        use_safetensors = bool(model_cfg.get("use_safetensors", True))
        local_models_only = bool(model_cfg.get("local_models_only", True))

        if log is not None:
            try:
                log(
                    f"Loading ASR model '{ai_name}' "
                    f"from '{Config.AI_ENGINE_DIR}' "
                    f"on {Config.DEVICE_FRIENDLY_NAME}..."
                )
            except Exception:
                pass

        try:
            torch.set_float32_matmul_precision("medium")
        except Exception:
            pass

        device_index = 0 if Config.DEVICE.type == "cuda" else -1

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(Config.AI_ENGINE_DIR),
            low_cpu_mem_usage=low_cpu_mem_usage,
            use_safetensors=use_safetensors,
            local_files_only=local_models_only,
        )
        processor = AutoProcessor.from_pretrained(
            str(Config.AI_ENGINE_DIR),
            local_files_only=local_models_only,
        )

        # Behaviour (timestamps, task, language) is controlled later
        # via generate_kwargs and return_timestamps in TranscriptionWorker.
        self.pipeline = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=device_index,
            dtype=None if device_index == -1 else Config.DTYPE,
        )
        return self.pipeline
