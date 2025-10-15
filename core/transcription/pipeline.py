# pyskryptor/core/transcription/pipeline.py
from __future__ import annotations

from typing import Optional, Callable

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from core.config import Config


class WhisperPipeline:
    """Creates a transformers Whisper pipeline with GPU-first settings."""

    def __init__(self, model_dir: Optional[str] = None) -> None:
        self.model_dir = str(model_dir or Config.AI_ENGINE_DIR)
        self._model = None
        self._processor = None
        self._pipe = None

    @property
    def pipeline(self):
        return self._pipe

    def build(self, log: Callable[[str], None]) -> None:
        mode = "GPU" if Config.DEVICE.type == "cuda" else "CPU"
        dtype_name = str(Config.DTYPE).split(".")[-1]
        tf32 = "ON" if Config.TF32_ENABLED else "OFF"
        dev_name = Config.DEVICE_FRIENDLY_NAME
        log(f"🧠 Tryb: {mode}{f' ({dev_name})' if mode == 'GPU' else ''}, dtype={dtype_name}, TF32={tf32}")

        try:
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                self.model_dir,
                torch_dtype=Config.DTYPE,
                low_cpu_mem_usage=True,
                use_safetensors=True,
                local_files_only=True,
            )
            self._processor = AutoProcessor.from_pretrained(
                self.model_dir,
                local_files_only=True,
                use_safetensors=True,
            )
        except Exception as e:
            log(f"❗ Nie udało się załadować modelu (preferowane ustawienia): {e}")
            log("🔁 Przełączam na tryb CPU fallback…")
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                self.model_dir,
                torch_dtype=torch.float32,
                low_cpu_mem_usage=True,
                use_safetensors=True,
                local_files_only=True,
            )
            self._processor = AutoProcessor.from_pretrained(
                self.model_dir,
                local_files_only=True,
                use_safetensors=True,
            )
            self._model.to(torch.device("cpu"))
            self._model.eval()
            self._pipe = pipeline(
                task="automatic-speech-recognition",
                model=self._model,
                tokenizer=getattr(self._processor, "tokenizer", self._processor),
                feature_extractor=getattr(self._processor, "feature_extractor", self._processor),
                device=-1,
                torch_dtype=None,
                ignore_warning=True,
                generate_kwargs={"task": "transcribe"},
            )
            log("✅ Model i pipeline gotowe.")
            return

        try:
            self._model.to(Config.DEVICE)
        except Exception:
            self._model.to(torch.device("cpu"))
            log("❗ Przeniesienie modelu na GPU nie powiodło się. Pracuję na CPU.")
        self._model.eval()

        device_for_pipe = 0 if Config.DEVICE.type == "cuda" else -1
        dtype_for_pipe = None if Config.DEVICE.type != "cuda" else Config.DTYPE

        self._pipe = pipeline(
            task="automatic-speech-recognition",
            model=self._model,
            tokenizer=getattr(self._processor, "tokenizer", self._processor),
            feature_extractor=getattr(self._processor, "feature_extractor", self._processor),
            device=device_for_pipe,
            torch_dtype=dtype_for_pipe,
            ignore_warning=True,
            generate_kwargs={"task": "transcribe"},
        )

        log("✅ Model i pipeline gotowe.")
