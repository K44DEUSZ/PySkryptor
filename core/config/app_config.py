# pyskryptor/core/config/app_config.py
from __future__ import annotations

import os
from pathlib import Path
import torch


class AppConfig:
    """Global runtime config: device, dtype, TF32, paths."""

    # Roots
    ROOT_DIR = Path.cwd()

    RESOURCES_DIR = ROOT_DIR / "resources"
    FFMPEG_DIR = RESOURCES_DIR / "ffmpeg"
    MODELS_DIR = RESOURCES_DIR / "models"
    AI_ENGINE_DIR = MODELS_DIR / "whisper-turbo"

    DATA_DIR = ROOT_DIR / "data"
    INPUT_DIR = DATA_DIR / "input"
    OUTPUT_DIR = DATA_DIR / "output"

    # Resolved at runtime
    FFMPEG_BIN_DIR: Path = FFMPEG_DIR
    DEVICE: torch.device = torch.device("cpu")
    DTYPE = torch.float32
    DEVICE_FRIENDLY_NAME: str = "CPU"
    TF32_ENABLED: bool = False

    @classmethod
    def _ensure_paths(cls) -> None:
        cls.RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
        cls.FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        cls.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        cls.AI_ENGINE_DIR.mkdir(parents=True, exist_ok=True)

        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.INPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _setup_ffmpeg_on_path(cls) -> None:
        """
        Expose bundled ffmpeg to yt_dlp/ffmpeg users.
        Supports both layouts:
        - resources/ffmpeg/{ffmpeg,ffprobe}
        - resources/ffmpeg/bin/{ffmpeg,ffprobe}
        """
        bin_dir = cls.FFMPEG_DIR / "bin"
        cls.FFMPEG_BIN_DIR = bin_dir if bin_dir.exists() else cls.FFMPEG_DIR

        bin_dir_str = str(cls.FFMPEG_BIN_DIR)
        path = os.environ.get("PATH", "")
        if bin_dir_str not in path.split(os.pathsep):
            os.environ["PATH"] = bin_dir_str + os.pathsep + path

        os.environ.setdefault("FFMPEG_LOCATION", bin_dir_str)

        exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        probe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        ffmpeg_exe = cls.FFMPEG_BIN_DIR / exe_name
        ffprobe_exe = cls.FFMPEG_BIN_DIR / probe_name

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
