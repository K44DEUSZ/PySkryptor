# core/config/app_config.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Tuple

import torch

from core.services.settings_service import SettingsService, SettingsSnapshot, SettingsError


class ConfigError(RuntimeError):
    """App configuration error wrapping settings/device issues."""
    pass


class AppConfig:
    """
    Central runtime configuration (paths, binaries, device/dtype).
    Consumes SettingsService; does not read JSON directly.
    """

    # ---------- Derived paths (filled during initialize) ----------
    ROOT_DIR: Path = Path(__file__).resolve().parents[2]
    RESOURCES_DIR: Path = ROOT_DIR / "resources"
    FFMPEG_DIR: Path = RESOURCES_DIR / "ffmpeg"
    MODELS_DIR: Path = RESOURCES_DIR / "models"
    AI_ENGINE_DIR: Path = MODELS_DIR / "whisper-turbo"
    LOCALES_DIR: Path = RESOURCES_DIR / "locales"

    DATA_DIR: Path = ROOT_DIR / "data"
    DOWNLOADS_DIR: Path = DATA_DIR / "downloads"
    INPUT_TMP_DIR: Path = DATA_DIR / ".input_tmp"
    TRANSCRIPTIONS_DIR: Path = DATA_DIR / "transcriptions"
    FFMPEG_BIN_DIR: Path = FFMPEG_DIR

    # ---------- Media extensions ----------
    AUDIO_EXT: Tuple[str, ...] = (".wav", ".mp3")
    VIDEO_EXT: Tuple[str, ...] = (".mp4", ".webm")

    # ---------- Device/dtype/runtime ----------
    DEVICE: torch.device = torch.device("cpu")
    DTYPE: Any = torch.float32
    DEVICE_FRIENDLY_NAME: str = "CPU"
    TF32_ENABLED: bool = False

    # ---------- Cached settings ----------
    SETTINGS: SettingsSnapshot | None = None

    # ---------- Public initialization ----------
    @classmethod
    def initialize(cls, settings: SettingsService | None = None) -> None:
        ss = settings or SettingsService(cls.ROOT_DIR)
        try:
            snap = ss.load()
        except SettingsError as ex:
            raise ConfigError(str(ex)) from ex

        cls.SETTINGS = snap
        cls._apply_paths(snap.paths)
        cls._ensure_dirs()
        cls._setup_ffmpeg_on_path()
        cls._apply_media_exts(snap.media)
        cls._setup_device_dtype(user=snap.user)

    # ---------- Apply sections ----------
    @classmethod
    def _apply_paths(cls, paths: Dict[str, Any]) -> None:
        def _resolve(p: str) -> Path:
            path = Path(p)
            return path if path.is_absolute() else (cls.ROOT_DIR / path)

        cls.RESOURCES_DIR = _resolve(paths["resources_dir"])
        cls.FFMPEG_DIR = _resolve(paths["ffmpeg_dir"])
        cls.MODELS_DIR = _resolve(paths["models_dir"])
        cls.AI_ENGINE_DIR = _resolve(paths["ai_engine_dir"])
        cls.LOCALES_DIR = _resolve(paths["locales_dir"])

        cls.DATA_DIR = _resolve(paths["data_dir"])
        cls.DOWNLOADS_DIR = _resolve(paths["downloads_dir"])
        cls.INPUT_TMP_DIR = _resolve(paths["input_tmp_dir"])
        cls.TRANSCRIPTIONS_DIR = _resolve(paths["transcriptions_dir"])

    @classmethod
    def _apply_media_exts(cls, media: Dict[str, Any]) -> None:
        def _norm(exts: Any) -> Tuple[str, ...]:
            seq = list(exts or [])
            out = []
            for x in seq:
                s = str(x).lower()
                if not s.startswith("."):
                    s = "." + s
                out.append(s)
            return tuple(dict.fromkeys(out))
        cls.AUDIO_EXT = _norm(media.get("audio_ext"))
        cls.VIDEO_EXT = _norm(media.get("video_ext"))

    @classmethod
    def _ensure_dirs(cls) -> None:
        for p in (
            cls.RESOURCES_DIR,
            cls.FFMPEG_DIR,
            cls.MODELS_DIR,
            cls.AI_ENGINE_DIR,
            cls.DATA_DIR,
            cls.DOWNLOADS_DIR,
            cls.TRANSCRIPTIONS_DIR,
        ):
            p.mkdir(parents=True, exist_ok=True)
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _setup_ffmpeg_on_path(cls) -> None:
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

    # ---------- Device / DType ----------
    @classmethod
    def _setup_device_dtype(cls, *, user: Dict[str, Any]) -> None:
        device = cls._resolve_device(user)
        cls.DEVICE = device

        if device.type == "cuda":
            cls.DTYPE = cls._resolve_dtype(user, device)
            try:
                cls.DEVICE_FRIENDLY_NAME = torch.cuda.get_device_name(0)
            except Exception:
                cls.DEVICE_FRIENDLY_NAME = "CUDA"
        else:
            cls.DTYPE = torch.float32
            cls.DEVICE_FRIENDLY_NAME = "CPU"

        try:
            torch.set_float32_matmul_precision("medium")
        except Exception:
            pass

        allow_tf32 = bool(user.get("allow_tf32", True))
        if device.type == "cuda" and allow_tf32:
            try:
                torch.backends.cuda.matmul.allow_tf32 = True  # type: ignore[attr-defined]
                cls.TF32_ENABLED = True
            except Exception:
                cls.TF32_ENABLED = False
        else:
            try:
                torch.backends.cuda.matmul.allow_tf32 = False  # type: ignore[attr-defined]
            except Exception:
                pass
            cls.TF32_ENABLED = False

    @staticmethod
    def _resolve_device(user: Dict[str, Any]) -> torch.device:
        pref = str(user.get("preferred_device", "auto")).lower()
        if os.environ.get("FORCE_CPU", "0") == "1":
            return torch.device("cpu")
        if pref == "cpu":
            return torch.device("cpu")
        if pref == "gpu":
            return torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
        return torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")

    @staticmethod
    def _resolve_dtype(user: Dict[str, Any], device: torch.device):
        prec = str(user.get("precision", "auto")).lower()
        if device.type == "cuda":
            if prec == "float32":
                return torch.float32
            if prec == "float16":
                return torch.float16
            if prec == "bfloat16":
                return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        return torch.float32

    # ---------- Convenience accessors ----------
    @classmethod
    def language(cls) -> str:
        if cls.SETTINGS:
            return str(cls.SETTINGS.user.get("language", "en"))
        return "en"

    @classmethod
    def audio_extensions(cls) -> Tuple[str, ...]:
        return cls.AUDIO_EXT

    @classmethod
    def video_extensions(cls) -> Tuple[str, ...]:
        return cls.VIDEO_EXT

    @classmethod
    def model_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.model) if cls.SETTINGS else {}

    @classmethod
    def user_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.user) if cls.SETTINGS else {}
