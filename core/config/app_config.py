# core/config/app_config.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple, List

import torch


class AppConfig:
    """
    Global runtime configuration and paths.

    - Fixed paths/resources live in code.
    - Defaults live in JSON (defaults.json) as a backup/factory profile.
    - User settings live in JSON (settings.json) and override only the *user* section.
    - Effective configuration = defaults[user] patched by settings[user]  +  defaults[system] (read-only).
    """

    # ---------- Fixed paths (not user-editable) ----------
    ROOT_DIR: Path = Path.cwd()

    RESOURCES_DIR: Path = ROOT_DIR / "resources"
    FFMPEG_DIR: Path = RESOURCES_DIR / "ffmpeg"
    MODELS_DIR: Path = RESOURCES_DIR / "models"
    AI_ENGINE_DIR: Path = MODELS_DIR / "whisper-turbo"  # local AI engine dir

    DATA_DIR: Path = ROOT_DIR / "data"
    DOWNLOADS_DIR: Path = DATA_DIR / "downloads"
    INPUT_TMP_DIR: Path = DATA_DIR / ".input_tmp"           # created/cleaned per run by workers
    TRANSCRIPTIONS_DIR: Path = DATA_DIR / "transcriptions"

    FFMPEG_BIN_DIR: Path = FFMPEG_DIR  # resolved at runtime to ffmpeg/bin if present

    # Supported extensions (single source of truth; not user-editable)
    AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".alac", ".aiff", ".opus", ".amr", ".mp2"}
    VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".wmv", ".mpeg", ".mpg", ".m4v", ".3gp"}

    # ---------- Config files ----------
    _CFG_DIR: Path = ROOT_DIR / "core" / "config"
    _SETTINGS_PATH: Path = _CFG_DIR / "settings.json"   # user profile
    _DEFAULTS_PATH: Path = _CFG_DIR / "defaults.json"   # factory backup

    # Built-in last-resort defaults (used only if defaults.json is missing/corrupted)
    _BUILTIN_DEFAULTS: Dict[str, Dict[str, Any]] = {
        "user": {
            "language": "pl",                # "pl" | "en"
            "preferred_device": "auto",      # "auto" | "cpu" | "gpu"
            "precision": "auto",             # "auto" | "float32" | "float16" | "bfloat16"
            "allow_tf32": True,
            "timestamps_output": True,       # output with timestamps or single block
            "keep_downloaded_files": True,   # keep media after URL transcription
            "keep_wav_temp": False           # keep extracted wav files in temp
        },
        "system": {
            "ai_engine_name": "whisper-turbo",
            "local_models_only": True,
            "chunk_length_s": 60,
            "stride_length_s": 5
        }
    }

    # Simple schema for validation
    _USER_SCHEMA = {
        "language": ("pl", "en"),
        "preferred_device": ("auto", "cpu", "gpu"),
        "precision": ("auto", "float32", "float16", "bfloat16"),
        "allow_tf32": (bool,),
        "timestamps_output": (bool,),
        "keep_downloaded_files": (bool,),
        "keep_wav_temp": (bool,),
    }
    _SYSTEM_SCHEMA = {
        "ai_engine_name": (str,),
        "local_models_only": (bool,),
        "chunk_length_s": (int,),
        "stride_length_s": (int,),
    }

    # Loaded raw and effective maps
    DEFAULTS_RAW: Dict[str, Any] = {}
    SETTINGS_USER_RAW: Dict[str, Any] = {}
    USER_EFFECTIVE: Dict[str, Any] = {}
    SYSTEM_EFFECTIVE: Dict[str, Any] = {}
    SETTINGS_ISSUES: List[str] = []

    # Derived runtime
    DEVICE: torch.device = torch.device("cpu")
    DTYPE = torch.float32
    DEVICE_FRIENDLY_NAME: str = "CPU"
    TF32_ENABLED: bool = False

    # ---------- Low-level I/O ----------
    @classmethod
    def _read_json(cls, path: Path) -> Dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @classmethod
    def _write_json(cls, path: Path, data: Dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- Validation / merge ----------
    @classmethod
    def _validate_user(cls, raw: Dict[str, Any], base: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        issues: List[str] = []
        out: Dict[str, Any] = dict(base)
        for k, base_val in base.items():
            if k not in raw:
                issues.append(f"user.{k}: missing -> default '{base_val}'")
                continue
            v = raw[k]
            allowed = cls._USER_SCHEMA.get(k)
            if allowed is None:
                out[k] = base_val
                issues.append(f"user.{k}: unknown -> default '{base_val}'")
                continue
            if allowed == (bool,):
                if isinstance(v, bool):
                    out[k] = v
                elif isinstance(v, str):
                    out[k] = v.strip().lower() in ("1", "true", "yes", "y", "on")
                else:
                    out[k] = base_val
                    issues.append(f"user.{k}: invalid type -> default '{base_val}'")
            else:
                v_str = str(v).lower()
                if v_str in allowed:
                    out[k] = v_str
                else:
                    out[k] = base_val
                    issues.append(f"user.{k}: invalid value '{v}' -> default '{base_val}'")
        return out, issues

    @classmethod
    def _validate_system(cls, raw: Dict[str, Any], base: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        issues: List[str] = []
        out: Dict[str, Any] = dict(base)
        for k, base_val in base.items():
            v = raw.get(k, base_val)
            schema = cls._SYSTEM_SCHEMA.get(k, (type(base_val),))
            typ = schema[0]
            if typ is bool:
                if isinstance(v, bool):
                    out[k] = v
                elif isinstance(v, str):
                    out[k] = v.strip().lower() in ("1", "true", "yes", "y", "on")
                else:
                    out[k] = base_val
                    issues.append(f"system.{k}: invalid type -> default '{base_val}'")
            elif typ is int:
                try:
                    out[k] = int(v)
                except Exception:
                    out[k] = base_val
                    issues.append(f"system.{k}: invalid int -> default '{base_val}'")
            elif typ is str:
                out[k] = str(v)
            else:
                out[k] = base_val
        return out, issues

    # ---------- Paths / binaries ----------
    @classmethod
    def _ensure_paths(cls) -> None:
        cls.RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
        cls.FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        cls.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        cls.AI_ENGINE_DIR.mkdir(parents=True, exist_ok=True)

        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        cls.TRANSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)

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

    # ---------- Device / dtype ----------
    @classmethod
    def _resolve_device(cls) -> torch.device:
        pref = str(cls.USER_EFFECTIVE.get("preferred_device", "auto")).lower()
        if os.environ.get("FORCE_CPU", "0") == "1":
            return torch.device("cpu")
        if pref == "cpu":
            return torch.device("cpu")
        if pref == "gpu":
            return torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
        return torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")

    @classmethod
    def _resolve_dtype(cls, device: torch.device):
        pref = str(cls.USER_EFFECTIVE.get("precision", "auto")).lower()
        if device.type == "cuda":
            if pref == "float32":
                return torch.float32
            if pref == "float16":
                return torch.float16
            if pref == "bfloat16":
                return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        return torch.float32

    @classmethod
    def _setup_device_dtype(cls) -> None:
        cls.DEVICE = cls._resolve_device()

        if cls.DEVICE.type == "cuda":
            cls.DTYPE = cls._resolve_dtype(cls.DEVICE)
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

        allow_tf32 = bool(cls.USER_EFFECTIVE.get("allow_tf32", True))
        if cls.DEVICE.type == "cuda" and allow_tf32:
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

    # ---------- Public entry point ----------
    @classmethod
    def initialize(cls) -> None:
        """
        Idempotent.
        1) Ensure required folders exist.
        2) Load defaults.json (or use built-in backup), then load settings.json.
        3) Build effective maps: USER = defaults[user] patched by settings[user]; SYSTEM = defaults[system].
        4) Expose ffmpeg and setup device/dtype/TF32 based on effective user prefs.
        """
        cls._ensure_paths()

        # Load defaults (factory)
        defaults = cls._read_json(cls._DEFAULTS_PATH)
        if not defaults:
            # write built-in defaults to defaults.json on first run
            cls._write_json(cls._DEFAULTS_PATH, cls._BUILTIN_DEFAULTS)
            defaults = dict(cls._BUILTIN_DEFAULTS)
        cls.DEFAULTS_RAW = defaults

        # Load user settings (do not create/overwrite silently)
        user_raw = cls._read_json(cls._SETTINGS_PATH)
        if not user_raw:
            # create initial settings from built-in defaults' user section
            self_seed = {"user": dict(cls._BUILTIN_DEFAULTS["user"])}
            cls._write_json(cls._SETTINGS_PATH, self_seed)
            user_raw = self_seed
        cls.SETTINGS_USER_RAW = user_raw

        # Validate/merge (user overrides only the "user" part)
        base_user = dict(defaults.get("user", cls._BUILTIN_DEFAULTS["user"]))
        base_system = dict(defaults.get("system", cls._BUILTIN_DEFAULTS["system"]))

        usr_effective, usr_issues = cls._validate_user(user_raw.get("user", {}), base_user)
        sys_effective, sys_issues = cls._validate_system(defaults.get("system", {}), base_system)

        cls.USER_EFFECTIVE = usr_effective
        cls.SYSTEM_EFFECTIVE = sys_effective
        cls.SETTINGS_ISSUES = [*usr_issues, *sys_issues]

        cls._setup_ffmpeg_on_path()
        cls._setup_device_dtype()

    # ---------- Public helpers / accessors ----------
    @classmethod
    def save_settings(cls) -> None:
        """
        Persist only the 'user' section (system is factory-controlled via defaults.json).
        """
        data = {"user": dict(cls.USER_EFFECTIVE)}
        cls._write_json(cls._SETTINGS_PATH, data)

    @classmethod
    def reset_to_defaults(cls, write: bool = True) -> None:
        """
        Reset settings.json to factory defaults (user section only).
        """
        defaults = cls._read_json(cls._DEFAULTS_PATH) or dict(cls._BUILTIN_DEFAULTS)
        data = {"user": dict(defaults.get("user", cls._BUILTIN_DEFAULTS["user"]))}
        if write:
            cls._write_json(cls._SETTINGS_PATH, data)
        cls.USER_EFFECTIVE = data["user"]

    # User-facing prefs
    @classmethod
    def language(cls) -> str:
        return str(cls.USER_EFFECTIVE.get("language", "pl"))

    @classmethod
    def timestamps_output(cls) -> bool:
        return bool(cls.USER_EFFECTIVE.get("timestamps_output", True))

    @classmethod
    def keep_downloaded_files(cls) -> bool:
        return bool(cls.USER_EFFECTIVE.get("keep_downloaded_files", True))

    @classmethod
    def keep_wav_temp(cls) -> bool:
        return bool(cls.USER_EFFECTIVE.get("keep_wav_temp", False))

    # System knobs (read-only from defaults)
    @classmethod
    def chunk_length_s(cls) -> int:
        return int(cls.SYSTEM_EFFECTIVE.get("chunk_length_s", 60))

    @classmethod
    def stride_length_s(cls) -> int:
        return int(cls.SYSTEM_EFFECTIVE.get("stride_length_s", 5))

    @classmethod
    def local_models_only(cls) -> bool:
        return bool(cls.SYSTEM_EFFECTIVE.get("local_models_only", True))
