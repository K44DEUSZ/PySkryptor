# app/model/config/app_config.py
from __future__ import annotations

import hashlib
import json
import platform
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.model.helpers.string_utils import sanitize_filename
from app.model.helpers.errors import AppError

if TYPE_CHECKING:
    from app.model.services.settings_service import SettingsSnapshot


class ConfigError(AppError):
    """Key-based runtime configuration error."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


class AppConfig:
    """Global runtime configuration and path mapping."""

    _UNSET = object()

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
    TRANSCRIPTION_MODEL_TYPES: tuple[str, ...] = ("whisper",)
    TRANSLATION_MODEL_TYPES: tuple[str, ...] = ("m2m_100",)

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

    DOWNLOAD_AUDIO_LANG_AUTO_VALUES: tuple[str, ...] = (
        LANGUAGE_DEFAULT_VALUE,
        LANGUAGE_AUTO_VALUE,
        "-",
    )
    TRANSLATION_SOURCE_DEFERRED_VALUES: tuple[str, ...] = (
        LANGUAGE_AUTO_VALUE,
        LANGUAGE_UI_VALUE,
        LANGUAGE_APP_VALUE,
        LANGUAGE_DEFAULT_VALUE,
        LANGUAGE_DEFAULT_UI_VALUE,
    )
    TRANSLATION_TARGET_DEFERRED_VALUES: tuple[str, ...] = (
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

    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError, TypeError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @classmethod
    def normalize_model_type(cls, model_type: Any) -> str:
        return str(model_type or "").strip().lower()

    @classmethod
    def task_for_model_type(cls, model_type: Any) -> str:
        norm = cls.normalize_model_type(model_type)
        if norm in cls.TRANSCRIPTION_MODEL_TYPES:
            return "transcription"
        if norm in cls.TRANSLATION_MODEL_TYPES:
            return "translation"
        return ""

    @classmethod
    def model_signature(cls, config_data: dict[str, Any]) -> str:
        if not isinstance(config_data, dict) or not config_data:
            return ""

        stable = {
            k: v
            for k, v in config_data.items()
            if str(k) not in ("_name_or_path", "transformers_version")
        }
        try:
            payload = json.dumps(stable, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return ""
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest().lower()

    @classmethod
    def local_model_descriptor(cls, model_name: str) -> dict[str, Any]:
        name = str(model_name or "").strip()
        if not name or name.startswith("__"):
            return {}

        model_dir = cls.AI_MODELS_DIR / name
        if not model_dir.exists() or not model_dir.is_dir():
            return {}

        cfg_path = model_dir / cls.MODEL_CONFIG_FILE
        if not cfg_path.exists() or not cfg_path.is_file():
            return {}

        cfg = cls._read_json_dict(cfg_path)
        model_type = cls.normalize_model_type(cfg.get("model_type", ""))
        task = cls.task_for_model_type(model_type)
        signature = cls.model_signature(cfg)

        return {
            "name": model_dir.name,
            "path": model_dir,
            "config_path": cfg_path,
            "model_type": model_type,
            "task": task,
            "signature": signature,
        }

    @classmethod
    def local_model_descriptors(cls) -> tuple[dict[str, Any], ...]:
        if not cls.AI_MODELS_DIR.exists() or not cls.AI_MODELS_DIR.is_dir():
            return tuple()

        out: list[dict[str, Any]] = []
        for path in sorted(cls.AI_MODELS_DIR.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_dir() or path.name.startswith("__"):
                continue
            desc = cls.local_model_descriptor(path.name)
            if desc:
                out.append(desc)
        return tuple(out)

    @classmethod
    def local_models_for_task(cls, task: str) -> tuple[dict[str, Any], ...]:
        wanted = str(task or "").strip().lower()
        return tuple(desc for desc in cls.local_model_descriptors() if str(desc.get("task", "")) == wanted)

    @classmethod
    def local_model_names_for_task(cls, task: str) -> tuple[str, ...]:
        return tuple(str(desc.get("name", "")) for desc in cls.local_models_for_task(task) if desc.get("name"))

    @classmethod
    def is_disabled_engine_name(cls, name: str) -> bool:
        token = str(name or "").strip().lower()
        return (not token) or token in ("none", "off", "disabled")

    @classmethod
    def autoselect_engine_name(cls, *, task: str) -> str:
        for desc in cls.local_models_for_task(task):
            name = str(desc.get("name", "")).strip()
            if name:
                return name
        return ""

    @classmethod
    def resolve_model_engine_name(cls, model_cfg: dict[str, Any], *, task: str) -> str:
        cfg = model_cfg if isinstance(model_cfg, dict) else {}
        raw = str(cfg.get("engine_name", "none") or "none").strip()
        low = raw.lower()

        if cls.is_disabled_engine_name(low):
            return cls.MISSING_VALUE
        if low == "auto":
            pick = cls.autoselect_engine_name(task=task)
            return pick if pick else cls.MISSING_VALUE

        desc = cls.local_model_descriptor(raw)
        if desc and str(desc.get("task", "")) == str(task or "").strip().lower():
            return str(desc.get("name") or raw)

        sig = str(cfg.get("engine_signature", "") or "").strip().lower()
        model_type = cls.normalize_model_type(cfg.get("engine_model_type", ""))
        matches: list[str] = []
        for cand in cls.local_models_for_task(task):
            cand_type = cls.normalize_model_type(cand.get("model_type", ""))
            cand_sig = str(cand.get("signature", "") or "").strip().lower()
            if model_type and cand_type != model_type:
                continue
            if sig and cand_sig != sig:
                continue
            matches.append(str(cand.get("name", "")).strip())

        if sig and len(matches) == 1:
            return matches[0]
        return cls.MISSING_VALUE

    @classmethod
    def active_engine_name(cls, *, task: str) -> str:
        task_id = str(task or "").strip().lower()
        engine_dir = cls.TRANSLATION_ENGINE_DIR if task_id == "translation" else cls.TRANSCRIPTION_ENGINE_DIR
        name = str(getattr(engine_dir, "name", "") or "").strip()
        if not name or name == cls.MISSING_VALUE:
            return ""
        return name

    # ----- Model resolution -----
    @classmethod
    def resolve_transcription_engine_name(cls, model: dict[str, Any]) -> str:
        cfg = model.get("transcription_model", {}) if isinstance(model, dict) else {}
        return cls.resolve_model_engine_name(cfg if isinstance(cfg, dict) else {}, task="transcription")

    @classmethod
    def resolve_translation_engine_name(cls, model: dict[str, Any]) -> str:
        cfg = model.get("translation_model", {}) if isinstance(model, dict) else {}
        return cls.resolve_model_engine_name(cfg if isinstance(cfg, dict) else {}, task="translation")

    # ----- Root mapping -----

    @classmethod
    def set_root_dir(cls, root_dir: Path) -> None:
        cls.ROOT_DIR = Path(root_dir).resolve()
        cls._DEFAULT_SETTINGS_CACHE = None
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
        cls.TRANSCRIPTION_ENGINE_DIR = cls.AI_MODELS_DIR / cls.MISSING_VALUE
        cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / cls.MISSING_VALUE
        cls.DATA_DIR = cls.ROOT_DIR / "userdata"
        cls.DOWNLOADS_DIR = cls.DATA_DIR / "downloads"
        cls.TRANSCRIPTIONS_DIR = cls.DATA_DIR / "transcriptions"
        cls.LOGS_DIR = cls.DATA_DIR / "logs"
        cls.APP_LOG_PATH = cls.LOGS_DIR / cls.APP_LOG_NAME
        cls.CRASH_LOG_PATH = cls.LOGS_DIR / cls.CRASH_LOG_NAME
        cls.USER_CONFIG_DIR = cls.DATA_DIR / "config"
        cls.SETTINGS_FILE = cls.USER_CONFIG_DIR / "settings.json"
        cls.MODEL_CONFIG_DIR = cls.APP_DIR / "model" / "config"
        cls.DEFAULTS_FILE = cls.MODEL_CONFIG_DIR / "defaults.json"
        cls.DOWNLOADS_TMP_DIR = cls.DOWNLOADS_DIR / "._tmp"
        cls.TRANSCRIPTIONS_TMP_DIR = cls.TRANSCRIPTIONS_DIR / "._tmp"

    # ----- File input / download output extensions -----
    FILES_AUDIO_INPUT_EXTS: tuple[str, ...] = ("wav", "mp3", "flac", "m4a", "ogg", "aac")
    FILES_VIDEO_INPUT_EXTS: tuple[str, ...] = ("mp4", "webm", "mkv", "mov", "avi")

    DOWNLOAD_AUDIO_FORMAT_PROFILES: dict[str, dict[str, Any]] = {
        "wav": {"selector_exts": ("wav",), "postprocess": "extract_audio", "preferredcodec": "wav"},
        "mp3": {"selector_exts": ("mp3",), "postprocess": "extract_audio", "preferredcodec": "mp3"},
        "flac": {"selector_exts": ("flac",), "postprocess": "extract_audio", "preferredcodec": "flac"},
        "m4a": {"selector_exts": ("m4a", "mp4"), "postprocess": "extract_audio", "preferredcodec": "m4a"},
        "ogg": {"selector_exts": ("ogg", "opus", "webm"), "postprocess": "extract_audio", "preferredcodec": "ogg"},
        "aac": {"selector_exts": ("aac", "m4a", "mp4"), "postprocess": "extract_audio", "preferredcodec": "aac"},
    }
    DOWNLOAD_VIDEO_FORMAT_PROFILES: dict[str, dict[str, Any]] = {
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

    DOWNLOAD_AUDIO_OUTPUT_EXTS: tuple[str, ...] = tuple(DOWNLOAD_AUDIO_FORMAT_PROFILES.keys())
    DOWNLOAD_VIDEO_OUTPUT_EXTS: tuple[str, ...] = tuple(DOWNLOAD_VIDEO_FORMAT_PROFILES.keys())

    @classmethod
    def download_audio_format_profile(cls, ext: str) -> dict[str, Any]:
        return dict(cls.DOWNLOAD_AUDIO_FORMAT_PROFILES.get(str(ext or "").strip().lower(), {}))

    @classmethod
    def download_video_format_profile(cls, ext: str) -> dict[str, Any]:
        return dict(cls.DOWNLOAD_VIDEO_FORMAT_PROFILES.get(str(ext or "").strip().lower(), {}))

    @classmethod
    def resolve_download_contract(
        cls,
        *,
        kind: str,
        purpose: str,
        keep_output: bool,
        ext: str,
    ) -> dict[str, Any]:
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
    def normalize_language_choice_value(cls, value: Any) -> str:
        token = cls.normalize_policy_value(value)
        if token == "default-ui":
            return cls.LANGUAGE_DEFAULT_UI_VALUE
        return token

    @classmethod
    def is_auto_language_value(cls, value: Any) -> bool:
        return cls.normalize_language_choice_value(value) == cls.LANGUAGE_AUTO_VALUE

    @classmethod
    def is_download_audio_auto_value(cls, value: Any) -> bool:
        token = cls.normalize_policy_value(value)
        return token in set(cls.DOWNLOAD_AUDIO_LANG_AUTO_VALUES)

    @classmethod
    def is_translation_source_deferred_value(cls, value: Any) -> bool:
        token = cls.normalize_language_choice_value(value)
        return (not token) or token in set(cls.TRANSLATION_SOURCE_DEFERRED_VALUES)

    @classmethod
    def is_translation_target_deferred_value(cls, value: Any) -> bool:
        token = cls.normalize_language_choice_value(value)
        return (not token) or token in set(cls.TRANSLATION_TARGET_DEFERRED_VALUES)

    @classmethod
    def files_audio_input_file_exts(cls) -> tuple[str, ...]:
        return tuple(f".{x}" for x in cls.FILES_AUDIO_INPUT_EXTS)

    @classmethod
    def files_video_input_file_exts(cls) -> tuple[str, ...]:
        return tuple(f".{x}" for x in cls.FILES_VIDEO_INPUT_EXTS)

    @classmethod
    def files_media_input_file_exts(cls) -> tuple[str, ...]:
        exts = {e.lower() for e in cls.files_audio_input_file_exts()}
        exts |= {e.lower() for e in cls.files_video_input_file_exts()}
        return tuple(sorted(exts))

    # ----- Live transcription -----
    LIVE_OUTPUT_MODE_STREAM: str = "stream"
    LIVE_OUTPUT_MODE_CUMULATIVE: str = "cumulative"
    LIVE_OUTPUT_MODES: tuple[str, ...] = (LIVE_OUTPUT_MODE_STREAM, LIVE_OUTPUT_MODE_CUMULATIVE)
    LIVE_UI_MODE_TRANSCRIBE: str = "transcribe"
    LIVE_UI_MODE_TRANSCRIBE_TRANSLATE: str = "transcribe_translate"
    LIVE_UI_MODES: tuple[str, ...] = (LIVE_UI_MODE_TRANSCRIBE, LIVE_UI_MODE_TRANSCRIBE_TRANSLATE)
    LIVE_UI_DEFAULT_MODE: str = LIVE_UI_MODE_TRANSCRIBE
    LIVE_DEFAULT_PRESET: str = "balanced"
    LIVE_PRESET_IDS: tuple[str, ...] = ("low_latency", "balanced", "high_context")

    LIVE_AUDIO_SIGNAL_PROFILE: dict[str, Any] = {
        "silence_level_threshold": 0.055,
        "silence_audio_rms_min": 0.007,
        "silence_tail_keep_s": 0.24,
        "tail_flush_min_s": 0.20,
        "weak_rms_threshold": 0.0115,
        "weak_activity_floor": 0.0055,
        "weak_active_ratio_threshold": 0.012,
        "weak_active_ms_threshold": 55.0,
        "solid_rms_threshold": 0.0145,
        "solid_activity_floor": 0.0065,
        "solid_active_ratio_threshold": 0.022,
        "solid_active_ms_threshold": 85.0,
        "language_detect_rms_threshold": 0.03,
        "language_detect_activity_floor": 0.009,
        "language_detect_active_ratio_threshold": 0.07,
        "language_detect_active_ms_threshold": 160.0,
        "artifact_min_chars": 3,
        "artifact_min_words": 2,
        "artifact_tail_max_words": 2,
        "artifact_tail_max_chars": 14,
    }

    LIVE_PRESET_PROFILES: dict[str, dict[str, Any]] = {
        "low_latency": {
            "chunk_length_s": 3,
            "stride_length_s": 2,
            "stream_commit_silence_s": 0.52,
            "cumulative_commit_silence_s": 0.58,
            "stream_clear_after_s": 1.05,
            "stream_replace_prefix_ratio": 0.58,
            "stream_commit_min_words": 5,
            "cumulative_merge_overlap_min": 2,
            "stream_show_previous_caption": False,
            "stream_max_pending_chunks": 2,
            "cumulative_max_pending_chunks": 3,
            "stream_translation_min_chars": 16,
            "cumulative_translation_min_chars": 18,
        },
        "balanced": {
            "chunk_length_s": 5,
            "stride_length_s": 4,
            "stream_commit_silence_s": 0.64,
            "cumulative_commit_silence_s": 0.72,
            "stream_clear_after_s": 1.30,
            "stream_replace_prefix_ratio": 0.64,
            "stream_commit_min_words": 6,
            "cumulative_merge_overlap_min": 2,
            "stream_show_previous_caption": False,
            "stream_max_pending_chunks": 3,
            "cumulative_max_pending_chunks": 4,
            "stream_translation_min_chars": 18,
            "cumulative_translation_min_chars": 20,
        },
        "high_context": {
            "chunk_length_s": 7,
            "stride_length_s": 5,
            "stream_commit_silence_s": 0.78,
            "cumulative_commit_silence_s": 0.86,
            "stream_clear_after_s": 1.55,
            "stream_replace_prefix_ratio": 0.70,
            "stream_commit_min_words": 7,
            "cumulative_merge_overlap_min": 3,
            "stream_show_previous_caption": False,
            "stream_max_pending_chunks": 3,
            "cumulative_max_pending_chunks": 5,
            "stream_translation_min_chars": 22,
            "cumulative_translation_min_chars": 24,
        },
    }

    @classmethod
    def live_ui_cfg_dict(cls) -> dict[str, Any]:
        app_cfg = cls._snapshot_section_dict("app")
        ui_cfg = app_cfg.get("ui", {}) if isinstance(app_cfg.get("ui"), dict) else {}
        live_cfg = ui_cfg.get("live", {}) if isinstance(ui_cfg.get("live"), dict) else {}
        return dict(live_cfg) if isinstance(live_cfg, dict) else {}

    @classmethod
    def live_ui_mode(cls) -> str:
        cfg = cls.live_ui_cfg_dict()
        return cls.normalize_live_ui_mode(cfg.get("mode"))

    @classmethod
    def live_ui_device_name(cls) -> str:
        cfg = cls.live_ui_cfg_dict()
        return str(cfg.get("device_name") or "").strip()

    @classmethod
    def live_ui_preset(cls) -> str:
        cfg = cls.live_ui_cfg_dict()
        return cls.normalize_live_preset(cfg.get("preset"))

    @classmethod
    def live_ui_output_mode(cls) -> str:
        cfg = cls.live_ui_cfg_dict()
        return cls.normalize_live_output_mode(cfg.get("output_mode"))

    @classmethod
    def live_ui_show_source(cls) -> bool:
        cfg = cls.live_ui_cfg_dict()
        return bool(cfg.get("show_source"))

    @classmethod
    def normalize_live_ui_mode(cls, value: Any) -> str:
        token = cls.normalize_policy_value(value)
        if token not in set(cls.LIVE_UI_MODES):
            return cls.LIVE_UI_DEFAULT_MODE
        return token

    @classmethod
    def normalize_live_output_mode(cls, value: Any) -> str:
        token = cls.normalize_policy_value(value)
        if token not in set(cls.LIVE_OUTPUT_MODES):
            return cls.LIVE_OUTPUT_MODE_CUMULATIVE
        return token

    @classmethod
    def normalize_live_preset(cls, value: Any) -> str:
        token = cls.normalize_policy_value(value)
        if token not in set(cls.LIVE_PRESET_IDS):
            return cls.LIVE_DEFAULT_PRESET
        return token

    @classmethod
    def live_audio_profile(cls) -> dict[str, Any]:
        return dict(cls.LIVE_AUDIO_SIGNAL_PROFILE)

    @classmethod
    def live_preset_profile(cls, preset: Any) -> dict[str, Any]:
        preset_id = cls.normalize_live_preset(preset)
        profile = cls.LIVE_PRESET_PROFILES.get(preset_id) or cls.LIVE_PRESET_PROFILES[cls.LIVE_DEFAULT_PRESET]
        return dict(profile)

    @classmethod
    def live_runtime_profile(cls, *, output_mode: Any, preset: Any) -> dict[str, Any]:
        output_mode_id = cls.normalize_live_output_mode(output_mode)
        preset_id = cls.normalize_live_preset(preset)
        profile = cls.live_audio_profile()
        profile.update(cls.live_preset_profile(preset_id))
        profile["output_mode"] = output_mode_id
        profile["preset_id"] = preset_id
        profile["stream_mode"] = output_mode_id == cls.LIVE_OUTPUT_MODE_STREAM
        profile["commit_silence_s"] = float(
            profile["stream_commit_silence_s"]
            if profile["stream_mode"]
            else profile["cumulative_commit_silence_s"]
        )
        profile["max_pending_chunks"] = int(
            profile["stream_max_pending_chunks"]
            if profile["stream_mode"]
            else profile["cumulative_max_pending_chunks"]
        )
        return profile

    # ----- Transcript output -----
    TRANSCRIPTION_OUTPUT_MODES: tuple[dict[str, Any], ...] = (
        {"id": "txt", "ext": "txt", "timestamps": False, "tr_key": "transcription.output_mode.plain_txt.label"},
        {"id": "txt_ts", "ext": "txt", "timestamps": True, "tr_key": "transcription.output_mode.txt_timestamps.label"},
        {"id": "srt", "ext": "srt", "timestamps": True, "tr_key": "transcription.output_mode.srt.label"},
    )

    _TRANSCRIPT_FILENAMES: dict[str, str] = {
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
    def get_transcription_output_modes(cls) -> tuple[dict[str, Any], ...]:
        return cls.TRANSCRIPTION_OUTPUT_MODES

    @classmethod
    def get_transcription_output_mode(cls, mode_id: str) -> dict[str, Any]:
        mid = str(mode_id or "txt").strip().lower()
        for mode in cls.TRANSCRIPTION_OUTPUT_MODES:
            if str(mode.get("id", "")).lower() == mid:
                return mode
        return cls.TRANSCRIPTION_OUTPUT_MODES[0]

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
    _DEFAULT_SETTINGS_CACHE: dict[str, Any] | None = None

    # ----- Snapshot accessors (UI/Controller helpers) -----
    @classmethod
    def _default_settings_dict(cls) -> dict[str, Any]:
        cache = cls._DEFAULT_SETTINGS_CACHE
        if isinstance(cache, dict):
            return cache

        if not cls.DEFAULTS_FILE.exists():
            raise ConfigError("error.settings.defaults_missing", path=str(cls.DEFAULTS_FILE))

        try:
            raw = json.loads(cls.DEFAULTS_FILE.read_text(encoding="utf-8"))
        except Exception as ex:
            raise ConfigError("error.settings.json_invalid", path=str(cls.DEFAULTS_FILE), detail=str(ex))

        if not isinstance(raw, dict):
            raise ConfigError("error.settings.section_invalid", section="root")

        cls._DEFAULT_SETTINGS_CACHE = raw
        return cls._DEFAULT_SETTINGS_CACHE

    @classmethod
    def _default_section_dict(cls, section_name: str) -> dict[str, Any]:
        defaults = cls._default_settings_dict()
        section = defaults.get(section_name, cls._UNSET)
        if not isinstance(section, dict):
            raise ConfigError("error.settings.section_invalid", section=str(section_name))
        return dict(section)

    @classmethod
    def _snapshot_section_value(cls, section_name: str, key: str) -> Any:
        snap = cls.SETTINGS
        if snap is not None:
            section = getattr(snap, section_name, {})
            if isinstance(section, dict):
                value = section.get(key, cls._UNSET)
                if value is not cls._UNSET and value is not None:
                    if not isinstance(value, str) or value.strip():
                        return value

        defaults = cls._default_section_dict(section_name)
        value = defaults.get(key, cls._UNSET)
        if value is cls._UNSET:
            raise ConfigError("error.settings.section_invalid", section=f"{section_name}.{key}")
        return value

    @classmethod
    def _snapshot_section_dict(cls, section_name: str) -> dict[str, Any]:
        """Return a shallow dict copy of a SettingsSnapshot section or its defaults."""

        merged = cls._default_section_dict(section_name)
        snap = cls.SETTINGS
        if snap is None:
            return merged
        sec = getattr(snap, section_name, {})
        if isinstance(sec, dict):
            merged.update(sec)
        return merged

    @classmethod
    def transcription_cfg_dict(cls) -> dict[str, Any]:
        return cls._snapshot_section_dict("transcription")

    @classmethod
    def translation_cfg_dict(cls) -> dict[str, Any]:
        return cls._snapshot_section_dict("translation")

    @classmethod
    def translation_source_language(cls) -> str:
        value = cls.normalize_language_choice_value(
            cls._snapshot_section_value("translation", "source_language") or cls.LANGUAGE_AUTO_VALUE
        )
        return value or cls.LANGUAGE_AUTO_VALUE

    @classmethod
    def translation_target_language(cls) -> str:
        value = cls.normalize_language_choice_value(
            cls._snapshot_section_value("translation", "target_language") or cls.LANGUAGE_DEFAULT_UI_VALUE
        )
        return value or cls.LANGUAGE_DEFAULT_UI_VALUE

    @classmethod
    def engine_cfg_dict(cls) -> dict[str, Any]:
        return cls._snapshot_section_dict("engine")

    @classmethod
    def model_cfg_dict(cls) -> dict[str, Any]:
        return cls._snapshot_section_dict("model")

    @classmethod
    def model_section_cfg_dict(cls, section_name: str) -> dict[str, Any]:
        model_cfg = cls.model_cfg_dict()
        section = model_cfg.get(str(section_name or "").strip(), {}) if isinstance(model_cfg, dict) else {}
        return dict(section) if isinstance(section, dict) else {}

    @classmethod
    def transcription_model_raw_cfg_dict(cls) -> dict[str, Any]:
        return cls.model_section_cfg_dict("transcription_model")

    @classmethod
    def translation_model_raw_cfg_dict(cls) -> dict[str, Any]:
        return cls.model_section_cfg_dict("translation_model")

    @classmethod
    def transcription_model_engine_name(cls) -> str:
        cfg = cls.transcription_model_raw_cfg_dict()
        return str(cfg.get("engine_name", "none") or "none").strip()

    @classmethod
    def translation_model_engine_name(cls) -> str:
        cfg = cls.translation_model_raw_cfg_dict()
        return str(cfg.get("engine_name", "none") or "none").strip()

    @classmethod
    def downloader_cfg_dict(cls) -> dict[str, Any]:
        return cls._snapshot_section_dict("downloader")

    @classmethod
    def network_cfg_dict(cls) -> dict[str, Any]:
        return cls._snapshot_section_dict("network")

    @classmethod
    def downloader_min_video_height(cls) -> int:
        raw = cls._snapshot_section_value("downloader", "min_video_height")
        return max(1, cls._coerce_int(raw, 1))

    @classmethod
    def downloader_max_video_height(cls) -> int:
        raw_max = cls._coerce_int(cls._snapshot_section_value("downloader", "max_video_height"), cls.downloader_min_video_height())
        return max(cls.downloader_min_video_height(), raw_max)

    @classmethod
    def network_max_bandwidth_kbps(cls) -> int | None:
        raw = cls._snapshot_section_value("network", "max_bandwidth_kbps")
        try:
            value = int(raw) if raw is not None else None
        except Exception:
            return None
        if value is not None and value <= 0:
            return None
        return value

    @classmethod
    def network_retries(cls) -> int:
        raw = cls._snapshot_section_value("network", "retries")
        return max(0, cls._coerce_int(raw, 0))

    @classmethod
    def network_concurrent_fragments(cls) -> int:
        raw = cls._snapshot_section_value("network", "concurrent_fragments")
        return max(1, cls._coerce_int(raw, 1))

    @classmethod
    def network_http_timeout_s(cls) -> int:
        raw = cls._snapshot_section_value("network", "http_timeout_s")
        return max(1, cls._coerce_int(raw, 1))

    @classmethod
    def network_no_check_certificate(cls) -> bool:
        return bool(cls._snapshot_section_value("network", "no_check_certificate"))

    @classmethod
    def engine_low_cpu_mem_usage(cls) -> bool:
        return bool(cls._snapshot_section_value("engine", "low_cpu_mem_usage"))

    @classmethod
    def transcription_output_mode_ids(cls) -> tuple[str, ...]:
        raw = cls._snapshot_section_value("transcription", "output_formats")
        if isinstance(raw, str) and raw.strip():
            selected = [raw.strip().lower()]
        elif isinstance(raw, (list, tuple)):
            selected = [str(x or "").strip().lower() for x in raw if str(x or "").strip()]
        else:
            selected = []

        valid_ids = {str(mode.get("id", "")).strip().lower() for mode in cls.TRANSCRIPTION_OUTPUT_MODES if mode.get("id")}
        norm: list[str] = []
        for mode_id in selected:
            if mode_id in valid_ids and mode_id not in norm:
                norm.append(mode_id)
        if not norm:
            norm.append(str(cls.TRANSCRIPTION_OUTPUT_MODES[0].get("id", "txt")).strip().lower() or "txt")
        return tuple(norm)

    @classmethod
    def transcription_output_default_ext(cls) -> str:
        mode_id = cls.transcription_output_mode_ids()[0]
        mode = cls.get_transcription_output_mode(mode_id)
        return str(mode.get("ext", "txt") or "txt").strip().lower().lstrip(".") or "txt"

    @classmethod
    def transcription_translate_after_enabled(cls) -> bool:
        return bool(cls._snapshot_section_value("transcription", "translate_after_transcription"))

    @classmethod
    def transcription_download_audio_only(cls) -> bool:
        return bool(cls._snapshot_section_value("transcription", "download_audio_only"))

    @classmethod
    def transcription_url_audio_ext(cls) -> str:
        value = str(cls._snapshot_section_value("transcription", "url_audio_ext") or "").strip().lower().lstrip(".")
        return value or "m4a"

    @classmethod
    def transcription_url_video_ext(cls) -> str:
        value = str(cls._snapshot_section_value("transcription", "url_video_ext") or "").strip().lower().lstrip(".")
        return value or "mp4"

    # ----- Snapshot mapping -----
    @classmethod
    def initialize_from_snapshot(cls, snap: "SettingsSnapshot") -> None:
        cls.SETTINGS = snap
        cls._apply_transcription_engine_dir(snap.model)
        cls._apply_translation_engine_dir(snap.model)

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
    def _apply_translation_engine_dir(cls, model: dict[str, Any]) -> None:
        tcfg = model.get("translation_model", {})
        resolved = cls.resolve_model_engine_name(tcfg if isinstance(tcfg, dict) else {}, task="translation")
        cls.TRANSLATION_ENGINE_DIR = cls.AI_MODELS_DIR / resolved

    @classmethod
    def _apply_transcription_engine_dir(cls, model: dict[str, Any]) -> None:
        tcfg = model.get("transcription_model", {})
        resolved = cls.resolve_model_engine_name(tcfg if isinstance(tcfg, dict) else {}, task="transcription")
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
    def runtime_capabilities(cls) -> dict[str, bool]:
        return {
            "has_cuda": bool(cls.HAS_CUDA),
            "bf16_supported": bool(cls.BF16_SUPPORTED),
            "tf32_supported": bool(cls.TF32_SUPPORTED),
        }
