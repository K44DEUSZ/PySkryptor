# core/config/app_config.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import torch


class ConfigError(RuntimeError):
    def __init__(self, key: str, **params: Any) -> None:
        self.key = key
        self.params = params
        super().__init__(key)


class AppConfig:
    """
    Central configuration. From now on:
    - settings.json is the single source of truth.
    - defaults.json is only a template for manual restore, not an automatic fallback.
    """

    ROOT_DIR: Path = Path.cwd()
    _CFG_DIR: Path = ROOT_DIR / "core" / "config"
    _DEFAULTS_PATH: Path = _CFG_DIR / "defaults.json"
    _SETTINGS_PATH: Path = _CFG_DIR / "settings.json"

    DEFAULTS_RAW: Dict[str, Any] = {}
    SETTINGS_RAW: Dict[str, Any] = {}

    PATHS: Dict[str, Any] = {}
    MEDIA: Dict[str, Any] = {}
    MODEL: Dict[str, Any] = {}
    USER: Dict[str, Any] = {}

    SETTINGS_ISSUES: List[str] = []

    # Derived paths
    RESOURCES_DIR: Path = ROOT_DIR / "resources"
    FFMPEG_DIR: Path = RESOURCES_DIR / "ffmpeg"
    MODELS_DIR: Path = RESOURCES_DIR / "models"
    AI_ENGINE_DIR: Path = MODELS_DIR / "whisper-turbo"
    DATA_DIR: Path = ROOT_DIR / "data"
    DOWNLOADS_DIR: Path = DATA_DIR / "downloads"
    INPUT_TMP_DIR: Path = DATA_DIR / ".input_tmp"
    TRANSCRIPTIONS_DIR: Path = DATA_DIR / "transcriptions"
    FFMPEG_BIN_DIR: Path = FFMPEG_DIR

    # Runtime (device/dtype)
    DEVICE: torch.device = torch.device("cpu")
    DTYPE = torch.float32
    DEVICE_FRIENDLY_NAME: str = "CPU"
    TF32_ENABLED: bool = False

    # ---------- JSON helpers ----------
    @classmethod
    def _read_json(cls, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("root must be an object")
        return data

    @classmethod
    def _write_json(cls, path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def restore_settings_from_defaults(cls, sections: Iterable[str] | None = None) -> None:
        defaults = cls._read_json(cls._DEFAULTS_PATH)
        if sections is None:
            data = defaults
        else:
            data = dict(cls.SETTINGS_RAW) if isinstance(cls.SETTINGS_RAW, dict) else {}
            for sec in sections:
                if sec in defaults:
                    data[sec] = defaults[sec]
        cls._write_json(cls._SETTINGS_PATH, data)

    # ---------- Validation ----------
    @staticmethod
    def _ensure_dict(obj: Any, name: str) -> Dict[str, Any]:
        if not isinstance(obj, dict):
            raise ValueError(f"section '{name}' must be an object")
        return obj

    @classmethod
    def _validate_required_keys(cls, sec_name: str, src: Dict[str, Any], schema: Dict[str, Any]) -> None:
        # Check presence of required keys (use keys from defaults.json as schema)
        required = set(schema.keys())
        present = set(src.keys())
        missing = sorted(list(required - present))
        if missing:
            raise ValueError(f"{sec_name}: missing keys: {', '.join(missing)}")

    @classmethod
    def _validate_paths(cls, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        cls._validate_required_keys("paths", src, schema)
        for k, v in src.items():
            if not isinstance(v, str) or not v:
                raise ValueError(f"paths.{k} must be a non-empty string")
        return src

    @classmethod
    def _validate_media(cls, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        cls._validate_required_keys("media", src, schema)
        def _norm_list(val: Any, key: str) -> List[str]:
            if not isinstance(val, list) or not all(isinstance(x, (str, int, float)) for x in val):
                raise ValueError(f"media.{key} must be a list of strings")
            return [str(x).lower() for x in val]
        out = {
            "audio_ext": _norm_list(src["audio_ext"], "audio_ext"),
            "video_ext": _norm_list(src["video_ext"], "video_ext"),
        }
        return out

    @classmethod
    def _validate_model(cls, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        cls._validate_required_keys("model", src, schema)
        out: Dict[str, Any] = {}
        out["ai_engine_name"] = str(src["ai_engine_name"])
        out["local_models_only"] = bool(src["local_models_only"])
        for k in ("chunk_length_s", "stride_length_s"):
            if not isinstance(src[k], int):
                raise ValueError(f"model.{k} must be int")
            out[k] = int(src[k])
        out["pipeline_task"] = str(src["pipeline_task"])
        out["ignore_warning"] = bool(src["ignore_warning"])
        out["default_language"] = src.get("default_language", None)
        out["return_timestamps"] = bool(src["return_timestamps"])
        out["use_safetensors"] = bool(src["use_safetensors"])
        out["low_cpu_mem_usage"] = bool(src["low_cpu_mem_usage"])
        return out

    @classmethod
    def _validate_user(cls, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        cls._validate_required_keys("user", src, schema)
        def _enum(val: Any, allowed: Tuple[str, ...], key: str) -> str:
            v = str(val).lower()
            if v not in allowed:
                raise ValueError(f"user.{key} invalid '{v}', allowed: {', '.join(allowed)}")
            return v
        out: Dict[str, Any] = {}
        out["language"] = _enum(src["language"], ("pl", "en"), "language")
        out["preferred_device"] = _enum(src["preferred_device"], ("auto", "cpu", "gpu"), "preferred_device")
        out["precision"] = _enum(src["precision"], ("auto", "float32", "float16", "bfloat16"), "precision")
        out["allow_tf32"] = bool(src["allow_tf32"])
        out["timestamps_output"] = bool(src["timestamps_output"])
        out["keep_downloaded_files"] = bool(src["keep_downloaded_files"])
        out["keep_wav_temp"] = bool(src["keep_wav_temp"])
        return out

    # ---------- Apply paths/binaries ----------
    @classmethod
    def _apply_paths(cls) -> None:
        def _resolve(p: str) -> Path:
            path = Path(p)
            return path if path.is_absolute() else (cls.ROOT_DIR / path)

        resources = _resolve(cls.PATHS["resources_dir"])
        data = _resolve(cls.PATHS["data_dir"])
        cls.RESOURCES_DIR = resources
        cls.FFMPEG_DIR = resources / cls.PATHS["ffmpeg_subdir"]
        cls.MODELS_DIR = resources / cls.PATHS["models_subdir"]
        cls.AI_ENGINE_DIR = cls.MODELS_DIR / cls.PATHS["ai_engine_subdir"]
        cls.DATA_DIR = data
        cls.DOWNLOADS_DIR = data / cls.PATHS["downloads_subdir"]
        cls.INPUT_TMP_DIR = data / cls.PATHS["input_tmp_subdir"]
        cls.TRANSCRIPTIONS_DIR = data / cls.PATHS["transcriptions_subdir"]

    @classmethod
    def _ensure_dirs(cls) -> None:
        for p in (cls.RESOURCES_DIR, cls.FFMPEG_DIR, cls.MODELS_DIR, cls.AI_ENGINE_DIR,
                  cls.DATA_DIR, cls.DOWNLOADS_DIR, cls.TRANSCRIPTIONS_DIR):
            p.mkdir(parents=True, exist_ok=True)

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
    def _resolve_device(cls) -> torch.device:
        pref = str(cls.USER.get("preferred_device", "auto")).lower()
        if os.environ.get("FORCE_CPU", "0") == "1":
            return torch.device("cpu")
        if pref == "cpu":
            return torch.device("cpu")
        if pref == "gpu":
            return torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
        return torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")

    @classmethod
    def _resolve_dtype(cls, device: torch.device):
        prec = str(cls.USER.get("precision", "auto")).lower()
        if device.type == "cuda":
            if prec == "float32":
                return torch.float32
            if prec == "float16":
                return torch.float16
            if prec == "bfloat16":
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

        allow_tf32 = bool(cls.USER.get("allow_tf32", True))
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

    # ---------- Public entry ----------
    @classmethod
    def initialize(cls) -> None:
        if not cls._DEFAULTS_PATH.exists():
            raise ConfigError("error.defaults_missing", path=str(cls._DEFAULTS_PATH))

        try:
            defaults = cls._read_json(cls._DEFAULTS_PATH)
            cls.DEFAULTS_RAW = defaults
        except Exception as ex:
            raise ConfigError("error.config.generic", detail=f"defaults.json: {ex}")

        if not cls._SETTINGS_PATH.exists():
            raise ConfigError("error.settings_invalid", path=str(cls._DEFAULTS_PATH), detail="settings.json missing")

        try:
            settings = cls._read_json(cls._SETTINGS_PATH)
            cls.SETTINGS_RAW = settings
        except Exception as ex:
            raise ConfigError("error.settings_invalid", path=str(cls._DEFAULTS_PATH), detail=f"invalid JSON: {ex}")

        # Shape/schema from defaults
        schema_paths = cls._ensure_dict(defaults.get("paths", {}), "paths")
        schema_media = cls._ensure_dict(defaults.get("media", {}), "media")
        schema_model = cls._ensure_dict(defaults.get("model", {}), "model")
        schema_user = cls._ensure_dict(defaults.get("user", {}), "user")

        # Validate settings strictly (no fallback of values)
        try:
            src_paths = cls._ensure_dict(settings.get("paths", {}), "paths")
            src_media = cls._ensure_dict(settings.get("media", {}), "media")
            src_model = cls._ensure_dict(settings.get("model", {}), "model")
            src_user = cls._ensure_dict(settings.get("user", {}), "user")

            cls.PATHS = cls._validate_paths(src_paths, schema_paths)
            cls.MEDIA = cls._validate_media(src_media, schema_media)
            cls.MODEL = cls._validate_model(src_model, schema_model)
            cls.USER = cls._validate_user(src_user, schema_user)
        except Exception as ex:
            raise ConfigError("error.settings_invalid", path=str(cls._DEFAULTS_PATH), detail=str(ex))

        # Apply
        cls._apply_paths()
        cls._ensure_dirs()
        cls._setup_ffmpeg_on_path()
        cls._setup_device_dtype()

    # ---------- Accessors ----------
    @classmethod
    def language(cls) -> str:
        return str(cls.USER.get("language", "pl"))

    @classmethod
    def audio_extensions(cls) -> Tuple[str, ...]:
        return tuple(cls.MEDIA.get("audio_ext", []))

    @classmethod
    def video_extensions(cls) -> Tuple[str, ...]:
        return tuple(cls.MEDIA.get("video_ext", []))

    # Model knobs from JSON
    @classmethod
    def ai_engine_name(cls) -> str:
        return str(cls.MODEL.get("ai_engine_name"))

    @classmethod
    def local_models_only(cls) -> bool:
        return bool(cls.MODEL.get("local_models_only"))

    @classmethod
    def chunk_length_s(cls) -> int:
        return int(cls.MODEL.get("chunk_length_s"))

    @classmethod
    def stride_length_s(cls) -> int:
        return int(cls.MODEL.get("stride_length_s"))

    @classmethod
    def pipeline_task(cls) -> str:
        return str(cls.MODEL.get("pipeline_task"))

    @classmethod
    def ignore_warning(cls) -> bool:
        return bool(cls.MODEL.get("ignore_warning"))

    @classmethod
    def default_language(cls):
        return cls.MODEL.get("default_language")

    @classmethod
    def return_timestamps(cls) -> bool:
        return bool(cls.MODEL.get("return_timestamps"))

    @classmethod
    def use_safetensors(cls) -> bool:
        return bool(cls.MODEL.get("use_safetensors"))

    @classmethod
    def low_cpu_mem_usage(cls) -> bool:
        return bool(cls.MODEL.get("low_cpu_mem_usage"))

    # Persist only user section
    @classmethod
    def save_user_settings(cls) -> None:
        data = dict(cls.SETTINGS_RAW) if isinstance(cls.SETTINGS_RAW, dict) else {}
        data["user"] = dict(cls.USER)
        cls._write_json(cls._SETTINGS_PATH, data)
