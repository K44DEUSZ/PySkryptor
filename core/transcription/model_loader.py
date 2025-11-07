from __future__ import annotations

from typing import Callable

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from core.config.app_config import AppConfig as Config


class ModelLoader:
    def __init__(self) -> None:
        self.pipeline = None

    def load(self, log: Callable[[str], None] = print):
        Config.initialize()
        log("🟣 Inicjalizacja modelu…")

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

        mode = "GPU" if Config.DEVICE.type == "cuda" else "CPU"
        tf32 = "ON" if Config.TF32_ENABLED else "OFF"
        dtype_name = str(Config.DTYPE).split(".")[-1]
        log(f"🧠 Tryb: {mode} ({Config.DEVICE_FRIENDLY_NAME}), dtype={dtype_name}, TF32={tf32}")

        return self.pipeline
