from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from core.config import Config

class ModelManager:
    def __init__(self):
        self._pipeline = None

    def load(self):
        try:
            device = Config.DEVICE
            dtype = Config.DTYPE
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                Config.MODEL_DIR.as_posix(),
                torch_dtype=dtype,
                low_cpu_mem_usage=True,
                use_safetensors=True,
                local_files_only=True
            ).to(device)
        except Exception as error:
            print(f"⚠️ Nie udało się załadować modelu na GPU ({error}), przełączam na CPU…")
            device = "cpu"
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                Config.MODEL_DIR.as_posix(),
                local_files_only=True
            ).to(device)

        processor = AutoProcessor.from_pretrained(
            Config.MODEL_DIR.as_posix(),
            local_files_only=True
        )

        self._pipeline = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=device,
            torch_dtype=None if device == "cpu" else dtype
        )

        return self._pipeline

    def get_pipeline(self):
        return self._pipeline
