# core/transcription/model_loader.py

from __future__ import annotations

from typing import Callable, Any
import torch

from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
from core.config.app_config import AppConfig as Config


class ModelLoader:
    def __init__(self) -> None:
        self.pipeline = None

    def load(self, log: Callable[[Any], None] | None = None):
        try:
            torch.set_float32_matmul_precision("medium")
        except Exception:
            pass

        device_index = 0 if Config.DEVICE.type == "cuda" else -1

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(Config.AI_ENGINE_DIR),
            low_cpu_mem_usage=True,
            use_safetensors=True,
            local_files_only=True,
        )
        processor = AutoProcessor.from_pretrained(str(Config.AI_ENGINE_DIR), local_files_only=True)

        self.pipeline = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=device_index,
            dtype=None if device_index == -1 else Config.DTYPE,
        )
        return self.pipeline
