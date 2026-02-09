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
    """Runtime configuration error."""


class AppConfig:
    """Global runtime configuration and path mapping."""

    # Metadata
    APP_NAME: str = "PySkryptor"
    APP_VERSION: str = "0.1.0"
    APP_AUTHOR: str = "Bartosz Golat"
    APP_DEVELOPMENT_YEARS: str = "2025-2026"
    APP_REPO_URL: str = "https://github.com/K44DEUSZ/PySkryptor"

    # Paths
    ROOT_DIR: Path = Path(__file__).resolve().parents[2]

    APP_DIR: Path = ROOT_DIR / "app"
    LICENSE_FILE: Path = APP_DIR / "LICENSE.txt"

    ASSETS_DIR: Path = ROOT_DIR / "assets"
    RUNTIME_DIR: Path = ROOT_DIR / ".runtime"

    AI_MODELS_DIR: Path = ASSETS_DIR / "ai_models"

    LOCALES_DIR: Path = ASSETS_DIR / "locales"
    STYLES_DIR: Path = ASSETS_DIR / "styles"
    IMAGES_DIR: Path = ASSETS_DIR / "images"

    FFMPEG_DIR: Path = RUNTIME_DIR / "ffmpeg"
    FFMPEG_BIN_DIR: Path = FFMPEG_DIR

    TRANSCRIPTION_ENGINE_DIR: Path = AI_MODELS_DIR / "__missing__"
    TRANSLATION_ENGINE_DIR: Path = AI_MODELS_DIR / "__missing__"
    TRANSLATION_ENGINE_IDS: Tuple[str, ...] = ("m2m100",)

    DATA_DIR: Path = ROOT_DIR / "data"
    DOWNLOADS_DIR: Path = DATA_DIR / "downloads"
    TRANSCRIPTIONS_DIR: Path = DATA_DIR / "transcriptions"
    LOGS_DIR: Path = DATA_DIR / "logs"

    DOWNLOADS_TMP_DIR: Path = DOWNLOADS_DIR / "._tmp"
    TRANSCRIPTIONS_TMP_DIR: Path = TRANSCRIPTIONS_DIR / "._tmp"

    @classmethod
    def set_root_dir(cls, root_dir: Path) -> None:
        cls.ROOT_DIR = Path(root_dir).resolve()

        cls.APP_DIR = cls.ROOT_DIR / "app"
        cls.LICENSE_FILE = cls.APP_DIR / "LICENSE.txt"

        cls.ASSETS_DIR = cls.ROOT_DIR / "assets"
        cls.RUNTIME_DIR = cls.ROOT_DIR / ".runtime"

        cls.AI_MODELS_DIR = cls.ASSETS_DIR / "ai_models"

        cls.LOCALES_DIR = cls.ASSETS_DIR / "locales"
        cls.STYLES_DIR = cls.ASSETS_DIR / "styles"
        cls.IMAGES_DIR = cls.ASSETS_DIR / "images"

        cls.FFMPEG_DIR = cls.RUNTIME_DIR / "ffmpeg"
        cls.FFMPEG_BIN_DIR = cls.FFMPEG_DIR

        cls.TRANSCRIPTION_ENGINE_DIR = cls.AI_MODELS_DIR / "__missing__"
        cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / "__missing__"

        cls.DATA_DIR = cls.ROOT_DIR / "data"
        cls.DOWNLOADS_DIR = cls.DATA_DIR / "downloads"
        cls.TRANSCRIPTIONS_DIR = cls.DATA_DIR / "transcriptions"
        cls.LOGS_DIR = cls.DATA_DIR / "logs"

        cls.DOWNLOADS_TMP_DIR = cls.DOWNLOADS_DIR / "._tmp"
        cls.TRANSCRIPTIONS_TMP_DIR = cls.TRANSCRIPTIONS_DIR / "._tmp"

    # ----- Extensions -----
    AUDIO_EXTS: Tuple[str, ...] = ("wav", "mp3", "flac", "m4a", "ogg", "aac")
    VIDEO_EXTS: Tuple[str, ...] = ("mp4", "webm", "mkv", "mov", "avi")

    @classmethod
    def audio_file_exts(cls) -> Tuple[str, ...]:
        return tuple(f".{x}" for x in cls.AUDIO_EXTS)

    @classmethod
    def video_file_exts(cls) -> Tuple[str, ...]:
        return tuple(f".{x}" for x in cls.VIDEO_EXTS)

    # ----- Transcript output -----
    TRANSCRIPTION_OUTPUT_MODES: Tuple[Dict[str, Any], ...] = (
        {"id": "txt", "ext": "txt", "timestamps": False, "tr_key": "transcription.output_mode.plain_txt.label"},
        {"id": "txt_ts", "ext": "txt", "timestamps": True, "tr_key": "transcription.output_mode.txt_timestamps.label"},
        {"id": "srt", "ext": "srt", "timestamps": True, "tr_key": "transcription.output_mode.srt.label"},
    )
    TRANSCRIPT_DEFAULT_EXT: str = "txt"

    @classmethod
    def get_transcription_output_modes(cls) -> Tuple[Dict[str, Any], ...]:
        return cls.TRANSCRIPTION_OUTPUT_MODES

    @classmethod
    def get_transcription_output_mode(cls, mode_id: str) -> Dict[str, Any]:
        mid = str(mode_id or "txt").strip().lower()
        for mode in cls.TRANSCRIPTION_OUTPUT_MODES:
            if str(mode.get("id", "")).lower() == mid:
                return mode
        return cls.TRANSCRIPTION_OUTPUT_MODES[0]

    # ----- Defaults -----
    VIDEO_MIN_HEIGHT: int = 144
    VIDEO_MAX_HEIGHT: int = 4320

    NET_MAX_KBPS: int | None = None
    NET_RETRIES: int = 3
    NET_CONC_FRAG: int = 4
    NET_TIMEOUT_S: int = 30

    USE_SAFETENSORS: bool = True

    # Runtime
    DEVICE: torch.device = torch.device("cpu")
    DTYPE: Any = torch.float32
    DEVICE_FRIENDLY_NAME: str = "CPU"
    DEVICE_KIND: str = "CPU"
    DEVICE_MODEL: str | None = None
    TF32_ENABLED: bool = False
    TF32_SUPPORTED: bool = False

    SETTINGS: "SettingsSnapshot | None" = None

    # ----- Snapshot mapping -----
    @classmethod
    def initialize_from_snapshot(cls, snap: "SettingsSnapshot") -> None:
        cls.SETTINGS = snap
        cls._apply_transcription_engine_dir(snap.model)
        cls._apply_translation_engine_dir(snap.model)
        cls._apply_downloader(snap.downloader)
        cls._apply_network(snap.network)
        cls._apply_transcription(snap.transcription)
        cls._setup_device_dtype(snap.engine)

    @classmethod
    def update_from_snapshot(
        cls,
        snap: "SettingsSnapshot",
        *,
        sections: tuple[str, ...] = ("transcription", "translation"),
    ) -> None:
        cls.SETTINGS = snap
        want = set(sections or ())
        if "model" in want:
            cls._apply_transcription_engine_dir(snap.model)
            cls._apply_translation_engine_dir(snap.model)
        if "downloader" in want:
            cls._apply_downloader(snap.downloader)
        if "network" in want:
            cls._apply_network(snap.network)
        if "transcription" in want:
            cls._apply_transcription(snap.transcription)
        if "engine" in want:
            cls._setup_device_dtype(snap.engine)

    # ----- Startup -----
    @classmethod
    def ensure_dirs(cls) -> None:
        for p in (
            cls.RUNTIME_DIR,
            cls.FFMPEG_DIR,
            cls.AI_MODELS_DIR,
            cls.LOCALES_DIR,
            cls.STYLES_DIR,
            cls.IMAGES_DIR,
            cls.DOWNLOADS_DIR,
            cls.TRANSCRIPTIONS_DIR,
            cls.LOGS_DIR,
            cls.DOWNLOADS_TMP_DIR,
            cls.TRANSCRIPTIONS_TMP_DIR,
        ):
            p.mkdir(parents=True, exist_ok=True)

    @classmethod
    def setup_ffmpeg_on_path(cls) -> None:
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

    # ----- Apply sections -----
    @classmethod
    def _apply_downloader(cls, downloader: Dict[str, Any]) -> None:
        def _to_int(v: Any, default: int) -> int:
            try:
                return int(v)
            except Exception:
                return default

        mn = _to_int(downloader.get("min_video_height", cls.VIDEO_MIN_HEIGHT), cls.VIDEO_MIN_HEIGHT)
        mx = _to_int(downloader.get("max_video_height", cls.VIDEO_MAX_HEIGHT), cls.VIDEO_MAX_HEIGHT)
        if mx < mn:
            mx = mn
        cls.VIDEO_MIN_HEIGHT = max(1, mn)
        cls.VIDEO_MAX_HEIGHT = max(cls.VIDEO_MIN_HEIGHT, mx)

    @classmethod
    def _apply_network(cls, network: Dict[str, Any]) -> None:
        def _to_int(v: Any, default: int) -> int:
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
        cls.NET_RETRIES = max(0, _to_int(network.get("retries", cls.NET_RETRIES), cls.NET_RETRIES))
        cls.NET_CONC_FRAG = max(1, _to_int(network.get("concurrent_fragments", cls.NET_CONC_FRAG), cls.NET_CONC_FRAG))
        cls.NET_TIMEOUT_S = max(1, _to_int(network.get("http_timeout_s", cls.NET_TIMEOUT_S), cls.NET_TIMEOUT_S))

    @classmethod
    def _apply_transcription(cls, transcription: Dict[str, Any]) -> None:
        raw = transcription.get("output_formats")
        if isinstance(raw, str) and raw.strip():
            mode_id = raw.strip().lower()
        elif isinstance(raw, (list, tuple)) and raw:
            mode_id = str(raw[0] or "txt").strip().lower()
        else:
            mode_id = str(transcription.get("output_format", "txt") or "txt").strip().lower()

        mode = cls.get_transcription_output_mode(mode_id)
        cls.TRANSCRIPT_DEFAULT_EXT = str(mode.get("ext", "txt") or "txt").strip().lower().lstrip(".") or "txt"

    @classmethod
    def _apply_translation_engine_dir(cls, model: Dict[str, Any]) -> None:
        tcfg = model.get("translation_model", {})
        if not isinstance(tcfg, dict):
            tcfg = {}
        name = str(tcfg.get("engine_name", "auto") or "auto").strip().lower()

        if not name or name in ("none", "off", "disabled"):
            cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / "__missing__"
            return

        if name == "auto":
            name = cls._autoselect_translation_engine_name() or "__missing__"

        cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / name

    @classmethod
    def _apply_transcription_engine_dir(cls, model: Dict[str, Any]) -> None:
        tcfg = model.get("transcription_model", {})
        if not isinstance(tcfg, dict):
            tcfg = {}
        name = str(tcfg.get("engine_name", "auto") or "auto").strip().lower()
        if not name or name == "auto":
            name = cls._autoselect_transcription_engine_name() or "__missing__"
        cls.TRANSCRIPTION_ENGINE_DIR = cls.AI_MODELS_DIR / name

    @classmethod
    def _iter_local_model_dirs(cls) -> Tuple[str, ...]:
        if not cls.AI_MODELS_DIR.exists() or not cls.AI_MODELS_DIR.is_dir():
            return tuple()
        out: list[str] = []
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

    @classmethod
    def _autoselect_transcription_engine_name(cls) -> str:
        trans_ids = {x.lower() for x in cls.TRANSLATION_ENGINE_IDS}
        for name in cls._iter_local_model_dirs():
            if name.lower() not in trans_ids:
                return name
        return ""

    @classmethod
    def _autoselect_translation_engine_name(cls) -> str:
        trans_ids = {x.lower() for x in cls.TRANSLATION_ENGINE_IDS}
        for name in cls._iter_local_model_dirs():
            if name.lower() in trans_ids:
                return name
        return ""

    # ----- Device / dtype -----
    @staticmethod
    def _cpu_model_name() -> str | None:
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
                    out = subprocess.check_output(["wmic", "cpu", "get", "Name"], stderr=subprocess.DEVNULL, text=True)
                    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
                    lines = [ln for ln in lines if ln.lower() != "name"]
                    if lines:
                        return lines[0]
                except Exception:
                    pass

            if system in ("linux", "darwin"):
                try:
                    out = subprocess.check_output(
                        ["sysctl", "-n", "machdep.cpu.brand_string"],
                        stderr=subprocess.DEVNULL,
                        text=True,
                    ).strip()
                    if out:
                        return out
                except Exception:
                    pass
                try:
                    cpuinfo = Path("/proc/cpuinfo")
                    if cpuinfo.exists():
                        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                            if "model name" in line.lower():
                                name = line.split(":", 1)[-1].strip()
                                if name:
                                    return name
                except Exception:
                    pass
        except Exception:
            pass
        return None

    @classmethod
    def _resolve_device(cls, preferred: str) -> torch.device:
        pref = str(preferred or "auto").strip().lower()
        if pref == "cpu":
            return torch.device("cpu")
        if pref in ("cuda", "gpu"):
            return torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")

    @classmethod
    def _resolve_dtype(cls, preferred: str, device: torch.device) -> Any:
        pref = str(preferred or "auto").strip().lower()
        if device.type != "cuda":
            return torch.float32
        if pref in ("float16", "fp16", "half"):
            return torch.float16
        if pref in ("bfloat16", "bf16"):
            return torch.bfloat16
        if pref in ("float32", "fp32"):
            return torch.float32
        return torch.float16

    @classmethod
    def _setup_device_dtype(cls, user: Dict[str, Any]) -> None:
        try:
            pref_dev = user.get("preferred_device", "auto")
            pref_prec = user.get("precision", "auto")
            allow_tf32 = bool(user.get("allow_tf32", False))

            device = cls._resolve_device(str(pref_dev))
            dtype = cls._resolve_dtype(str(pref_prec), device)

            cls.DEVICE = device
            cls.DTYPE = dtype

            if device.type == "cuda" and torch.cuda.is_available():
                try:
                    props = torch.cuda.get_device_properties(0)
                    cls.DEVICE_KIND = "GPU"
                    cls.DEVICE_MODEL = getattr(props, "name", None)
                    cls.DEVICE_FRIENDLY_NAME = f"GPU ({cls.DEVICE_MODEL})" if cls.DEVICE_MODEL else "GPU"
                    major = int(getattr(props, "major", 0))
                    cls.TF32_SUPPORTED = major >= 8
                except Exception:
                    cls.DEVICE_KIND = "GPU"
                    cls.DEVICE_MODEL = None
                    cls.DEVICE_FRIENDLY_NAME = "GPU"
                    cls.TF32_SUPPORTED = False
            else:
                cls.DEVICE_KIND = "CPU"
                cls.DEVICE_MODEL = cls._cpu_model_name()
                cls.DEVICE_FRIENDLY_NAME = f"CPU ({cls.DEVICE_MODEL})" if cls.DEVICE_MODEL else "CPU"
                cls.TF32_SUPPORTED = False

            cls.TF32_ENABLED = bool(allow_tf32 and cls.TF32_SUPPORTED and device.type == "cuda" and dtype == torch.float32)

            if device.type == "cuda":
                torch.backends.cuda.matmul.allow_tf32 = cls.TF32_ENABLED
                torch.backends.cudnn.allow_tf32 = cls.TF32_ENABLED
                try:
                    torch.set_float32_matmul_precision("medium")
                except Exception:
                    pass
        except Exception as exc:
            raise ConfigError(str(exc)) from exc
