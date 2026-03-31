# app/model/core/config/config.py
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.model.core.config.policy import LanguagePolicy
from app.model.core.config.paths import PathCatalog
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.errors import AppError
from app.model.download.policy import DownloadPolicy
from app.model.transcription.policy import TranscriptionOutputPolicy

if TYPE_CHECKING:
    from app.model.core.domain.entities import SettingsSnapshot


class ConfigError(AppError):
    """Key-based runtime configuration error."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


class AppConfig:
    """Global runtime configuration facade."""

    _UNSET = object()

    PATHS: PathCatalog

    MISSING_VALUE: str = "__missing__"

    APP_LOG_NAME: str = "app.log"
    CRASH_LOG_NAME: str = "crash.log"

    AUDIO_PROBE_TIMEOUT_S: float = 10.0
    ASR_SAMPLE_RATE: int = 16000
    ASR_CHANNELS: int = 1
    ASR_WAV_FORMAT_TOKEN: str = "wav"
    ASR_WAV_CODEC_PREFIX: str = "pcm_"

    TRANSCRIPTION_MODEL_TOKENIZER_FILE: str = "tokenizer_config.json"
    TRANSLATION_MODEL_TOKENIZER_FILE: str = "special_tokens_map.json"

    BULK_ADD_CONFIRMATION_MIN_THRESHOLD: int = 2
    BULK_ADD_CONFIRMATION_MAX_THRESHOLD: int = 1000
    BULK_ADD_CONFIRMATION_DEFAULT_THRESHOLD: int = 20

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

    @classmethod
    def transcription_model_tokenizer_path(cls) -> Path:
        return cls.PATHS.TRANSCRIPTION_ENGINE_DIR / cls.TRANSCRIPTION_MODEL_TOKENIZER_FILE

    @classmethod
    def translation_model_tokenizer_path(cls) -> Path:
        return cls.PATHS.TRANSLATION_ENGINE_DIR / cls.TRANSLATION_MODEL_TOKENIZER_FILE

    @classmethod
    def set_root_dir(cls, root_dir: Path, *, install_root: Path | None = None) -> None:
        cls._DEFAULT_SETTINGS_CACHE = None
        cls.PATHS = PathCatalog.build(
            root_dir,
            install_root_dir=install_root,
            app_log_name=cls.APP_LOG_NAME,
            crash_log_name=cls.CRASH_LOG_NAME,
            missing_value=cls.MISSING_VALUE,
        )

    @classmethod
    def live_ui_cfg_dict(cls) -> dict[str, Any]:
        app_cfg = cls._snapshot_section_dict("app")
        ui_cfg = app_cfg.get("ui", {}) if isinstance(app_cfg.get("ui"), dict) else {}
        live_cfg = ui_cfg.get("live", {}) if isinstance(ui_cfg.get("live"), dict) else {}
        return dict(live_cfg) if isinstance(live_cfg, dict) else {}

    @classmethod
    def welcome_dialog_cfg_dict(cls) -> dict[str, Any]:
        app_cfg = cls._snapshot_section_dict("app")
        ui_cfg = app_cfg.get("ui", {}) if isinstance(app_cfg.get("ui"), dict) else {}
        welcome_cfg = ui_cfg.get("welcome_dialog", {}) if isinstance(ui_cfg.get("welcome_dialog"), dict) else {}
        return dict(welcome_cfg) if isinstance(welcome_cfg, dict) else {}

    @classmethod
    def ui_welcome_dialog_enabled(cls) -> bool:
        cfg = cls.welcome_dialog_cfg_dict()
        return bool(cfg.get("show_on_startup", True))

    @classmethod
    def source_rights_notice_cfg_dict(cls) -> dict[str, Any]:
        app_cfg = cls._snapshot_section_dict("app")
        ui_cfg = app_cfg.get("ui", {}) if isinstance(app_cfg.get("ui"), dict) else {}
        notice_cfg = (
            ui_cfg.get("source_rights_notice", {})
            if isinstance(ui_cfg.get("source_rights_notice"), dict)
            else {}
        )
        return dict(notice_cfg) if isinstance(notice_cfg, dict) else {}

    @classmethod
    def ui_source_rights_notice_enabled(cls) -> bool:
        cfg = cls.source_rights_notice_cfg_dict()
        return bool(cfg.get("show_on_add", True))

    @classmethod
    def bulk_add_confirmation_cfg_dict(cls) -> dict[str, Any]:
        app_cfg = cls._snapshot_section_dict("app")
        ui_cfg = app_cfg.get("ui", {}) if isinstance(app_cfg.get("ui"), dict) else {}
        bulk_cfg = (
            ui_cfg.get("bulk_add_confirmation", {})
            if isinstance(ui_cfg.get("bulk_add_confirmation"), dict)
            else {}
        )
        return dict(bulk_cfg) if isinstance(bulk_cfg, dict) else {}

    @classmethod
    def ui_bulk_add_confirmation_enabled(cls) -> bool:
        cfg = cls.bulk_add_confirmation_cfg_dict()
        return bool(cfg.get("enabled", True))

    @classmethod
    def ui_bulk_add_confirmation_threshold(cls) -> int:
        cfg = cls.bulk_add_confirmation_cfg_dict()
        raw = cfg.get("threshold", cls.BULK_ADD_CONFIRMATION_DEFAULT_THRESHOLD)
        threshold = cls._coerce_int(raw, cls.BULK_ADD_CONFIRMATION_DEFAULT_THRESHOLD)
        return max(cls.BULK_ADD_CONFIRMATION_MIN_THRESHOLD, min(cls.BULK_ADD_CONFIRMATION_MAX_THRESHOLD, threshold))

    @classmethod
    def files_ui_cfg_dict(cls) -> dict[str, Any]:
        app_cfg = cls._snapshot_section_dict("app")
        ui_cfg = app_cfg.get("ui", {}) if isinstance(app_cfg.get("ui"), dict) else {}
        files_cfg = ui_cfg.get("files", {}) if isinstance(ui_cfg.get("files"), dict) else {}
        return dict(files_cfg) if isinstance(files_cfg, dict) else {}

    @classmethod
    def _ui_tab_cfg_dict(cls, tab_name: str) -> dict[str, Any]:
        tab = str(tab_name or "").strip().lower()
        if tab == "files":
            return cls.files_ui_cfg_dict()
        if tab == "live":
            return cls.live_ui_cfg_dict()
        return {}

    @classmethod
    def default_source_language_policy(cls) -> str:
        cfg = cls.transcription_cfg_dict()
        value = cfg.get("default_source_language", LanguagePolicy.AUTO)
        return LanguagePolicy.normalize_default_source_language_policy(value)

    @classmethod
    def default_target_language_policy(cls) -> str:
        cfg = cls.translation_cfg_dict()
        value = cfg.get("default_target_language", LanguagePolicy.DEFAULT_UI)
        return LanguagePolicy.normalize_default_target_language_policy(value)

    @classmethod
    def _tab_last_used_source_language(cls, tab_name: str) -> str:
        cfg = cls._ui_tab_cfg_dict(tab_name)
        return LanguagePolicy.normalize_last_used_source_language(
            cfg.get("last_used_source_language", LanguagePolicy.AUTO)
        )

    @classmethod
    def _tab_last_used_target_language(cls, tab_name: str) -> str:
        cfg = cls._ui_tab_cfg_dict(tab_name)
        return LanguagePolicy.normalize_last_used_target_language(
            cfg.get("last_used_target_language", LanguagePolicy.DEFAULT_UI)
        )

    @classmethod
    def resolve_default_source_language_for_tab(cls, tab_name: str) -> str:
        policy = cls.default_source_language_policy()
        if LanguagePolicy.is_last_used(policy):
            return cls._tab_last_used_source_language(tab_name)
        if LanguagePolicy.is_auto(policy):
            return LanguagePolicy.AUTO
        norm = LanguagePolicy.normalize_code(policy, drop_region=False)
        return norm or LanguagePolicy.AUTO

    @classmethod
    def resolve_default_target_language_for_tab(cls, tab_name: str, ui_language: str | None = None) -> str:
        policy = cls.default_target_language_policy()
        if LanguagePolicy.is_last_used(policy):
            policy = cls._tab_last_used_target_language(tab_name)
        if LanguagePolicy.is_default_ui(policy):
            try:
                from app.model.core.runtime.localization import current_language
            except (ImportError, AttributeError, RuntimeError):
                resolved_ui = str(ui_language or "").strip().lower()
            else:
                resolved_ui = str(ui_language or current_language()).strip().lower()
            norm_ui = LanguagePolicy.normalize_code(resolved_ui, drop_region=True)
            return norm_ui or LanguagePolicy.DEFAULT_UI
        norm = LanguagePolicy.normalize_code(policy, drop_region=False)
        return norm or LanguagePolicy.DEFAULT_UI

    @classmethod
    def live_ui_mode(cls) -> str:
        cfg = cls.live_ui_cfg_dict()
        return RuntimeProfiles.normalize_live_ui_mode(cfg.get("mode"))

    @classmethod
    def live_ui_device_name(cls) -> str:
        cfg = cls.live_ui_cfg_dict()
        return str(cfg.get("device_name") or "").strip()

    @classmethod
    def live_ui_profile(cls) -> str:
        cfg = cls.live_ui_cfg_dict()
        return RuntimeProfiles.normalize_live_profile(cfg.get("profile"))


    @classmethod
    def live_ui_output_mode(cls) -> str:
        cfg = cls.live_ui_cfg_dict()
        return RuntimeProfiles.normalize_live_output_mode(cfg.get("output_mode"))

    @classmethod
    def _default_settings_dict(cls) -> dict[str, Any]:
        cache = cls._DEFAULT_SETTINGS_CACHE
        if isinstance(cache, dict):
            return cache

        if not cls.PATHS.DEFAULTS_FILE.exists():
            raise ConfigError("error.settings.defaults_missing", path=str(cls.PATHS.DEFAULTS_FILE))

        try:
            raw = json.loads(cls.PATHS.DEFAULTS_FILE.read_text(encoding="utf-8"))
        except Exception as ex:
            raise ConfigError("error.settings.json_invalid", path=str(cls.PATHS.DEFAULTS_FILE), detail=str(ex))

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
    def browser_cookies_cfg_dict(cls) -> dict[str, Any]:
        return cls._snapshot_section_dict("browser_cookies")

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
    def downloader_min_video_height(cls) -> int:
        raw = cls._snapshot_section_value("downloader", "min_video_height")
        return max(1, cls._coerce_int(raw, 1))

    @classmethod
    def downloader_max_video_height(cls) -> int:
        raw_max = cls._coerce_int(
            cls._snapshot_section_value("downloader", "max_video_height"),
            cls.downloader_min_video_height(),
        )
        return max(cls.downloader_min_video_height(), raw_max)

    @classmethod
    def network_max_bandwidth_kbps(cls) -> int | None:
        raw = cls._snapshot_section_value("network", "max_bandwidth_kbps")
        try:
            value = int(raw) if raw is not None else None
        except (TypeError, ValueError):
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
    def browser_cookies_mode(cls) -> str:
        cfg = cls.browser_cookies_cfg_dict()
        fallback = DownloadPolicy.COOKIE_BROWSER_MODES[0]
        return DownloadPolicy.normalize_cookie_browser_mode(cfg.get("mode", fallback))

    @classmethod
    def browser_cookie_browser_policy(cls) -> str:
        cfg = cls.browser_cookies_cfg_dict()
        fallback = DownloadPolicy.COOKIE_BROWSER_POLICIES[0]
        browser = str(cfg.get("browser", fallback) or fallback).strip().lower()
        return browser if browser in DownloadPolicy.COOKIE_BROWSER_POLICIES else fallback

    @classmethod
    def browser_cookie_file_path(cls) -> str:
        cfg = cls.browser_cookies_cfg_dict()
        return str(cfg.get("file_path") or "").strip()

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

        valid_ids = set(TranscriptionOutputPolicy.valid_mode_ids())
        norm: list[str] = []
        for mode_id in selected:
            if mode_id in valid_ids and mode_id not in norm:
                norm.append(mode_id)
        if not norm:
            fallback = next(iter(TranscriptionOutputPolicy.valid_mode_ids()), "txt")
            norm.append(str(fallback or "txt"))
        return tuple(norm)

    @classmethod
    def transcription_output_default_ext(cls) -> str:
        mode_id = cls.transcription_output_mode_ids()[0]
        mode = TranscriptionOutputPolicy.get_transcription_output_mode(mode_id)
        return str(mode.get("ext", "txt") or "txt").strip().lower().lstrip(".") or "txt"

    @classmethod
    def transcription_translate_after_enabled(cls) -> bool:
        return bool(cls._snapshot_section_value("transcription", "translate_after_transcription"))


    @classmethod
    def transcription_url_audio_ext(cls) -> str:
        value = str(cls._snapshot_section_value("transcription", "url_audio_ext") or "").strip().lower().lstrip(".")
        return value or "m4a"

    @classmethod
    def transcription_url_video_ext(cls) -> str:
        value = str(cls._snapshot_section_value("transcription", "url_video_ext") or "").strip().lower().lstrip(".")
        return value or "mp4"

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

    @classmethod
    def ensure_dirs(cls) -> None:
        PathCatalog.ensure_runtime_dirs(cls.PATHS)

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _apply_translation_engine_dir(cls, model: dict[str, Any]) -> None:
        tcfg = model.get("translation_model", {})
        from app.model.engines.resolution import EngineResolver
        resolved = EngineResolver.resolve_model_engine_name(
            tcfg if isinstance(tcfg, dict) else {},
            task="translation",
        )
        cls.PATHS.TRANSLATION_ENGINE_DIR = cls.PATHS.AI_MODELS_DIR / resolved

    @classmethod
    def _apply_transcription_engine_dir(cls, model: dict[str, Any]) -> None:
        tcfg = model.get("transcription_model", {})
        from app.model.engines.resolution import EngineResolver
        resolved = EngineResolver.resolve_model_engine_name(
            tcfg if isinstance(tcfg, dict) else {},
            task="transcription",
        )
        cls.PATHS.TRANSCRIPTION_ENGINE_DIR = cls.PATHS.AI_MODELS_DIR / resolved

    @classmethod
    def has_cuda(cls) -> bool:
        return bool(cls.HAS_CUDA)

    @classmethod
    def auto_device_key(cls) -> str:
        return "cuda" if cls.has_cuda() else "cpu"

    @classmethod
    def auto_precision_key(cls) -> str:
        if cls.has_cuda():
            if bool(cls.BF16_SUPPORTED):
                return "bfloat16"
            return "float16"
        return "float32"

    @classmethod
    def is_fp32_math_mode_applicable(cls, device_key: str, precision_key: str) -> bool:
        raw_device = str(device_key or LanguagePolicy.AUTO).strip().lower()
        raw_precision = str(precision_key or LanguagePolicy.AUTO).strip().lower()

        resolved_device = cls.auto_device_key() if raw_device == LanguagePolicy.AUTO else raw_device
        resolved_precision = cls.auto_precision_key() if raw_precision == LanguagePolicy.AUTO else raw_precision

        return bool(
            resolved_device.startswith("cuda")
            and resolved_precision == "float32"
            and bool(cls.TF32_SUPPORTED)
        )

    @classmethod
    def runtime_capabilities(cls) -> dict[str, bool]:
        return {
            "has_cuda": bool(cls.HAS_CUDA),
            "bf16_supported": bool(cls.BF16_SUPPORTED),
            "tf32_supported": bool(cls.TF32_SUPPORTED),
        }


AppConfig.PATHS = PathCatalog.build(
    Path(__file__).resolve().parents[4],
    app_log_name=AppConfig.APP_LOG_NAME,
    crash_log_name=AppConfig.CRASH_LOG_NAME,
    missing_value=AppConfig.MISSING_VALUE,
)
