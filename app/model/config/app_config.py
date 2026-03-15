# app/model/config/app_config.py
from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Dict, Tuple, TYPE_CHECKING

from app.model.helpers.string_utils import sanitize_filename
from app.model.helpers.errors import AppError

if TYPE_CHECKING:
    from app.model.services.settings_service import SettingsSnapshot


class ConfigError(AppError):
    """Key-based runtime configuration error."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key=str(key), params=dict(params or {}))


class AppConfig:
    """Global runtime configuration and path mapping."""

    APP_NAME: str = "PySkryptor"
    APP_VERSION: str = "1.0 ALPHA"
    APP_AUTHOR: str = "Bartosz Golat"
    APP_DEVELOPMENT_YEARS: str = "2025-2026"
    APP_REPO_URL: str = "https://github.com/K44DEUSZ/PySkryptor"

    ROOT_DIR: Path = Path(__file__).resolve().parents[3]

    APP_DIR: Path = ROOT_DIR / "app"
    LICENSE_FILE: Path = ROOT_DIR / "LICENSE"

    ASSETS_DIR: Path = ROOT_DIR / "assets"
    RUNTIME_DIR: Path = ROOT_DIR / "bin"

    AI_MODELS_DIR: Path = ROOT_DIR / "models"

    LOCALES_DIR: Path = ASSETS_DIR / "locales"
    STYLES_DIR: Path = APP_DIR / "view"
    IMAGES_DIR: Path = ASSETS_DIR / "images"
    ICONS_DIR: Path = ASSETS_DIR / "icons"

    FFMPEG_DIR: Path = RUNTIME_DIR / "ffmpeg"
    FFMPEG_BIN_DIR: Path = FFMPEG_DIR

    DENO_DIR: Path = RUNTIME_DIR / "deno"
    DENO_BIN: Path = DENO_DIR / ("deno.exe" if platform.system().lower().startswith("win") else "deno")

    MISSING_VALUE: str = "__missing__"

    TRANSCRIPTION_ENGINE_DIR: Path = AI_MODELS_DIR / MISSING_VALUE
    TRANSLATION_ENGINE_DIR: Path = AI_MODELS_DIR / MISSING_VALUE

    MODEL_CONFIG_FILE: str = "config.json"
    TRANSCRIPTION_MODEL_TYPES: Tuple[str, ...] = ("whisper",)
    TRANSLATION_MODEL_TYPES: Tuple[str, ...] = ("m2m_100",)

    DATA_DIR: Path = ROOT_DIR / "userdata"
    DOWNLOADS_DIR: Path = DATA_DIR / "downloads"
    TRANSCRIPTIONS_DIR: Path = DATA_DIR / "transcriptions"
    LOGS_DIR: Path = DATA_DIR / "logs"

    APP_LOG_NAME: str = "app.log"
    CRASH_LOG_NAME: str = "crash.log"
    APP_LOG_PATH: Path = LOGS_DIR / APP_LOG_NAME
    CRASH_LOG_PATH: Path = LOGS_DIR / CRASH_LOG_NAME

    USER_CONFIG_DIR: Path = DATA_DIR / "config"
    SETTINGS_FILE: Path = USER_CONFIG_DIR / "settings.json"

    MODEL_CONFIG_DIR: Path = APP_DIR / "model" / "config"
    DEFAULTS_FILE: Path = MODEL_CONFIG_DIR / "defaults.json"

    DOWNLOADS_TMP_DIR: Path = DOWNLOADS_DIR / "._tmp"
    TRANSCRIPTIONS_TMP_DIR: Path = TRANSCRIPTIONS_DIR / "._tmp"

    DOWNLOAD_PURPOSE_DOWNLOAD: str = "download"
    DOWNLOAD_PURPOSE_TRANSCRIPTION: str = "transcription"

    DOWNLOAD_ARTIFACT_POLICY_STRICT_FINAL_EXT: str = "strict_final_ext"
    DOWNLOAD_ARTIFACT_POLICY_WORK_INPUT: str = "work_input"

    DOWNLOAD_DEFAULT_PURPOSE: str = DOWNLOAD_PURPOSE_DOWNLOAD
    DOWNLOAD_DEFAULT_STEM: str = "download"

    LANGUAGE_AUTO_VALUE: str = "auto"
    LANGUAGE_DEFAULT_VALUE: str = "default"
    LANGUAGE_UI_VALUE: str = "ui"
    LANGUAGE_APP_VALUE: str = "app"
    LANGUAGE_DEFAULT_UI_VALUE: str = "default_ui"

    DOWNLOAD_AUDIO_LANG_AUTO_VALUES: Tuple[str, ...] = (
        LANGUAGE_DEFAULT_VALUE,
        LANGUAGE_AUTO_VALUE,
        "-",
    )
    TRANSLATION_SOURCE_DEFERRED_VALUES: Tuple[str, ...] = (
        LANGUAGE_AUTO_VALUE,
        LANGUAGE_UI_VALUE,
        LANGUAGE_APP_VALUE,
        LANGUAGE_DEFAULT_VALUE,
        LANGUAGE_DEFAULT_UI_VALUE,
    )
    TRANSLATION_TARGET_DEFERRED_VALUES: Tuple[str, ...] = (
        LANGUAGE_AUTO_VALUE,
        LANGUAGE_DEFAULT_VALUE,
        LANGUAGE_UI_VALUE,
        LANGUAGE_APP_VALUE,
        LANGUAGE_DEFAULT_UI_VALUE,
    )

    DOWNLOAD_FALLBACK_AUDIO_SELECTOR: str = "bestaudio/best"
    DOWNLOAD_FALLBACK_VIDEO_SELECTOR: str = "bv*+ba/b"
    URL_DOWNLOAD_DEFAULT_QUALITY: str = "best"

    OUTPUT_DEFAULT_STEM: str = "item"
    TRANSCRIPT_DEFAULT_BASENAME: str = "transcript"
    TMP_AUDIO_DEFAULT_STEM: str = "audio"
    AUDIO_OUTPUT_DEFAULT_FILENAME: str = "Audio.wav"
    AUDIO_OUTPUT_DEFAULT_BASENAME: str = "Audio"
    SOURCE_MEDIA_DEFAULT_BASENAME: str = "Source"
    SOURCE_MEDIA_DEFAULT_EXT: str = "bin"

    AUDIO_PROBE_TIMEOUT_S: float = 10.0
    ASR_SAMPLE_RATE: int = 16000
    ASR_CHANNELS: int = 1
    ASR_WAV_FORMAT_TOKEN: str = "wav"
    ASR_WAV_CODEC_PREFIX: str = "pcm_"

    # ----- Model tokenizer files -----
    TRANSCRIPTION_MODEL_TOKENIZER_FILE: str = "tokenizer_config.json"
    TRANSLATION_MODEL_TOKENIZER_FILE: str = "special_tokens_map.json"

    @classmethod
    def transcription_model_tokenizer_path(cls) -> Path:
        return cls.TRANSCRIPTION_ENGINE_DIR / cls.TRANSCRIPTION_MODEL_TOKENIZER_FILE

    @classmethod
    def translation_model_tokenizer_path(cls) -> Path:
        return cls.TRANSLATION_ENGINE_DIR / cls.TRANSLATION_MODEL_TOKENIZER_FILE

    # ----- Model resolution -----
    @classmethod
    def resolve_transcription_engine_name(cls, model: Dict[str, Any]) -> str:
        from app.model.services.ai_models_service import resolve_engine_name

        cfg = model.get("transcription_model", {}) if isinstance(model, dict) else {}
        return resolve_engine_name(cfg if isinstance(cfg, dict) else {}, task="transcription")

    @classmethod
    def resolve_translation_engine_name(cls, model: Dict[str, Any]) -> str:
        from app.model.services.ai_models_service import resolve_engine_name

        cfg = model.get("translation_model", {}) if isinstance(model, dict) else {}
        return resolve_engine_name(cfg if isinstance(cfg, dict) else {}, task="translation")

    # ----- UI defaults -----

    @classmethod
    def set_root_dir(cls, root_dir: Path) -> None:
        cls.ROOT_DIR = Path(root_dir).resolve()

        cls.APP_DIR = cls.ROOT_DIR / "app"
        cls.LICENSE_FILE = cls.ROOT_DIR / "LICENSE"

        cls.ASSETS_DIR = cls.ROOT_DIR / "assets"
        cls.RUNTIME_DIR = cls.ROOT_DIR / "bin"

        cls.AI_MODELS_DIR = cls.ROOT_DIR / "models"

        cls.LOCALES_DIR = cls.ASSETS_DIR / "locales"
        cls.STYLES_DIR = cls.APP_DIR / "view"
        cls.IMAGES_DIR = cls.ASSETS_DIR / "images"
        cls.ICONS_DIR = cls.ASSETS_DIR / "icons"

        cls.FFMPEG_DIR = cls.RUNTIME_DIR / "ffmpeg"
        cls.FFMPEG_BIN_DIR = cls.FFMPEG_DIR

        cls.DENO_DIR = cls.RUNTIME_DIR / "deno"
        cls.DENO_BIN = cls.DENO_DIR / ("deno.exe" if platform.system().lower().startswith("win") else "deno")

        cls.MISSING_VALUE = "__missing__"
        cls.TRANSCRIPTION_ENGINE_DIR = cls.AI_MODELS_DIR / cls.MISSING_VALUE
        cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / cls.MISSING_VALUE

        cls.DATA_DIR = cls.ROOT_DIR / "userdata"
        cls.DOWNLOADS_DIR = cls.DATA_DIR / "downloads"
        cls.TRANSCRIPTIONS_DIR = cls.DATA_DIR / "transcriptions"
        cls.LOGS_DIR = cls.DATA_DIR / "logs"

        cls.APP_LOG_NAME = "app.log"
        cls.CRASH_LOG_NAME = "crash.log"
        cls.APP_LOG_PATH = cls.LOGS_DIR / cls.APP_LOG_NAME
        cls.CRASH_LOG_PATH = cls.LOGS_DIR / cls.CRASH_LOG_NAME

        cls.USER_CONFIG_DIR = cls.DATA_DIR / "config"
        cls.SETTINGS_FILE = cls.USER_CONFIG_DIR / "settings.json"

        cls.MODEL_CONFIG_DIR = cls.APP_DIR / "model" / "config"
        cls.DEFAULTS_FILE = cls.MODEL_CONFIG_DIR / "defaults.json"

        cls.DOWNLOADS_TMP_DIR = cls.DOWNLOADS_DIR / "._tmp"
        cls.TRANSCRIPTIONS_TMP_DIR = cls.TRANSCRIPTIONS_DIR / "._tmp"

        cls.DOWNLOAD_PURPOSE_DOWNLOAD = "download"
        cls.DOWNLOAD_PURPOSE_TRANSCRIPTION = "transcription"

        cls.DOWNLOAD_ARTIFACT_POLICY_STRICT_FINAL_EXT = "strict_final_ext"
        cls.DOWNLOAD_ARTIFACT_POLICY_WORK_INPUT = "work_input"

        cls.DOWNLOAD_DEFAULT_PURPOSE = cls.DOWNLOAD_PURPOSE_DOWNLOAD
        cls.DOWNLOAD_DEFAULT_STEM = "download"

        cls.LANGUAGE_AUTO_VALUE = "auto"
        cls.LANGUAGE_DEFAULT_VALUE = "default"
        cls.LANGUAGE_UI_VALUE = "ui"
        cls.LANGUAGE_APP_VALUE = "app"
        cls.LANGUAGE_DEFAULT_UI_VALUE = "default_ui"

        cls.DOWNLOAD_AUDIO_LANG_AUTO_VALUES = (
            cls.LANGUAGE_DEFAULT_VALUE,
            cls.LANGUAGE_AUTO_VALUE,
            "-",
        )
        cls.TRANSLATION_SOURCE_DEFERRED_VALUES = (
            cls.LANGUAGE_AUTO_VALUE,
            cls.LANGUAGE_UI_VALUE,
            cls.LANGUAGE_APP_VALUE,
            cls.LANGUAGE_DEFAULT_VALUE,
            cls.LANGUAGE_DEFAULT_UI_VALUE,
        )
        cls.TRANSLATION_TARGET_DEFERRED_VALUES = (
            cls.LANGUAGE_AUTO_VALUE,
            cls.LANGUAGE_DEFAULT_VALUE,
            cls.LANGUAGE_UI_VALUE,
            cls.LANGUAGE_APP_VALUE,
            cls.LANGUAGE_DEFAULT_UI_VALUE,
        )

        cls.DOWNLOAD_FALLBACK_AUDIO_SELECTOR = "bestaudio/best"
        cls.DOWNLOAD_FALLBACK_VIDEO_SELECTOR = "bv*+ba/b"
        cls.URL_DOWNLOAD_DEFAULT_QUALITY = "best"

        cls.OUTPUT_DEFAULT_STEM = "item"
        cls.TRANSCRIPT_DEFAULT_BASENAME = "transcript"
        cls.TMP_AUDIO_DEFAULT_STEM = "audio"
        cls.AUDIO_OUTPUT_DEFAULT_FILENAME = "Audio.wav"
        cls.AUDIO_OUTPUT_DEFAULT_BASENAME = "Audio"
        cls.SOURCE_MEDIA_DEFAULT_BASENAME = "Source"
        cls.SOURCE_MEDIA_DEFAULT_EXT = "bin"

        cls.AUDIO_PROBE_TIMEOUT_S = 10.0
        cls.ASR_SAMPLE_RATE = 16000
        cls.ASR_CHANNELS = 1
        cls.ASR_WAV_FORMAT_TOKEN = "wav"
        cls.ASR_WAV_CODEC_PREFIX = "pcm_"

    # ----- File input / download output extensions -----
    FILES_AUDIO_INPUT_EXTS: Tuple[str, ...] = ("wav", "mp3", "flac", "m4a", "ogg", "aac")
    FILES_VIDEO_INPUT_EXTS: Tuple[str, ...] = ("mp4", "webm", "mkv", "mov", "avi")

    DOWNLOAD_AUDIO_FORMAT_PROFILES: Dict[str, Dict[str, Any]] = {
        "wav": {"selector_exts": ("wav",), "postprocess": "extract_audio", "preferredcodec": "wav"},
        "mp3": {"selector_exts": ("mp3",), "postprocess": "extract_audio", "preferredcodec": "mp3"},
        "flac": {"selector_exts": ("flac",), "postprocess": "extract_audio", "preferredcodec": "flac"},
        "m4a": {"selector_exts": ("m4a", "mp4"), "postprocess": "extract_audio", "preferredcodec": "m4a"},
        "ogg": {"selector_exts": ("ogg", "opus", "webm"), "postprocess": "extract_audio", "preferredcodec": "ogg"},
        "aac": {"selector_exts": ("aac", "m4a", "mp4"), "postprocess": "extract_audio", "preferredcodec": "aac"},
    }
    DOWNLOAD_VIDEO_FORMAT_PROFILES: Dict[str, Dict[str, Any]] = {
        "mp4": {
            "video_exts": ("mp4",),
            "audio_exts": ("m4a", "mp4", "aac"),
            "strategy": "native_or_merge_or_convert",
            "strict_final_ext": True,
        },
        "webm": {
            "video_exts": ("webm",),
            "audio_exts": ("webm", "opus"),
            "strategy": "native_or_merge_or_convert",
            "strict_final_ext": True,
        },
        "mkv": {
            "video_exts": tuple(),
            "audio_exts": tuple(),
            "strategy": "remux",
            "strict_final_ext": True,
        },
        "mov": {
            "video_exts": ("mov",),
            "audio_exts": ("m4a", "mp4", "aac"),
            "strategy": "native_or_merge_or_convert",
            "strict_final_ext": True,
        },
        "avi": {
            "video_exts": tuple(),
            "audio_exts": tuple(),
            "strategy": "convert",
            "strict_final_ext": True,
        },
    }

    DOWNLOAD_AUDIO_OUTPUT_EXTS: Tuple[str, ...] = tuple(DOWNLOAD_AUDIO_FORMAT_PROFILES.keys())
    DOWNLOAD_VIDEO_OUTPUT_EXTS: Tuple[str, ...] = tuple(DOWNLOAD_VIDEO_FORMAT_PROFILES.keys())

    @classmethod
    def download_audio_format_profile(cls, ext: str) -> Dict[str, Any]:
        return dict(cls.DOWNLOAD_AUDIO_FORMAT_PROFILES.get(str(ext or "").strip().lower(), {}))

    @classmethod
    def download_video_format_profile(cls, ext: str) -> Dict[str, Any]:
        return dict(cls.DOWNLOAD_VIDEO_FORMAT_PROFILES.get(str(ext or "").strip().lower(), {}))

    @classmethod
    def resolve_download_contract(
        cls,
        *,
        kind: str,
        purpose: str,
        keep_output: bool,
        ext: str,
    ) -> Dict[str, Any]:
        kind_l = str(kind or "").strip().lower()
        purpose_l = cls.normalize_policy_value(purpose) or cls.DOWNLOAD_DEFAULT_PURPOSE
        ext_l = str(ext or "").strip().lower().lstrip(".")

        if kind_l == "audio":
            strict_final_ext = bool(ext_l)
        else:
            strict_final_ext = bool(cls.download_video_format_profile(ext_l).get("strict_final_ext"))

        artifact_policy = cls.DOWNLOAD_ARTIFACT_POLICY_STRICT_FINAL_EXT
        final_ext = ext_l if strict_final_ext else ""

        if purpose_l == cls.DOWNLOAD_PURPOSE_TRANSCRIPTION and not bool(keep_output):
            artifact_policy = cls.DOWNLOAD_ARTIFACT_POLICY_WORK_INPUT
            final_ext = ""

        return {
            "plan_ext": ext_l,
            "final_ext": final_ext,
            "artifact_policy": artifact_policy,
            "strict_final_ext": bool(final_ext),
        }

    @classmethod
    def normalize_policy_value(cls, value: Any) -> str:
        return str(value or "").strip().lower()

    @classmethod
    def is_auto_language_value(cls, value: Any) -> bool:
        return cls.normalize_policy_value(value) == cls.LANGUAGE_AUTO_VALUE

    @classmethod
    def is_download_audio_auto_value(cls, value: Any) -> bool:
        token = cls.normalize_policy_value(value)
        return token in set(cls.DOWNLOAD_AUDIO_LANG_AUTO_VALUES)

    @classmethod
    def is_translation_source_deferred_value(cls, value: Any) -> bool:
        token = cls.normalize_policy_value(value)
        return (not token) or token in set(cls.TRANSLATION_SOURCE_DEFERRED_VALUES)

    @classmethod
    def is_translation_target_deferred_value(cls, value: Any) -> bool:
        token = cls.normalize_policy_value(value)
        return (not token) or token in set(cls.TRANSLATION_TARGET_DEFERRED_VALUES)

    @classmethod
    def files_audio_input_file_exts(cls) -> Tuple[str, ...]:
        return tuple(f".{x}" for x in cls.FILES_AUDIO_INPUT_EXTS)

    @classmethod
    def files_video_input_file_exts(cls) -> Tuple[str, ...]:
        return tuple(f".{x}" for x in cls.FILES_VIDEO_INPUT_EXTS)

    @classmethod
    def files_media_input_file_exts(cls) -> Tuple[str, ...]:
        exts = {e.lower() for e in cls.files_audio_input_file_exts()}
        exts |= {e.lower() for e in cls.files_video_input_file_exts()}
        return tuple(sorted(exts))

    # ----- Transcript output -----
    TRANSCRIPTION_OUTPUT_MODES: Tuple[Dict[str, Any], ...] = (
        {"id": "txt", "ext": "txt", "timestamps": False, "tr_key": "transcription.output_mode.plain_txt.label"},
        {"id": "txt_ts", "ext": "txt", "timestamps": True, "tr_key": "transcription.output_mode.txt_timestamps.label"},
        {"id": "srt", "ext": "srt", "timestamps": True, "tr_key": "transcription.output_mode.srt.label"},
    )
    TRANSCRIPT_DEFAULT_EXT: str = "txt"

    _TRANSCRIPT_FILENAMES: Dict[str, str] = {
        "txt": "transcript.txt",
        "txt_ts": "transcript_ts.txt",
        "srt": "transcript.srt",
    }

    @classmethod
    def transcript_filename(cls, mode_id: str) -> str:
        """Return a deterministic transcript filename for a given output mode."""
        mid = str(mode_id or "txt").strip().lower()
        if mid in cls._TRANSCRIPT_FILENAMES:
            return cls._TRANSCRIPT_FILENAMES[mid]

        mode = cls.get_transcription_output_mode(mid)
        ext = str(mode.get("ext", "txt") or "txt").strip().lower().lstrip(".") or "txt"
        safe_mid = sanitize_filename(mid) or "mode"
        return f"transcript_{safe_mid}.{ext}"

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

    NET_NO_CHECK_CERT: bool = True

    ENGINE_LOW_CPU_MEM_USAGE: bool = True

    USE_SAFETENSORS: bool = True

    DEVICE_ID: str = "cpu"
    DTYPE_ID: str = "float32"

    DEVICE_FRIENDLY_NAME: str = "CPU"
    DEVICE_KIND: str = "CPU"
    DEVICE_MODEL: str | None = None

    HAS_CUDA: bool = False
    BF16_SUPPORTED: bool = False
    TF32_ENABLED: bool = False
    TF32_SUPPORTED: bool = False
    SETTINGS: "SettingsSnapshot | None" = None

    # ----- Snapshot accessors (UI/Controller helpers) -----
    @classmethod
    def _snapshot_section_dict(cls, section_name: str) -> dict:
        """Return a shallow dict copy of a SettingsSnapshot section."""

        snap = cls.SETTINGS
        if snap is None:
            return {}
        sec = getattr(snap, section_name, {})
        return dict(sec) if isinstance(sec, dict) else {}

    @classmethod
    def transcription_cfg_dict(cls) -> dict:
        return cls._snapshot_section_dict("transcription")

    @classmethod
    def translation_cfg_dict(cls) -> dict:
        return cls._snapshot_section_dict("translation")

    @classmethod
    def engine_cfg_dict(cls) -> dict:
        return cls._snapshot_section_dict("engine")

    @classmethod
    def model_cfg_dict(cls) -> dict:
        return cls._snapshot_section_dict("model")

    @classmethod
    def transcription_model_cfg_dict(cls) -> dict:
        cfg = dict(cls.model_cfg_dict().get("transcription_model") or {})
        resolved_name = cls.TRANSCRIPTION_ENGINE_DIR.name if cls.TRANSCRIPTION_ENGINE_DIR.name != cls.MISSING_VALUE else ""
        if resolved_name:
            from app.model.services.ai_models_service import local_model_descriptor

            cfg["engine_name"] = resolved_name
            desc = local_model_descriptor(resolved_name)
            if desc:
                cfg["engine_model_type"] = str(desc.get("model_type", "") or "")
                cfg["engine_signature"] = str(desc.get("signature", "") or "")
        return cfg

    @classmethod
    def translation_model_cfg_dict(cls) -> dict:
        cfg = dict(cls.model_cfg_dict().get("translation_model") or {})
        resolved_name = cls.TRANSLATION_ENGINE_DIR.name if cls.TRANSLATION_ENGINE_DIR.name != cls.MISSING_VALUE else ""
        if resolved_name:
            from app.model.services.ai_models_service import local_model_descriptor

            cfg["engine_name"] = resolved_name
            desc = local_model_descriptor(resolved_name)
            if desc:
                cfg["engine_model_type"] = str(desc.get("model_type", "") or "")
                cfg["engine_signature"] = str(desc.get("signature", "") or "")
        return cfg

    # ----- Snapshot mapping -----
    @classmethod
    def initialize_from_snapshot(cls, snap: "SettingsSnapshot") -> None:
        cls.SETTINGS = snap
        cls._apply_transcription_engine_dir(snap.model)
        cls._apply_translation_engine_dir(snap.model)
        cls._apply_downloader(snap.downloader)
        cls._apply_network(snap.network)
        cls._apply_transcription(snap.transcription)
        cls._apply_engine(snap.engine)

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
            cls._apply_engine(snap.engine)

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
            cls.ICONS_DIR,
            cls.DOWNLOADS_DIR,
            cls.TRANSCRIPTIONS_DIR,
            cls.LOGS_DIR,
            cls.USER_CONFIG_DIR,
            cls.DOWNLOADS_TMP_DIR,
            cls.TRANSCRIPTIONS_TMP_DIR,
        ):
            p.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    # ----- Apply sections -----
    @classmethod
    def _apply_downloader(cls, downloader: Dict[str, Any]) -> None:
        mn = cls._coerce_int(downloader.get("min_video_height", cls.VIDEO_MIN_HEIGHT), cls.VIDEO_MIN_HEIGHT)
        mx = cls._coerce_int(downloader.get("max_video_height", cls.VIDEO_MAX_HEIGHT), cls.VIDEO_MAX_HEIGHT)
        if mx < mn:
            mx = mn
        cls.VIDEO_MIN_HEIGHT = max(1, mn)
        cls.VIDEO_MAX_HEIGHT = max(cls.VIDEO_MIN_HEIGHT, mx)

    @classmethod
    def _apply_network(cls, network: Dict[str, Any]) -> None:
        kbps = network.get("max_bandwidth_kbps")
        try:
            kbps_val = int(kbps) if kbps is not None else None
        except Exception:
            kbps_val = None
        if kbps_val is not None and kbps_val <= 0:
            kbps_val = None

        cls.NET_MAX_KBPS = kbps_val
        cls.NET_RETRIES = max(0, cls._coerce_int(network.get("retries", cls.NET_RETRIES), cls.NET_RETRIES))
        cls.NET_CONC_FRAG = max(1, cls._coerce_int(network.get("concurrent_fragments", cls.NET_CONC_FRAG), cls.NET_CONC_FRAG))
        cls.NET_TIMEOUT_S = max(1, cls._coerce_int(network.get("http_timeout_s", cls.NET_TIMEOUT_S), cls.NET_TIMEOUT_S))

        cls.NET_NO_CHECK_CERT = bool(network.get("no_check_certificate", cls.NET_NO_CHECK_CERT))

    @classmethod
    def _apply_transcription(cls, transcription: Dict[str, Any]) -> None:
        raw = transcription.get("output_formats")
        if isinstance(raw, str) and raw.strip():
            mode_id = raw.strip().lower()
        elif isinstance(raw, (list, tuple)) and raw:
            mode_id = str(raw[0] or "txt").strip().lower()
        else:
            mode_id = "txt"

        mode = cls.get_transcription_output_mode(mode_id)
        cls.TRANSCRIPT_DEFAULT_EXT = str(mode.get("ext", "txt") or "txt").strip().lower().lstrip(".") or "txt"

    @classmethod
    def _apply_engine(cls, engine: Dict[str, Any]) -> None:
        """Apply lightweight engine settings from a snapshot section."""

        if not isinstance(engine, dict):
            return
        cls.ENGINE_LOW_CPU_MEM_USAGE = bool(engine.get("low_cpu_mem_usage", cls.ENGINE_LOW_CPU_MEM_USAGE))

    @classmethod
    def _apply_translation_engine_dir(cls, model: Dict[str, Any]) -> None:
        from app.model.services.ai_models_service import resolve_engine_name

        tcfg = model.get("translation_model", {})
        resolved = resolve_engine_name(tcfg if isinstance(tcfg, dict) else {}, task="translation")
        cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / resolved

    @classmethod
    def _apply_transcription_engine_dir(cls, model: Dict[str, Any]) -> None:
        from app.model.services.ai_models_service import resolve_engine_name

        tcfg = model.get("transcription_model", {})
        resolved = resolve_engine_name(tcfg if isinstance(tcfg, dict) else {}, task="transcription")
        cls.TRANSCRIPTION_ENGINE_DIR = cls.AI_MODELS_DIR / resolved

    # ----- Runtime capabilities -----
    @classmethod
    def has_cuda(cls) -> bool:
        return bool(cls.HAS_CUDA)

    @classmethod
    def auto_device_key(cls) -> str:
        return "cuda" if cls.has_cuda() else "cpu"

    @classmethod
    def auto_precision_key(cls) -> str:
        return "float16" if cls.has_cuda() else "float32"

    @classmethod
    def runtime_capabilities(cls) -> Dict[str, bool]:
        return {
            "has_cuda": bool(cls.HAS_CUDA),
            "bf16_supported": bool(cls.BF16_SUPPORTED),
            "tf32_supported": bool(cls.TF32_SUPPORTED),
        }
