# app/model/settings/validation.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.model.core.config.config import AppConfig
from app.model.core.config.policy import LanguagePolicy
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.entities import SettingsSnapshot
from app.model.core.domain.errors import AppError
from app.model.core.utils.string_utils import normalize_lang_code
from app.model.download.policy import DownloadPolicy
from app.model.engines.capabilities import transcription_language_codes, translation_language_codes
from app.model.engines.registry import ModelRegistry
from app.model.engines.resolution import EngineResolver
from app.model.transcription.policy import TranscriptionOutputPolicy

_LANG_CODE_RE = re.compile(r"^[a-z]{2,3}([_-][a-z]{2,4})?$", re.IGNORECASE)


class SettingsError(AppError):
    """Key-based settings error for validation and persistence failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


def _ensure_dict(obj: Any, section: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise SettingsError("error.settings.section_invalid", section=section)
    return obj


def _merge(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    out = dict(schema)
    out.update(src)
    return out


def _enum_str(val: Any, allowed: tuple[str, ...], field: str) -> str:
    normalized = str(val).strip().lower()
    if normalized not in allowed:
        raise SettingsError("error.type.enum", field=field, allowed=", ".join(allowed))
    return normalized


def _as_int(val: Any, field: str) -> int:
    if not isinstance(val, int):
        raise SettingsError("error.type.int", field=field)
    return int(val)


def _schema_value(src: dict[str, Any], schema: dict[str, Any], key: str, default: Any) -> Any:
    value = src.get(key)
    if isinstance(value, str):
        value = value.strip()
    if value not in (None, ""):
        return value
    return schema.get(key, default)


def _optional_normalized(raw: Any, *, normalize_fn) -> Any:
    if raw in (None, ""):
        return None
    return normalize_fn(raw)


def _optional_bounded_int(raw: Any, *, minimum: int, maximum: int) -> int | None:
    if raw in (None, ""):
        return None
    try:
        num = int(raw)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, num))


def _looks_like_lang_code(value: str) -> bool:
    return bool(_LANG_CODE_RE.match((value or "").strip()))


def _coerce_lang_code(raw: str) -> str:
    normalized = normalize_lang_code(str(raw or "").strip(), drop_region=False)
    return normalized or ""


def _validate_app(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    src = _merge(src, schema)

    def _dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    logging_cfg = _merge(_dict(src.get("logging")), _dict(schema.get("logging")))
    logging_schema = _dict(schema.get("logging"))
    level = str(_schema_value(logging_cfg, logging_schema, "level", "warning") or "warning").strip().lower()
    if level not in ("debug", "info", "warning", "error"):
        level = str(logging_schema.get("level", "warning") or "warning").strip().lower()
        if level not in ("debug", "info", "warning", "error"):
            level = "warning"

    ui_schema = _dict(schema.get("ui"))
    ui_cfg = _merge(_dict(src.get("ui")), ui_schema)
    show_advanced = bool(_schema_value(ui_cfg, ui_schema, "show_advanced_settings", False))

    welcome_schema = _dict(ui_schema.get("welcome_dialog"))
    welcome_cfg = _merge(_dict(ui_cfg.get("welcome_dialog")), welcome_schema)
    welcome_show_on_startup = bool(_schema_value(welcome_cfg, welcome_schema, "show_on_startup", True))

    source_notice_schema = _dict(ui_schema.get("source_rights_notice"))
    source_notice_cfg = _merge(_dict(ui_cfg.get("source_rights_notice")), source_notice_schema)
    source_notice_show_on_add = bool(_schema_value(source_notice_cfg, source_notice_schema, "show_on_add", True))

    bulk_schema = _dict(ui_schema.get("bulk_add_confirmation"))
    bulk_cfg = _merge(_dict(ui_cfg.get("bulk_add_confirmation")), bulk_schema)
    bulk_enabled = bool(_schema_value(bulk_cfg, bulk_schema, "enabled", True))
    try:
        bulk_threshold = int(
            _schema_value(
                bulk_cfg,
                bulk_schema,
                "threshold",
                AppConfig.ui_bulk_add_confirmation_threshold(),
            )
        )
    except (TypeError, ValueError):
        bulk_threshold = int(AppConfig.ui_bulk_add_confirmation_threshold())
    bulk_threshold = max(
        AppConfig.BULK_ADD_CONFIRMATION_MIN_THRESHOLD,
        min(AppConfig.BULK_ADD_CONFIRMATION_MAX_THRESHOLD, bulk_threshold),
    )

    live_schema = _dict(ui_schema.get("live"))
    live_cfg = _merge(_dict(ui_cfg.get("live")), live_schema)
    files_schema = _dict(ui_schema.get("files"))
    files_cfg = _merge(_dict(ui_cfg.get("files")), files_schema)

    def _last_used_source(cfg: dict[str, Any], cfg_schema: dict[str, Any], field: str) -> str:
        raw = _schema_value(cfg, cfg_schema, field, LanguagePolicy.AUTO)
        value = LanguagePolicy.normalize_last_used_source_language(raw)
        supported = set(transcription_language_codes())
        if LanguagePolicy.is_auto(value):
            return LanguagePolicy.AUTO
        if supported:
            return value if value in supported else LanguagePolicy.AUTO
        return value if _looks_like_lang_code(value) else LanguagePolicy.AUTO

    def _last_used_target(cfg: dict[str, Any], cfg_schema: dict[str, Any], field: str) -> str:
        raw = _schema_value(cfg, cfg_schema, field, LanguagePolicy.DEFAULT_UI)
        value = LanguagePolicy.normalize_last_used_target_language(raw)
        supported = set(translation_language_codes())
        if LanguagePolicy.is_default_ui(value):
            return LanguagePolicy.DEFAULT_UI
        if supported:
            return value if value in supported else LanguagePolicy.DEFAULT_UI
        return value if _looks_like_lang_code(value) else LanguagePolicy.DEFAULT_UI

    return {
        "language": str(_schema_value(src, schema, "language", "auto") or "auto"),
        "theme": _enum_str(_schema_value(src, schema, "theme", "auto"), ("auto", "light", "dark"), "app.theme"),
        "logging": {
            "enabled": bool(_schema_value(logging_cfg, logging_schema, "enabled", True)),
            "level": level,
        },
        "ui": {
            "show_advanced_settings": show_advanced,
            "welcome_dialog": {"show_on_startup": welcome_show_on_startup},
            "source_rights_notice": {"show_on_add": source_notice_show_on_add},
            "bulk_add_confirmation": {"enabled": bulk_enabled, "threshold": bulk_threshold},
            "live": {
                "mode": _enum_str(
                    _schema_value(live_cfg, live_schema, "mode", RuntimeProfiles.LIVE_UI_DEFAULT_MODE),
                    RuntimeProfiles.LIVE_UI_MODES,
                    "app.ui.live.mode",
                ),
                "profile": RuntimeProfiles.normalize_live_profile(
                    _schema_value(live_cfg, live_schema, "profile", RuntimeProfiles.LIVE_DEFAULT_PROFILE)
                ),
                "output_mode": _enum_str(
                    _schema_value(
                        live_cfg,
                        live_schema,
                        "output_mode",
                        RuntimeProfiles.LIVE_OUTPUT_MODE_CUMULATIVE,
                    ),
                    RuntimeProfiles.LIVE_OUTPUT_MODES,
                    "app.ui.live.output_mode",
                ),
                "device_name": str(_schema_value(live_cfg, live_schema, "device_name", "") or "").strip(),
                "last_used_source_language": _last_used_source(
                    live_cfg,
                    live_schema,
                    "last_used_source_language",
                ),
                "last_used_target_language": _last_used_target(
                    live_cfg,
                    live_schema,
                    "last_used_target_language",
                ),
            },
            "files": {
                "last_used_source_language": _last_used_source(
                    files_cfg,
                    files_schema,
                    "last_used_source_language",
                ),
                "last_used_target_language": _last_used_target(
                    files_cfg,
                    files_schema,
                    "last_used_target_language",
                ),
            },
        },
    }


def _validate_engine(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    src = _merge(src, schema)
    return {
        "preferred_device": _enum_str(
            _schema_value(src, schema, "preferred_device", "auto"),
            ("auto", "cpu", "cuda"),
            "engine.preferred_device",
        ),
        "precision": _enum_str(
            _schema_value(src, schema, "precision", "auto"),
            ("auto", "float32", "float16", "bfloat16"),
            "engine.precision",
        ),
        "fp32_math_mode": _enum_str(
            _schema_value(src, schema, "fp32_math_mode", "ieee"),
            ("ieee", "tf32"),
            "engine.fp32_math_mode",
        ),
        "low_cpu_mem_usage": bool(_schema_value(src, schema, "low_cpu_mem_usage", True)),
    }


def _validate_model(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    src = _merge(src, schema)

    def _dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    t_schema = _dict(schema.get("transcription_model"))
    x_schema = _dict(schema.get("translation_model"))
    t = _merge(_dict(src.get("transcription_model")), t_schema)
    x = _merge(_dict(src.get("translation_model")), x_schema)
    t_adv_schema = _dict(t_schema.get("advanced"))
    x_adv_schema = _dict(x_schema.get("advanced"))
    t_adv = _merge(_dict(t.get("advanced")), t_adv_schema)
    x_adv = _merge(_dict(x.get("advanced")), x_adv_schema)

    transcription_profile = RuntimeProfiles.normalize_transcription_profile(
        _schema_value(t, t_schema, "profile", RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE)
    )
    translation_profile = RuntimeProfiles.normalize_translation_profile(
        _schema_value(x, x_schema, "profile", RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE)
    )

    def _engine_meta(cfg: dict[str, Any]) -> dict[str, str | None]:
        engine_name = str(cfg.get("engine_name", "none") or "none").strip()
        low = engine_name.lower()
        model_type = str(cfg.get("engine_model_type", "") or "").strip().lower() or None
        signature = str(cfg.get("engine_signature", "") or "").strip().lower() or None

        if ModelRegistry.is_disabled_engine_name(low) or low == "auto":
            return {"engine_model_type": None, "engine_signature": None}

        descriptor = EngineResolver.local_model_descriptor(engine_name)
        if descriptor:
            model_type = str(descriptor.get("model_type", model_type or "") or "").strip().lower() or None
            signature = str(descriptor.get("signature", signature or "") or "").strip().lower() or None

        return {"engine_model_type": model_type, "engine_signature": signature}

    def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            normalized = int(default)
        return max(minimum, min(maximum, normalized))

    t_meta = _engine_meta(t)
    x_meta = _engine_meta(x)

    return {
        "transcription_model": {
            "engine_name": str(_schema_value(t, t_schema, "engine_name", "none") or "none").strip(),
            "engine_model_type": t_meta["engine_model_type"],
            "engine_signature": t_meta["engine_signature"],
            "profile": transcription_profile,
            "ignore_warning": bool(_schema_value(t, t_schema, "ignore_warning", False)),
            "advanced": {
                "context_policy": _optional_normalized(
                    _schema_value(t_adv, t_adv_schema, "context_policy", None),
                    normalize_fn=RuntimeProfiles.normalize_context_policy,
                ),
                "silence_guard": _optional_normalized(
                    _schema_value(t_adv, t_adv_schema, "silence_guard", None),
                    normalize_fn=RuntimeProfiles.normalize_silence_guard,
                ),
                "language_stability": _optional_normalized(
                    _schema_value(t_adv, t_adv_schema, "language_stability", None),
                    normalize_fn=RuntimeProfiles.normalize_language_stability,
                ),
                "chunk_length_s": _optional_bounded_int(
                    _schema_value(t_adv, t_adv_schema, "chunk_length_s", None),
                    minimum=5,
                    maximum=120,
                ),
                "stride_length_s": _optional_bounded_int(
                    _schema_value(t_adv, t_adv_schema, "stride_length_s", None),
                    minimum=0,
                    maximum=30,
                ),
            },
        },
        "translation_model": {
            "engine_name": str(_schema_value(x, x_schema, "engine_name", "none") or "none").strip(),
            "engine_model_type": x_meta["engine_model_type"],
            "engine_signature": x_meta["engine_signature"],
            "profile": translation_profile,
            "max_new_tokens": _coerce_int(_schema_value(x, x_schema, "max_new_tokens", 256), 256, 16, 8192),
            "chunk_max_chars": _coerce_int(
                _schema_value(x, x_schema, "chunk_max_chars", 1200),
                1200,
                200,
                20000,
            ),
            "advanced": {
                "style": _optional_normalized(
                    _schema_value(x_adv, x_adv_schema, "style", None),
                    normalize_fn=RuntimeProfiles.normalize_translation_style,
                ),
                "num_beams": _optional_bounded_int(
                    _schema_value(x_adv, x_adv_schema, "num_beams", None),
                    minimum=1,
                    maximum=8,
                ),
                "no_repeat_ngram_size": _optional_bounded_int(
                    _schema_value(x_adv, x_adv_schema, "no_repeat_ngram_size", None),
                    minimum=0,
                    maximum=8,
                ),
            },
        },
    }


def _validate_transcription(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    src = _merge(src, schema)

    default_source_raw = LanguagePolicy.normalize_choice_value(
        _schema_value(src, schema, "default_source_language", LanguagePolicy.AUTO)
    )
    supported_source = set(transcription_language_codes())
    if LanguagePolicy.is_last_used(default_source_raw):
        default_source = LanguagePolicy.LAST_USED
    elif LanguagePolicy.is_auto(default_source_raw):
        default_source = LanguagePolicy.AUTO
    else:
        normalized_source = _coerce_lang_code(default_source_raw)
        if supported_source:
            default_source = normalized_source if normalized_source in supported_source else LanguagePolicy.AUTO
        else:
            default_source = normalized_source if _looks_like_lang_code(normalized_source) else LanguagePolicy.AUTO

    mode_ids = [
        str(mode.get("id", "")).strip().lower()
        for mode in TranscriptionOutputPolicy.get_transcription_output_modes()
    ]
    mode_ids = [mode_id for mode_id in mode_ids if mode_id]
    schema_formats = schema.get("output_formats")
    if isinstance(schema_formats, (list, tuple)):
        default_selected = [str(value or "").strip().lower() for value in schema_formats if str(value or "").strip()]
    else:
        default_selected = ["txt"]

    raw_formats = src.get("output_formats")
    if isinstance(raw_formats, (list, tuple)):
        selected = [str(value or "").strip().lower() for value in raw_formats if str(value or "").strip()]
    else:
        selected = list(default_selected)

    normalized_formats: list[str] = []
    seen: set[str] = set()
    for mode_id in selected:
        if mode_id in mode_ids and mode_id not in seen:
            normalized_formats.append(mode_id)
            seen.add(mode_id)
    if not normalized_formats:
        normalized_formats = default_selected[:1] if default_selected else ["txt"]

    return {
        "default_source_language": default_source,
        "output_formats": tuple(normalized_formats),
        "download_audio_only": bool(_schema_value(src, schema, "download_audio_only", True)),
        "url_keep_audio": bool(_schema_value(src, schema, "url_keep_audio", False)),
        "url_audio_ext": _enum_str(
            str(_schema_value(src, schema, "url_audio_ext", "m4a") or "m4a").strip().lower().lstrip("."),
            DownloadPolicy.DOWNLOAD_AUDIO_OUTPUT_EXTENSIONS,
            "transcription.url_audio_ext",
        ),
        "url_keep_video": bool(_schema_value(src, schema, "url_keep_video", False)),
        "url_video_ext": _enum_str(
            str(_schema_value(src, schema, "url_video_ext", "mp4") or "mp4").strip().lower().lstrip("."),
            DownloadPolicy.DOWNLOAD_VIDEO_OUTPUT_EXTENSIONS,
            "transcription.url_video_ext",
        ),
        "translate_after_transcription": bool(
            _schema_value(src, schema, "translate_after_transcription", False)
        ),
    }


def _validate_translation(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    src = _merge(src, schema)

    target_raw = LanguagePolicy.normalize_choice_value(
        src.get("default_target_language", "") or LanguagePolicy.DEFAULT_UI
    )
    deferred_target = LanguagePolicy.DEFAULT_UI
    if LanguagePolicy.is_last_used(target_raw):
        return {"default_target_language": LanguagePolicy.LAST_USED}

    target_codes = set(translation_language_codes())
    if LanguagePolicy.is_default_ui(target_raw) or not target_raw:
        target_language = deferred_target
    else:
        normalized = _coerce_lang_code(target_raw)
        if not normalized:
            target_language = deferred_target
        elif target_codes:
            target_language = normalized if normalized in target_codes else deferred_target
        else:
            target_language = normalized if _looks_like_lang_code(normalized) else deferred_target

    return {"default_target_language": target_language}


def _validate_downloader(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    src = _merge(src, schema)
    min_height = _as_int(src.get("min_video_height"), "downloader.min_video_height")
    max_height = _as_int(src.get("max_video_height"), "downloader.max_video_height")
    if min_height > max_height:
        raise SettingsError(
            "error.settings.invalid_video_height_range",
            min_height=min_height,
            max_height=max_height,
        )
    return {"min_video_height": min_height, "max_video_height": max_height}


def _validate_network(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    src = _merge(src, schema)
    max_bandwidth = src.get("max_bandwidth_kbps")
    if max_bandwidth is not None and not isinstance(max_bandwidth, int):
        raise SettingsError("error.type.int", field="network.max_bandwidth_kbps")
    return {
        "max_bandwidth_kbps": max_bandwidth,
        "retries": _as_int(src.get("retries"), "network.retries"),
        "concurrent_fragments": _as_int(src.get("concurrent_fragments"), "network.concurrent_fragments"),
        "http_timeout_s": _as_int(src.get("http_timeout_s"), "network.http_timeout_s"),
    }


def _validate_browser_cookies(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    src = _merge(src, schema)
    mode = _enum_str(
        _schema_value(src, schema, "mode", DownloadPolicy.COOKIE_BROWSER_MODES[0]),
        DownloadPolicy.COOKIE_BROWSER_MODES,
        "browser_cookies.mode",
    )
    browser = _enum_str(
        _schema_value(src, schema, "browser", DownloadPolicy.COOKIE_BROWSER_POLICIES[0]),
        DownloadPolicy.COOKIE_BROWSER_POLICIES,
        "browser_cookies.browser",
    )
    file_path = str(_schema_value(src, schema, "file_path", "") or "").strip()
    if mode == "from_file":
        cookie_path = Path(file_path)
        if not file_path:
            raise SettingsError("error.settings.cookie_file_path_missing")
        if not cookie_path.exists():
            raise SettingsError("error.settings.cookie_file_missing", path=str(cookie_path))
        if not cookie_path.is_file():
            raise SettingsError("error.settings.cookie_file_not_file", path=str(cookie_path))
        try:
            with cookie_path.open("rb") as handle:
                probe = handle.read(32)
        except OSError as ex:
            raise SettingsError("error.settings.cookie_file_unreadable", path=str(cookie_path), detail=str(ex)) from ex
        if not probe:
            raise SettingsError("error.settings.cookie_file_empty", path=str(cookie_path))
    return {
        "mode": mode,
        "browser": browser,
        "file_path": file_path,
    }


def validate_settings(defaults: dict[str, Any], settings: dict[str, Any]) -> SettingsSnapshot:
    """Validate raw settings against defaults and return an immutable snapshot."""
    schema_app = _ensure_dict(defaults.get("app", {}), "app")
    schema_engine = _ensure_dict(defaults.get("engine", {}), "engine")
    schema_model = _ensure_dict(defaults.get("model", {}), "model")
    schema_transcription = _ensure_dict(defaults.get("transcription", {}), "transcription")
    schema_translation = _ensure_dict(defaults.get("translation", {}), "translation")
    schema_downloader = _ensure_dict(defaults.get("downloader", {}), "downloader")
    schema_browser_cookies = _ensure_dict(defaults.get("browser_cookies", {}), "browser_cookies")
    schema_network = _ensure_dict(defaults.get("network", {}), "network")

    app = _ensure_dict(settings.get("app", {}), "app")
    engine = _ensure_dict(settings.get("engine", {}), "engine")
    model = _ensure_dict(settings.get("model", {}), "model")
    transcription = _ensure_dict(settings.get("transcription", {}), "transcription")
    translation = _ensure_dict(settings.get("translation", {}), "translation")
    downloader = _ensure_dict(settings.get("downloader", {}), "downloader")
    browser_cookies = _ensure_dict(settings.get("browser_cookies", {}), "browser_cookies")
    network = _ensure_dict(settings.get("network", {}), "network")

    return SettingsSnapshot(
        app=_validate_app(app, schema_app),
        engine=_validate_engine(engine, schema_engine),
        model=_validate_model(model, schema_model),
        transcription=_validate_transcription(transcription, schema_transcription),
        translation=_validate_translation(translation, schema_translation),
        downloader=_validate_downloader(downloader, schema_downloader),
        network=_validate_network(network, schema_network),
        browser_cookies=_validate_browser_cookies(browser_cookies, schema_browser_cookies),
    )
