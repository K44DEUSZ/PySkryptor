from pathlib import Path
import os
import warnings
import torch
from transformers.utils import logging as hf_logging

class Config:
    BASE_DIR = Path(__file__).resolve().parent.parent
    MODEL_DIR = BASE_DIR / ".models" / "whisper-turbo"
    INPUT_DIR = BASE_DIR / "files" / "input"
    OUTPUT_DIR = BASE_DIR / "files" / "output"

    AUDIO_EXT = [
        ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac",
        ".wma", ".alac", ".aiff", ".opus", ".amr", ".mp2"
    ]

    VIDEO_EXT = [
        ".mp4", ".mkv", ".webm", ".mov", ".avi",
        ".flv", ".wmv", ".mpeg", ".mpg", ".m4v", ".3gp"
    ]

    LANGUAGE = "polish"

    _force_cpu = os.getenv("FORCE_CPU", "0") == "1"
    DEVICE = "cpu" if _force_cpu else ("cuda:0" if torch.cuda.is_available() else "cpu")
    DTYPE = torch.float32 if DEVICE == "cpu" else torch.float16

    @classmethod
    def initialize(cls) -> None:
        warnings.filterwarnings("ignore", category=FutureWarning)
        hf_logging.set_verbosity_error()

        if os.name == "nt":
            try:
                ffmpeg_dir = cls.BASE_DIR / ".ffmpeg" / "bin"
                os.add_dll_directory(str(ffmpeg_dir))
                os.environ["PATH"] = str(ffmpeg_dir) + os.pathsep + os.environ.get("PATH", "")
            except Exception:
                pass

        cls.INPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

Config.initialize()
