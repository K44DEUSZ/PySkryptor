# core/transcription/model_loader.py
from __future__ import annotations

from typing import Callable, Any
import torch

from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
from core.config.app_config import AppConfig as Config


class ModelLoader:
    """Builds and exposes the ASR pipeline (transformers Whisper)."""

    def __init__(self) -> None:
        self.pipeline: Any | None = None

    def load(self, log: Callable[[Any], None]) -> Any:
        # Config (paths/device/dtype) already resolved from settings.json
        Config.initialize()

        log({"key": "log.model.init"})

        try:
            torch.set_float32_matmul_precision("medium")
        except Exception:
            pass

        device_index = 0 if Config.DEVICE.type == "cuda" else -1
        ms = Config.model_settings()
        local_only = bool(ms.get("local_models_only", True))

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(Config.AI_ENGINE_DIR),
            low_cpu_mem_usage=bool(ms.get("low_cpu_mem_usage", True)),
            use_safetensors=bool(ms.get("use_safetensors", True)),
            local_files_only=local_only,
        )
        processor = AutoProcessor.from_pretrained(
            str(Config.AI_ENGINE_DIR),
            local_files_only=local_only,
        )

        self.pipeline = pipeline(
            task=str(ms.get("pipeline_task", "automatic-speech-recognition")),
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=device_index,
            dtype=None if device_index == -1 else Config.DTYPE,
        )

        mode = "GPU" if Config.DEVICE.type == "cuda" else "CPU"
        dtype_name = str(Config.DTYPE).split(".")[-1]
        log({
            "key": "log.model.ready",
            "params": {
                "mode": mode,
                "device": Config.DEVICE_FRIENDLY_NAME,
                "dtype": dtype_name,
                "tf32": "ON" if Config.TF32_ENABLED else "OFF",
            },
        })

        return self.pipeline
