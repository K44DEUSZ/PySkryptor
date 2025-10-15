# core/model_manager.py
# Ładowanie lokalnego modelu ASR (np. Whisper-turbo) i budowa pipeline z właściwym device/dtype.

from pathlib import Path
from typing import Optional, Callable

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from core.config import Config


class ModelManager:
    """
    Odpowiada za:
    - załadowanie modelu z lokalnego katalogu,
    - przeniesienie na odpowiednie urządzenie (GPU/CPU),
    - zbudowanie pipeline Transformers ASR.
    """

    def __init__(self, model_dir: Optional[Path] = None) -> None:
        self.model_dir = Path(model_dir) if model_dir else Config.WHISPER_TURBO_DIR
        self._model: Optional[torch.nn.Module] = None
        self._processor = None
        self._pipe = None

    def load(self, log: Callable[[str], None] = print) -> None:
        """
        Ładuje model i procesor lokalnie, tworzy pipeline.
        Loguje tryb (GPU/CPU), nazwę urządzenia, dtype i TF32.
        """
        # Log o trybie
        mode = "GPU" if Config.DEVICE.type == "cuda" else "CPU"
        dtype_name = str(Config.DTYPE).split(".")[-1]
        tf32 = "ON" if Config.TF32_ENABLED else "OFF"
        dev_name = Config.DEVICE_FRIENDLY_NAME

        log(f"🧠 Tryb: {mode}{f' ({dev_name})' if mode == 'GPU' else ''}, dtype={dtype_name}, TF32={tf32}")

        # Próba załadowania w preferowanej konfiguracji
        try:
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                str(self.model_dir),
                torch_dtype=Config.DTYPE,
                low_cpu_mem_usage=True,
                use_safetensors=True,
                local_files_only=True,
            )
            self._processor = AutoProcessor.from_pretrained(
                str(self.model_dir),
                local_files_only=True,
                use_safetensors=True,
            )
        except Exception as e:
            # Fallback do CPU (lokalny)
            log(f"❗ Nie udało się załadować modelu lokalnie z ustawieniami GPU/DTYPE ({e}).")
            log("🔁 Przełączam na tryb CPU fallback…")
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                str(self.model_dir),
                torch_dtype=torch.float32,  # CPU
                low_cpu_mem_usage=True,
                use_safetensors=True,
                local_files_only=True,
            )
            self._processor = AutoProcessor.from_pretrained(
                str(self.model_dir),
                local_files_only=True,
                use_safetensors=True,
            )

            # Wersja CPU
            self._model.to(torch.device("cpu"))
            self._model.eval()

            self._pipe = pipeline(
                task="automatic-speech-recognition",
                model=self._model,
                tokenizer=getattr(self._processor, "tokenizer", self._processor),
                feature_extractor=getattr(self._processor, "feature_extractor", self._processor),
                device=-1,           # -1 = CPU
                torch_dtype=None,    # na CPU nie wymuszamy dtype w pipeline
                ignore_warning=True,  # wycisz ostrzeżenia dot. chunków
                generate_kwargs={"task": "transcribe"},
            )
            log("✅ Model i pipeline gotowe.")
            return

        # Przeniesienie modelu na urządzenie
        try:
            self._model.to(Config.DEVICE)
        except Exception:
            # awaryjnie CPU
            self._model.to(torch.device("cpu"))
            log("❗ Przeniesienie modelu na GPU nie powiodło się. Pracuję na CPU.")
        self._model.eval()

        # Ustal device index dla pipeline: 0 dla GPU, -1 dla CPU
        device_for_pipe = 0 if Config.DEVICE.type == "cuda" else -1
        dtype_for_pipe = None if Config.DEVICE.type != "cuda" else Config.DTYPE

        self._pipe = pipeline(
            task="automatic-speech-recognition",
            model=self._model,
            tokenizer=getattr(self._processor, "tokenizer", self._processor),
            feature_extractor=getattr(self._processor, "feature_extractor", self._processor),
            device=device_for_pipe,
            torch_dtype=dtype_for_pipe,
            ignore_warning=True,              # nie pokazuj ostrzeżeń o chunk_length_s itp.
            generate_kwargs={"task": "transcribe"},
        )

        log("✅ Model i pipeline gotowe.")

    @property
    def pipe(self):
        return self._pipe
