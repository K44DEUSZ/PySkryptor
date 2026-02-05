# model/config/app_config.py
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Tuple, TYPE_CHECKING

import torch

if TYPE_CHECKING:
    from model.services.settings_service import SettingsSnapshot


class ConfigError(RuntimeError):
    """App configuration error wrapping runtime/device issues."""


class AppConfig:
    """Global application configuration and metadata."""

    # ----- Static app metadata (not user-configurable) -----

    APP_NAME: str = "PySkryptor"
    APP_VERSION: str = "0.1.0"
    APP_AUTHOR: str = "Bartosz Golat"

    APP_COPYRIGHT_RANGE: str = "2025–2026"

    APP_REPO_URL: str = "https://github.com/K44DEUSZ/PySkryptor"

    # ----- Paths (not user-configurable) -----

    ROOT_DIR: Path = Path(__file__).resolve().parents[2]

    APP_DIR: Path = ROOT_DIR / "app"
    LICENSE_FILE: Path = APP_DIR / "LICENSE.txt"

    ASSETS_DIR: Path = ROOT_DIR / "assets"
    RUNTIME_DIR: Path = ROOT_DIR / ".runtime"

    FFMPEG_DIR: Path = RUNTIME_DIR / "ffmpeg"
    AI_MODELS_DIR: Path = ASSETS_DIR / "ai_models"
    AI_ENGINE_DIR: Path = AI_MODELS_DIR / "__missing__"
    TRANSLATION_ENGINE_DIR: Path = AI_MODELS_DIR / "__missing__"

    TRANSLATION_ENGINE_IDS: Tuple[str, ...] = ("m2m100",)

    VIEW_RESOURCES_DIR: Path = ROOT_DIR / "view" / "resources"
    LOCALES_DIR: Path = VIEW_RESOURCES_DIR / "locales"
    STYLES_DIR: Path = VIEW_RESOURCES_DIR / "styles"

    IMAGES_DIR: Path = VIEW_RESOURCES_DIR / "images"
    APP_LOGO_SVG: Path = IMAGES_DIR / "logo.svg"

    DATA_DIR: Path = ROOT_DIR / "data"
    DOWNLOADS_DIR: Path = DATA_DIR / "downloads"
    TRANSCRIPTIONS_DIR: Path = DATA_DIR / "transcriptions"
    LOGS_DIR: Path = DATA_DIR / "logs"

    INPUT_TMP_DIR: Path = TRANSCRIPTIONS_DIR / ".input_tmp"
    FFMPEG_BIN_DIR: Path = FFMPEG_DIR

    @classmethod
    def set_root_dir(cls, root_dir: Path) -> None:
        """Set project root dir and recompute derived paths."""
        cls.ROOT_DIR = Path(root_dir).resolve()
        cls.APP_DIR = cls.ROOT_DIR / "app"
        cls.LICENSE_FILE = cls.APP_DIR / "LICENSE.txt"
        cls.ASSETS_DIR = cls.ROOT_DIR / "assets"
        cls.RUNTIME_DIR = cls.ROOT_DIR / ".runtime"

        cls.FFMPEG_DIR = cls.RUNTIME_DIR / "ffmpeg"
        cls.AI_MODELS_DIR = cls.ASSETS_DIR / "ai_models"
        cls.AI_ENGINE_DIR = cls.AI_MODELS_DIR / "__missing__"
        cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / "__missing__"
        cls.LOCALES_DIR = cls.ROOT_DIR / "view" / "resources" / "locales"
        cls.STYLES_DIR = cls.ROOT_DIR / "view" / "resources" / "styles"

        cls.IMAGES_DIR = cls.ROOT_DIR / "view" / "resources" / "images"
        cls.APP_LOGO_SVG = cls.IMAGES_DIR / "logo.svg"

        cls.DATA_DIR = cls.ROOT_DIR / "data"
        cls.DOWNLOADS_DIR = cls.DATA_DIR / "downloads"
        cls.TRANSCRIPTIONS_DIR = cls.DATA_DIR / "transcriptions"
        cls.LOGS_DIR = cls.DATA_DIR / "logs"
        cls.INPUT_TMP_DIR = cls.TRANSCRIPTIONS_DIR / ".input_tmp"

        # AI_ENGINE_DIR depends on model setting and is finalized in initialize_from_snapshot().

    # ----- Media extensions (not user-configurable) -----

    AUDIO_EXT: Tuple[str, ...] = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac")
    VIDEO_EXT: Tuple[str, ...] = (".mp4", ".webm", ".mkv", ".mov", ".avi")

    SUPPORTED_MEDIA_EXTS: Tuple[str, ...] = AUDIO_EXT + VIDEO_EXT

    DOWN_AUDIO_EXT: Tuple[str, ...] = ("m4a", "mp3")
    DOWN_VIDEO_EXT: Tuple[str, ...] = ("mp4", "webm")

    TRANSCRIPT_EXT: Tuple[str, ...] = ("txt", "srt")
    TRANSCRIPT_DEFAULT_EXT: str = "txt"

    # Output modes used by UI and services. Keep these as the single source of truth
    # for selectable transcript output formats.
    TRANSCRIPTION_OUTPUT_MODES: Tuple[Dict[str, Any], ...] = (
        {
            "id": "txt",
            "ext": "txt",
            "timestamps": False,
            "tr_key": "settings.transcription.output.plain_txt",
        },
        {
            "id": "txt_ts",
            "ext": "txt",
            "timestamps": True,
            "tr_key": "settings.transcription.output.txt_timestamps",
        },
        {
            "id": "srt",
            "ext": "srt",
            "timestamps": False,
            "tr_key": "settings.transcription.output.srt",
        },
    )

    @classmethod
    def get_transcription_output_modes(cls) -> Tuple[Dict[str, Any], ...]:
        """Return supported transcript output modes used by the UI."""
        return cls.TRANSCRIPTION_OUTPUT_MODES

    @classmethod
    def resolve_transcription_output_mode_id(cls, output_ext: str, timestamps_output: bool) -> str:
        """Resolve UI output mode id from settings fields (output_ext + timestamps_output)."""
        ext = str(output_ext or "txt").strip().lower()
        ts = bool(timestamps_output)
        for mode in cls.TRANSCRIPTION_OUTPUT_MODES:
            if str(mode.get("ext", "")).lower() == ext and bool(mode.get("timestamps")) == ts:
                return str(mode.get("id", "txt"))
        # Fallback to plain text
        return "txt"

    @classmethod
    def get_transcription_output_mode(cls, mode_id: str) -> Dict[str, Any]:
        """Get mode dict by id. Falls back to plain txt when unknown."""
        mid = str(mode_id or "txt").strip().lower()
        for mode in cls.TRANSCRIPTION_OUTPUT_MODES:
            if str(mode.get("id", "")).lower() == mid:
                return mode
        return cls.TRANSCRIPTION_OUTPUT_MODES[0]

    # ----- Downloader / network defaults -----

    VIDEO_MIN_HEIGHT: int = 144
    VIDEO_MAX_HEIGHT: int = 4320

    NET_MAX_KBPS: int | None = None
    NET_RETRIES: int = 3
    NET_CONC_FRAG: int = 4
    NET_TIMEOUT_S: int = 30

    # ----- Model loader defaults (not user-configurable) -----

    USE_SAFETENSORS: bool = True

    # ----- Device / dtype / runtime -----

    DEVICE: torch.device = torch.device("cpu")
    DTYPE: Any = torch.float32
    DEVICE_FRIENDLY_NAME: str = "CPU"
    DEVICE_KIND: str = "CPU"
    DEVICE_MODEL: str | None = None
    TF32_ENABLED: bool = False

    SETTINGS: "SettingsSnapshot | None" = None

    # ----- Initialization -----

    @classmethod
    def initialize_from_snapshot(cls, snap: "SettingsSnapshot") -> None:
        """
        Apply runtime configuration using an already-validated SettingsSnapshot.

        AppConfig is a runtime map (source of truth) used across the app.
        JSON parsing + validation lives in SettingsService.
        """
        cls.SETTINGS = snap

        cls._apply_model_dir(snap.model)
        cls._apply_translation_dir(snap.model)

        cls._ensure_dirs()
        cls._setup_ffmpeg_on_path()

        cls._apply_downloader(snap.downloader)
        cls._apply_network(snap.network)
        cls._apply_transcription(snap.transcription)
        cls._setup_device_dtype(user=snap.engine)

    @classmethod
    def update_from_snapshot(
        cls,
        snap: "SettingsSnapshot",
        *,
        sections: tuple[str, ...] = ("transcription", "translation"),
    ) -> None:
        """Update runtime config from an already-validated snapshot.

        This is a lightweight alternative to initialize_from_snapshot():
        it only reapplies the requested sections and avoids expensive
        setup steps (device probing, ffmpeg PATH, directory creation).
        """
        cls.SETTINGS = snap
        want = set(sections or ())
        if "model" in want:
            cls._apply_model_dir(snap.model)
        cls._apply_translation_dir(snap.model)
        if "downloader" in want:
            cls._apply_downloader(snap.downloader)
        if "network" in want:
            cls._apply_network(snap.network)
        if "transcription" in want:
            cls._apply_transcription(snap.transcription)
        if "engine" in want:
            cls._apply_translation_dir(snap.model)
            cls._setup_device_dtype(user=snap.engine)

    # ----- Apply sections from settings -----

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
        """Apply basic network options: retries, timeouts, bandwidth."""

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

    @classmethod
    def _apply_transcription(cls, transcription: Dict[str, Any]) -> None:
        # ----- Transcription -----
        raw = transcription.get("output_ext", cls.TRANSCRIPT_DEFAULT_EXT)
        ext = str(raw).lower().strip()
        if ext.startswith("."):
            ext = ext[1:]
        if ext:
            cls.TRANSCRIPT_DEFAULT_EXT = ext

    @classmethod
    def _apply_translation_dir(cls, model: Dict[str, Any]) -> None:
        trans = model.get("translation_model", {}) if isinstance(model.get("translation_model"), dict) else {}
        name = str(trans.get("engine_name", "auto") or "auto").strip().lower()

        if not name or name in ("none", "off", "disabled"):
            cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / "__missing__"
            return

        if name == "auto":
            name = cls._autoselect_translation_engine_name() or "__missing__"

        cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / name

    @classmethod
    def _apply_model_dir(cls, model: Dict[str, Any]) -> None:
        """Set AI_ENGINE_DIR from selected transcription model folder name in settings."""
        trans = model.get("transcription_model", {}) if isinstance(model.get("transcription_model"), dict) else {}
        name = str(trans.get("engine_name", "auto") or "auto").strip().lower()

        # Legacy fallbacks.
        if not name:
            name = str(model.get("asr_engine_name", "") or "").strip()
        if not name or name == "auto":
            name = cls._autoselect_transcription_engine_name() or "__missing__"

        cls.AI_ENGINE_DIR = cls.AI_MODELS_DIR / name

    @classmethod
    def _iter_local_model_dirs(cls) -> Tuple[str, ...]:
        try:
            if not cls.AI_MODELS_DIR.exists() or not cls.AI_MODELS_DIR.is_dir():
                return tuple()
            out = []
            for d in sorted(cls.AI_MODELS_DIR.iterdir(), key=lambda p: p.name.lower()):
                if not d.is_dir():
                    continue
                try:
                    if not any(d.iterdir()):
                        continue
                except Exception:
                    continue
                out.append(d.name)
            return tuple(out)
        except Exception:
            return tuple()

    @classmethod
    def _autoselect_transcription_engine_name(cls) -> str:
        for name in cls._iter_local_model_dirs():
            if name.lower() not in set(x.lower() for x in cls.TRANSLATION_ENGINE_IDS):
                return name
        return ""

    @classmethod
    def _autoselect_translation_engine_name(cls) -> str:
        for name in cls._iter_local_model_dirs():
            if name.lower() in set(x.lower() for x in cls.TRANSLATION_ENGINE_IDS):
                return name
        return ""

    # ----- Filesystem / ffmpeg -----

    @classmethod
    def _ensure_dirs(cls) -> None:
        """Create all required resource/data directories if missing."""
        for p in (
            cls.ASSETS_DIR,
            cls.RUNTIME_DIR,
            cls.FFMPEG_DIR,
            cls.AI_MODELS_DIR,
            cls.ROOT_DIR / "view" / "resources",
            cls.LOCALES_DIR,
            cls.STYLES_DIR,
            cls.DATA_DIR,
            cls.DOWNLOADS_DIR,
            cls.TRANSCRIPTIONS_DIR,
            cls.LOGS_DIR,
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
                torch.backends.cudnn.allow_tf32 = True
                cls.TF32_ENABLED = True
            except Exception:
                cls.TF32_ENABLED = False
        else:
            cls.TF32_ENABLED = False

    @staticmethod
    def _resolve_device(user: Dict[str, Any]) -> torch.device:
        """Resolve device preference: cpu/cuda/auto."""
        pref = str(user.get("device", "auto")).strip().lower()
        if pref in ("cpu", "cuda"):
            if pref == "cuda" and torch.cuda.is_available():
                return torch.device("cuda")
            return torch.device("cpu")

        # auto
        if torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")

    @staticmethod
    def _resolve_dtype(user: Dict[str, Any], device: torch.device) -> Any:
        """Resolve dtype preference for CUDA: float16/float32/bfloat16/auto."""
        pref = str(user.get("dtype", "auto")).strip().lower()

        if device.type != "cuda":
            return torch.float32

        if pref in ("float16", "fp16"):
            return torch.float16
        if pref in ("float32", "fp32"):
            return torch.float32
        if pref in ("bfloat16", "bf16"):
            return torch.bfloat16

        # auto: prefer float16
        return torch.float16

    # ----- Accessors (safe calls from UI / services) -----

    @classmethod
    def audio_extensions(cls) -> Tuple[str, ...]:
        return cls.AUDIO_EXT

    @classmethod
    def video_extensions(cls) -> Tuple[str, ...]:
        return cls.VIDEO_EXT

    @classmethod
    def supported_media_extensions(cls) -> Tuple[str, ...]:
        return cls.SUPPORTED_MEDIA_EXTS

    @classmethod
    def license_file_path(cls) -> Path:
        return cls.LICENSE_FILE

    @classmethod
    def downloader_audio_extensions(cls) -> Tuple[str, ...]:
        return cls.DOWN_AUDIO_EXT

    @classmethod
    def downloader_video_extensions(cls) -> Tuple[str, ...]:
        return cls.DOWN_VIDEO_EXT

    @classmethod
    def transcript_default_ext(cls) -> str:
        return cls.TRANSCRIPT_DEFAULT_EXT

    @classmethod
    def transcript_extensions(cls) -> Tuple[str, ...]:
        return cls.TRANSCRIPT_EXT

    @classmethod
    def model_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.model) if cls.SETTINGS else {}

    @classmethod
    def transcription_model_settings(cls) -> Dict[str, Any]:
        mdl = cls.model_settings()
        sub = mdl.get("transcription_model", {})
        return dict(sub) if isinstance(sub, dict) else {}

    @classmethod
    def translation_model_settings(cls) -> Dict[str, Any]:
        mdl = cls.model_settings()
        sub = mdl.get("translation_model", {})
        return dict(sub) if isinstance(sub, dict) else {}

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
    def translation_settings(cls) -> Dict[str, Any]:
        return dict(getattr(cls.SETTINGS, 'translation', {}) or {}) if cls.SETTINGS else {}

    @classmethod
    def downloader_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.downloader) if cls.SETTINGS else {}

    @classmethod
    def network_settings(cls) -> Dict[str, Any]:
        return dict(cls.SETTINGS.network) if cls.SETTINGS else {}

    @classmethod
    def translation_engine_dir(cls) -> Path:
        return cls.TRANSLATION_ENGINE_DIR

    @classmethod
    def translation_model_ref(cls) -> str:
        p = cls.TRANSLATION_ENGINE_DIR
        eng = str(cls.translation_model_settings().get("engine_name", "none") or "none").strip().lower()
        if eng in ("none", "", "off", "disabled"):
            return "none"

        try:
            if p and p.exists() and p.is_dir() and any(p.iterdir()):
                return str(p)
        except Exception:
            pass

        return eng

    @classmethod
    def min_video_height(cls) -> int:
        return int(cls.VIDEO_MIN_HEIGHT)

    @classmethod
    def max_video_height(cls) -> int:
        return int(cls.VIDEO_MAX_HEIGHT)

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