from __future__ import annotations

import os
import platform
import subprocess
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
    Uses SettingsService as the single source of truth.
    """

    # ----- Init settings -----

    # Derived paths (will be overridden by initialize() based on settings.json)
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

    # Media extensions (input = what we accept as sources)
    AUDIO_EXT: Tuple[str, ...] = (".wav", ".mp3")
    VIDEO_EXT: Tuple[str, ...] = (".mp4", ".webm")

    # Downloader output extensions (what we offer in download formats)
    DOWN_AUDIO_EXT: Tuple[str, ...] = ("m4a", "mp3")
    DOWN_VIDEO_EXT: Tuple[str, ...] = ("mp4", "webm")

    # Transcript formats
    TRANSCRIPT_DEFAULT_EXT: str = "txt"  # without leading dot
    TRANSCRIPT_EXT: Tuple[str, ...] = ("txt",)

    # Download prefs
    VIDEO_MIN_HEIGHT: int = 144
    VIDEO_MAX_HEIGHT: int = 4320

    # Network
    NET_MAX_KBPS: int | None = None
    NET_RETRIES: int = 3
    NET_CONC_FRAG: int = 4
    NET_TIMEOUT_S: int = 30
    NET_PROXY: str | None = None
    NET_THROTTLE_S: int = 0

    # Device/dtype/runtime
    DEVICE: torch.device = torch.device("cpu")
    DTYPE: Any = torch.float32
    DEVICE_FRIENDLY_NAME: str = "CPU"
    DEVICE_KIND: str = "CPU"
    DEVICE_MODEL: str | None = None
    TF32_ENABLED: bool = False

    # Cached settings snapshot
    SETTINGS: SettingsSnapshot | None = None

    # ----- Public initialization -----

    @classmethod
    def initialize(cls, settings: SettingsService | None = None) -> None:
        """Load settings and apply all runtime configuration."""
        ss = settings or SettingsService(cls.ROOT_DIR)
        try:
            snap = ss.load()
        except SettingsError as ex:
            raise ConfigError(str(ex)) from ex

        cls.SETTINGS = snap
        cls._apply_paths(snap.paths)
        cls._ensure_dirs()
        cls._setup_ffmpeg_on_path()
        cls._apply_media(snap.media)
        cls._apply_downloader(snap.downloader)
        cls._apply_network(snap.network)
        cls._setup_device_dtype(user=snap.engine)

    # ----- Apply sections -----

    @classmethod
    def _apply_paths(cls, paths: Dict[str, Any]) -> None:
        """Resolve configurable paths and store them as absolute."""

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
    def _apply_media(cls, media: Dict[str, Any]) -> None:
        """Normalize audio/video extensions and transcript formats from settings."""
        def _norm_with_dot(exts: Any) -> Tuple[str, ...]:
            seq = list(exts or [])
            out = []
            for x in seq:
                s = str(x).lower().strip()
                if not s:
                    continue
                if not s.startswith("."):
                    s = "." + s
                out.append(s)
            # de-duplicate preserving order
            return tuple(dict.fromkeys(out))

        def _norm_no_dot(exts: Any) -> Tuple[str, ...]:
            seq = list(exts or [])
            out = []
            for x in seq:
                s = str(x).lower().strip()
                if not s:
                    continue
                if s.startswith("."):
                    s = s[1:]
                out.append(s)
            return tuple(dict.fromkeys(out))

        input_cfg = media.get("input", {}) or {}
        dl_cfg = media.get("downloader", {}) or {}
        tr_cfg = media.get("transcripts", {}) or {}

        # Input (files the app accepts as sources)
        cls.AUDIO_EXT = _norm_with_dot(input_cfg.get("audio_ext"))
        cls.VIDEO_EXT = _norm_with_dot(input_cfg.get("video_ext"))

        # Downloader formats (output options; no leading dots)
        cls.DOWN_AUDIO_EXT = _norm_no_dot(dl_cfg.get("audio_ext"))
        cls.DOWN_VIDEO_EXT = _norm_no_dot(dl_cfg.get("video_ext"))

        # Transcript formats (no leading dots)
        default_ext = str(tr_cfg.get("default_ext") or "txt").lower().strip()
        if default_ext.startswith("."):
            default_ext = default_ext[1:]
        cls.TRANSCRIPT_DEFAULT_EXT = default_ext or "txt"
        cls.TRANSCRIPT_EXT = _norm_no_dot(tr_cfg.get("ext") or [cls.TRANSCRIPT_DEFAULT_EXT])

    @classmethod
    def _apply_downloader(cls, downloader: Dict[str, Any]) -> None:
        """Apply download min/max video height with basic clamping."""
        try:
            mn = int(downloader.get("min_video_height", cls.VIDEO_MIN_HEIGHT))
        except Exception:
            mn = cls.VIDEO_MIN_HEIGHT
        try:
            mx = int(downloader.get("max_video_height", cls.VIDEO_MAX_HEIGHT))
        except Exception:
            mx = cls.VIDEO_MAX_HEIGHT
        if mx < mn:
            mx = mn
        cls.VIDEO_MIN_HEIGHT = max(1, mn)
        cls.VIDEO_MAX_HEIGHT = max(cls.VIDEO_MIN_HEIGHT, mx)

    @classmethod
    def _apply_network(cls, network: Dict[str, Any]) -> None:
        """Apply basic network options: retries, timeouts, proxy, bandwidth."""
        def _to_int(v, default):
            try:
                return int(v)
            except Exception:
                return default

        kbps = network.get("max_bandwidth_kbps")
        try:
            kbps_val = int(kbps) if kbps is not None else None
        except Exception:
            kbps_val = None
        if kbps_val is not None and kbps_val <= 0:
            kbps_val = None

        cls.NET_MAX_KBPS = kbps_val
        cls.NET_RETRIES = _to_int(network.get("retries", cls.NET_RETRIES), cls.NET_RETRIES)
        cls.NET_CONC_FRAG = max(
            1,
            _to_int(network.get("concurrent_fragments", cls.NET_CONC_FRAG), cls.NET_CONC_FRAG),
        )
        cls.NET_TIMEOUT_S = max(
            1,
            _to_int(network.get("http_timeout_s", cls.NET_TIMEOUT_S), cls.NET_TIMEOUT_S),
        )
        proxy_raw = network.get("proxy")
        cls.NET_PROXY = (str(proxy_raw).strip() or None) if proxy_raw is not None else None
        cls.NET_THROTTLE_S = max(
            0,
            _to_int(network.get("throttle_startup_s", cls.NET_THROTTLE_S), cls.NET_THROTTLE_S),
        )

    @classmethod
    def _ensure_dirs(cls) -> None:
        """Create all required resource/data directories if missing."""
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

    @classmethod
    def _setup_ffmpeg_on_path(cls) -> None:
        """Expose ffmpeg/ffprobe via PATH and related environment variables."""
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

    # ----- Device / DType -----

    @staticmethod
    def _cpu_model_name() -> str | None:
        """Best-effort CPU model string for friendly display."""
        try:
            system = platform.system().lower()

            if system == "windows":
                # 1) Registry query – usually matches Settings → System → About.
                try:
                    out = subprocess.check_output(
                        [
                            "reg",
                            "query",
                            r"HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                            "/v",
                            "ProcessorNameString",
                        ],
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )
                    for line in out.splitlines():
                        if "ProcessorNameString" in line:
                            parts = [p for p in line.split("  ") if p.strip()]
                            if parts:
                                name = parts[-1].strip()
                                if name:
                                    return name
                except Exception:
                    pass

                # 2) Fallback: WMIC.
                try:
                    out = subprocess.check_output(
                        ["wmic", "cpu", "get", "Name"],
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )
                    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
                    lines = [ln for ln in lines if ln.lower() != "name"]
                    if lines:
                        return lines[0]
                except Exception:
                    pass

                # 3) Last resort.
                name = platform.processor() or ""
                if not name:
                    name = os.environ.get("PROCESSOR_IDENTIFIER", "") or ""
                return name or None

            if system == "linux":
                info = Path("/proc/cpuinfo")
                if info.exists():
                    for line in info.read_text(errors="ignore").splitlines():
                        if "model name" in line.lower():
                            return line.split(":", 1)[1].strip() or None
                return None

            if system == "darwin":
                out = subprocess.check_output(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                out = (out or "").strip()
                return out or None

        except Exception:
            return None

        return None

    @classmethod
    def _setup_device_dtype(cls, *, user: Dict[str, Any]) -> None:
        """Pick device and dtype according to engine preferences and hardware."""
        device = cls._resolve_device(user)
        cls.DEVICE = device

        if device.type == "cuda":
            cls.DTYPE = cls._resolve_dtype(user, device)
            cls.DEVICE_KIND = "GPU"
            try:
                cls.DEVICE_MODEL = torch.cuda.get_device_name(0)
            except Exception:
                cls.DEVICE_MODEL = None
        else:
            cls.DTYPE = torch.float32
            cls.DEVICE_KIND = "CPU"
            cls.DEVICE_MODEL = cls._cpu_model_name()

        if cls.DEVICE_MODEL:
            cls.DEVICE_FRIENDLY_NAME = f"{cls.DEVICE_KIND} ({cls.DEVICE_MODEL})"
        else:
            cls.DEVICE_FRIENDLY_NAME = cls.DEVICE_KIND

        try:
            torch.set_float32_matmul_precision("medium")
        except Exception:
            pass

        allow_tf32 = bool(user.get("allow_tf32", True))
        if device.type == "cuda" and allow_tf32:
            try:
                torch.backends.cuda.matmul.allow_tf32 = True
                cls.TF32_ENABLED = True
            except Exception:
                cls.TF32_ENABLED = False
        else:
            try:
                torch.backends.cuda.matmul.allow_tf32 = False
            except Exception:
                pass
            cls.TF32_ENABLED = False

    @staticmethod
    def _resolve_device(user: Dict[str, Any]) -> torch.device:
        """Resolve torch.device from user preference and availability."""
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
        """Resolve torch dtype for selected device and precision."""
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

    # ----- Convenience accessors -----

    @classmethod
    def language(cls) -> str:
        """
        Return current UI language code from settings (or 'en' as fallback).
        'auto' means 'let the Translator decide', so we fall back to 'en' here.
        """
        if cls.SETTINGS:
            lang = str(cls.SETTINGS.app.get("language", "auto"))
            return "en" if lang == "auto" else lang
        return "en"

    # Media extensions (input)
    @classmethod
    def audio_extensions(cls) -> Tuple[str, ...]:
        return cls.AUDIO_EXT

    @classmethod
    def video_extensions(cls) -> Tuple[str, ...]:
        return cls.VIDEO_EXT

    # Downloader extensions (output)
    @classmethod
    def downloader_audio_extensions(cls) -> Tuple[str, ...]:
        return cls.DOWN_AUDIO_EXT

    @classmethod
    def downloader_video_extensions(cls) -> Tuple[str, ...]:
        return cls.DOWN_VIDEO_EXT

    # Transcript formats
    @classmethod
    def transcript_default_ext(cls) -> str:
        return cls.TRANSCRIPT_DEFAULT_EXT

    @classmethod
    def transcript_extensions(cls) -> Tuple[str, ...]:
        return cls.TRANSCRIPT_EXT

    # Model / app / engine / transcription / downloader / network settings

    @classmethod
    def model_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.model) if cls.SETTINGS else {}

    @classmethod
    def app_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.app) if cls.SETTINGS else {}

    @classmethod
    def engine_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.engine) if cls.SETTINGS else {}

    @classmethod
    def transcription_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.transcription) if cls.SETTINGS else {}

    @classmethod
    def downloader_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.downloader) if cls.SETTINGS else {}

    @classmethod
    def network_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.network) if cls.SETTINGS else {}

    # Download prefs
    @classmethod
    def min_video_height(cls) -> int:
        return int(cls.VIDEO_MIN_HEIGHT)

    @classmethod
    def max_video_height(cls) -> int:
        return int(cls.VIDEO_MAX_HEIGHT)

    # Network
    @classmethod
    def net_max_kbps(cls) -> int | None:
        return cls.NET_MAX_KBPS

    @classmethod
    def net_retries(cls) -> int:
        return cls.NET_RETRIES

    @classmethod
    def net_concurrent_fragments(cls) -> int:
        return cls.NET_CONC_FRAG

    @classmethod
    def net_timeout_s(cls) -> int:
        return cls.NET_TIMEOUT_S

    @classmethod
    def net_proxy(cls) -> str | None:
        return cls.NET_PROXY

    @classmethod
    def net_throttle_startup_s(cls) -> int:
        return cls.NET_THROTTLE_S
