# core/config/app_config.py
from __future__ import annotations

import os
from pathlib import Path
import torch


class AppConfig:
    """Global runtime config: device, dtype, TF32 + all project paths."""

    # Roots
    ROOT_DIR = Path.cwd()

    # resources/ (binaries + models)
    RESOURCES_DIR = ROOT_DIR / "resources"
    FFMPEG_DIR = RESOURCES_DIR / "ffmpeg"
    MODELS_DIR = RESOURCES_DIR / "models"
    AI_ENGINE_DIR = MODELS_DIR / "whisper-turbo"  # <- zgodnie z wytycznymi

    # data/ (user I/O only)
    DATA_DIR = ROOT_DIR / "data"
    DOWNLOADS_DIR = DATA_DIR / "downloads"
    INPUT_TMP_DIR = DATA_DIR / ".input_tmp"           # ukryty bufor plików
    TRANSCRIPTIONS_DIR = DATA_DIR / "transcriptions"  # docelowe wyniki

    # Resolved at runtime
    FFMPEG_BIN_DIR: Path = FFMPEG_DIR
    DEVICE: torch.device = torch.device("cpu")
    DTYPE = torch.float32
    DEVICE_FRIENDLY_NAME: str = "CPU"
    TF32_ENABLED: bool = False

    # Media extensions (wspólne miejsce)
    AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".alac", ".aiff", ".opus", ".amr", ".mp2"}
    VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".wmv", ".mpeg", ".mpg", ".m4v", ".3gp"}

    @classmethod
    def _ensure_paths(cls) -> None:
        cls.RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
        cls.FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        cls.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        cls.AI_ENGINE_DIR.mkdir(parents=True, exist_ok=True)

        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        cls.INPUT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        cls.TRANSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _setup_ffmpeg_on_path(cls) -> None:
        """Expose bundled ffmpeg/ffprobe to yt_dlp and subprocesses."""
        bin_dir = cls.FFMPEG_DIR / "bin"
        cls.FFMPEG_BIN_DIR = bin_dir if bin_dir.exists() else cls.FFMPEG_DIR

        bin_dir_str = str(cls.FFMPEG_BIN_DIR)
        env_path = os.environ.get("PATH", "")
        if bin_dir_str not in env_path.split(os.pathsep):
            os.environ["PATH"] = bin_dir_str + os.pathsep + env_path

        os.environ.setdefault("FFMPEG_LOCATION", bin_dir_str)

        exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        probe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        ffmpeg_exe = cls.FFMPEG_BIN_DIR / exe
        ffprobe_exe = cls.FFMPEG_BIN_DIR / probe
        if ffmpeg_exe.exists():
            os.environ.setdefault("FFMPEG_BINARY", str(ffmpeg_exe))
            os.environ.setdefault("IMAGEIO_FFMPEG_EXE", str(ffmpeg_exe))
        if ffprobe_exe.exists():
            os.environ.setdefault("FFPROBE_BINARY", str(ffprobe_exe))

    @classmethod
    def _setup_device_dtype(cls) -> None:
        force_cpu = os.environ.get("FORCE_CPU", "0") == "1"
        if force_cpu:
            cls.DEVICE = torch.device("cpu")
        else:
            cls.DEVICE = torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")

        if cls.DEVICE.type == "cuda":
            cls.DTYPE = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            try:
                cls.DEVICE_FRIENDLY_NAME = torch.cuda.get_device_name(0)
            except Exception:
                cls.DEVICE_FRIENDLY_NAME = "CUDA"
        else:
            cls.DTYPE = torch.float32
            cls.DEVICE_FRIENDLY_NAME = "CPU"

        torch.set_float32_matmul_precision("medium")
        if cls.DEVICE.type == "cuda":
            try:
                torch.backends.cuda.matmul.allow_tf32 = True  # type: ignore[attr-defined]
                cls.TF32_ENABLED = True
            except Exception:
                cls.TF32_ENABLED = False
        else:
            cls.TF32_ENABLED = False

    @classmethod
    def initialize(cls) -> None:
        cls._ensure_paths()
        cls._setup_ffmpeg_on_path()
        cls._setup_device_dtype()
