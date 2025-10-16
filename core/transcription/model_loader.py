# core/transcription/model_loader.py
from __future__ import annotations

from typing import Callable

from transformers import pipeline, AutoModelForSpeechSeq2Seq, AutoProcessor

from core.config.app_config import AppConfig as Config


class ModelLoader:
    """Loads ASR pipeline with proper device/dtype config."""

    def __init__(self) -> None:
        self.pipeline = None  # public attr expected by workers
        self.pipe = None      # backward-compat alias

    def load(self, log: Callable[[str], None]) -> None:
        log("🟣 Inicjalizacja modelu…")
        device_index = 0 if Config.DEVICE.type == "cuda" else -1

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(Config.AI_ENGINE_DIR),
            dtype=Config.DTYPE,                # new API (torch_dtype deprecated)
            low_cpu_mem_usage=True,
            use_safetensors=True,
            local_files_only=True,
        )
        processor = AutoProcessor.from_pretrained(str(Config.AI_ENGINE_DIR), local_files_only=True)
        model.to(Config.DEVICE)
        model.eval()

        self.pipeline = pipeline(
            task="automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=device_index,
            dtype=None if device_index == -1 else Config.DTYPE,
        )
        self.pipe = self.pipeline  # alias

        log("🟣 Model i pipeline gotowe.")
        mode = "GPU" if Config.DEVICE.type == "cuda" else "CPU"
        tf32 = "ON" if Config.TF32_ENABLED else "OFF"
        log(f"🧠 Tryb: {mode} ({Config.DEVICE_FRIENDLY_NAME}), dtype={Config.DTYPE.__repr__().split('.')[-1]}, TF32={tf32}")

    def get(self):
        return self.pipeline
