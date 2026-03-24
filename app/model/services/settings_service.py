# app/model/services/settings_service.py
from __future__ import annotations

import json
import os
import logging
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.model.config.app_config import AppConfig as Config
from app.model.domain.entities import SettingsSnapshot, snapshot_to_dict
from app.model.domain.errors import AppError
from app.model.helpers.string_utils import normalize_lang_code

_LOG = logging.getLogger(__name__)

class SettingsError(AppError):
    """Key-based settings error for validation and persistence failures."""
    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))

class SettingsCatalog:
    """Lightweight catalog of user-facing options sourced from AppConfig."""

    @classmethod
    def transcription_output_modes(cls) -> tuple[dict[str, Any], ...]:
        return Config.get_transcription_output_modes()

    @classmethod
    def transcript_extensions(cls) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    str(m.get("ext", "")).strip().lower()
                    for m in cls.transcription_output_modes()
                    if m.get("ext")
                }
            )
        )

    @classmethod
    def download_audio_exts(cls) -> tuple[str, ...]:
        return tuple(Config.DOWNLOAD_AUDIO_OUTPUT_EXTS)

    @classmethod
    def download_video_exts(cls) -> tuple[str, ...]:
        return tuple(Config.DOWNLOAD_VIDEO_OUTPUT_EXTS)

    _LANG_CACHE: dict[str, tuple[float, set[str]]] = {}

    @staticmethod
    def _file_mtime(path: Path) -> float:
        try:
            return float(path.stat().st_mtime)
        except OSError:
            return 0.0

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError, TypeError, ValueError):
            return {}

    @classmethod
    def _cache_get(cls, key: str, mtime: float) -> set[str] | None:
        cur = cls._LANG_CACHE.get(key)
        if not cur:
            return None
        cached_mtime, cached_codes = cur
        if float(cached_mtime) == float(mtime):
            return set(cached_codes)
        return None

    @classmethod
    def _cache_put(cls, key: str, mtime: float, codes: set[str]) -> set[str]:
        out = {c for c in (codes or set()) if c}
        cls._LANG_CACHE[key] = (float(mtime), set(out))
        return set(out)

    @classmethod
    def _m2m100_language_codes(cls) -> set[str]:
        path = Config.translation_model_tokenizer_path()
        if not path.exists():
            return set()

        mtime = cls._file_mtime(path)
        cache_key = f"m2m100::{path}"
        cached = cls._cache_get(cache_key, mtime)
        if cached is not None:
            return cached

        data = cls._read_json(path)
        raw = data.get("additional_special_tokens")
        tokens = raw if isinstance(raw, list) else []

        codes: set[str] = set()
        for t in tokens:
            s = str(t or "").strip()
            if len(s) >= 4 and s.startswith("__") and s.endswith("__"):
                code = s[2:-2]
                norm = normalize_lang_code(code, drop_region=False)
                if norm:
                    codes.add(norm)

        return cls._cache_put(cache_key, mtime, codes)

    @classmethod
    def _whisper_language_codes(cls) -> set[str]:
        path = Config.transcription_model_tokenizer_path()
        if not path.exists():
            return set()

        mtime = cls._file_mtime(path)
        cache_key = f"whisper::{path}"
        cached = cls._cache_get(cache_key, mtime)
        if cached is not None:
            return cached

        data = cls._read_json(path)
        raw = data.get("additional_special_tokens")
        tokens = raw if isinstance(raw, list) else []

        codes: set[str] = set()
        for t in tokens:
            s = str(t or "").strip()
            if len(s) >= 6 and s.startswith("<|") and s.endswith("|>"):
                code = s[2:-2]
                if "|" in code:
                    continue
                norm = normalize_lang_code(code, drop_region=False)
                if norm:
                    codes.add(norm)

        return cls._cache_put(cache_key, mtime, codes)

    @classmethod
    def translation_language_codes(cls) -> set[str]:
        """Return supported translation language codes."""

        return cls._m2m100_language_codes()

    @classmethod
    def translation_target_allowed(cls) -> set[str]:
        return cls.translation_language_codes() | {Config.LANGUAGE_DEFAULT_UI_VALUE, Config.LANGUAGE_LAST_USED_VALUE}

    @classmethod
    def transcription_language_codes(cls) -> set[str]:
        """Return supported transcription language codes."""

        return cls._whisper_language_codes()

    @classmethod
    def transcription_language_allowed(cls) -> set[str]:
        return cls.transcription_language_codes() | {Config.LANGUAGE_AUTO_VALUE, Config.LANGUAGE_LAST_USED_VALUE}

    @classmethod
    def transcription_source_allowed(cls) -> set[str]:
        return cls.transcription_language_allowed()

class RuntimeConfigService:
    """Apply validated settings snapshots to runtime configuration state."""
    @staticmethod
    def initialize(config_cls: Any, snap: "SettingsSnapshot") -> None:
        config_cls.initialize_from_snapshot(snap)

    @staticmethod
    def update(
        config_cls: Any,
        snap: "SettingsSnapshot",
        *,
        sections: tuple[str, ...] = ("transcription", "translation"),
    ) -> None:
        config_cls.update_from_snapshot(snap, sections=sections)

    @staticmethod
    def ensure_dirs(config_cls: Any) -> None:
        config_cls.ensure_dirs()

    @staticmethod
    def setup_ffmpeg_on_path(config_cls: Any) -> None:
        bin_dir = config_cls.FFMPEG_DIR / "bin"
        config_cls.FFMPEG_BIN_DIR = bin_dir if bin_dir.exists() else config_cls.FFMPEG_DIR

        bin_dir_str = str(config_cls.FFMPEG_BIN_DIR)
        env_path = os.environ.get("PATH", "")
        if bin_dir_str not in env_path.split(os.pathsep):
            os.environ["PATH"] = bin_dir_str + os.pathsep + env_path

        os.environ.setdefault("FFMPEG_LOCATION", bin_dir_str)

        exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        probe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        ffmpeg_exe = config_cls.FFMPEG_BIN_DIR / exe
        ffprobe_exe = config_cls.FFMPEG_BIN_DIR / probe
        if ffmpeg_exe.exists():
            os.environ.setdefault("FFMPEG_BINARY", str(ffmpeg_exe))
            os.environ.setdefault("IMAGEIO_FFMPEG_EXE", str(ffmpeg_exe))
        if ffprobe_exe.exists():
            os.environ.setdefault("FFPROBE_BINARY", str(ffprobe_exe))

class SettingsService:
    """Load, validate, and persist application settings snapshots."""
    _LANG_CODE_RE = re.compile(r"^[a-z]{2,3}([_-][a-z]{2,4})?$", re.IGNORECASE)

    def __init__(
        self,
        *,
        defaults_path: Path | None = None,
        settings_path: Path | None = None,
    ) -> None:
        self._defaults_path = Path(defaults_path) if defaults_path else Config.DEFAULTS_FILE
        self._settings_path = Path(settings_path) if settings_path else Config.SETTINGS_FILE

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError, TypeError, ValueError) as ex:
            raise SettingsError("error.settings.json_invalid", path=str(path), detail=str(ex))
        if not isinstance(data, dict):
            raise SettingsError("error.settings.section_invalid", section="root")
        return data

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _ensure_dict(obj: Any, section: str) -> dict[str, Any]:
        if not isinstance(obj, dict):
            raise SettingsError("error.settings.section_invalid", section=section)
        return obj

    @staticmethod
    def _merge(src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        out = dict(schema)
        out.update(src)
        return out

    @staticmethod
    def _enum_str(val: Any, allowed: tuple[str, ...], field: str) -> str:
        s = str(val).strip().lower()
        if s not in allowed:
            raise SettingsError("error.type.enum", field=field, allowed=", ".join(allowed))
        return s

    @staticmethod
    def _as_int(val: Any, field: str) -> int:
        if not isinstance(val, int):
            raise SettingsError("error.type.int", field=field)
        return int(val)

    @staticmethod
    def _schema_value(src: dict[str, Any], schema: dict[str, Any], key: str, default: Any) -> Any:
        value = src.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value not in (None, ""):
            return value
        return schema.get(key, default)

    @classmethod
    def _looks_like_lang_code(cls, s: str) -> bool:
        return bool(cls._LANG_CODE_RE.match((s or "").strip()))

    @staticmethod
    def _coerce_lang_code(raw: str) -> str:
        norm = normalize_lang_code(str(raw or "").strip(), drop_region=False)
        return norm or ""

    def _validate_app(self, src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        src = self._merge(src, schema)

        logging_cfg = src.get("logging", {}) if isinstance(src.get("logging"), dict) else {}
        logging_schema = schema.get("logging", {}) if isinstance(schema.get("logging"), dict) else {}
        logging_cfg = self._merge(logging_cfg, logging_schema)

        level = str(self._schema_value(logging_cfg, logging_schema, "level", "warning") or "warning").strip().lower()
        if level not in ("debug", "info", "warning", "error"):
            level = str(logging_schema.get("level", "warning") or "warning").strip().lower()

        ui_cfg = src.get("ui", {}) if isinstance(src.get("ui"), dict) else {}
        ui_schema = schema.get("ui", {}) if isinstance(schema.get("ui"), dict) else {}
        ui_cfg = self._merge(ui_cfg, ui_schema)

        show_adv = bool(self._schema_value(ui_cfg, ui_schema, "show_advanced_settings", False))
        bulk_cfg = ui_cfg.get("bulk_add_confirmation", {}) if isinstance(ui_cfg.get("bulk_add_confirmation"), dict) else {}
        bulk_schema = ui_schema.get("bulk_add_confirmation", {}) if isinstance(ui_schema.get("bulk_add_confirmation"), dict) else {}
        bulk_cfg = self._merge(bulk_cfg, bulk_schema)
        bulk_enabled = bool(self._schema_value(bulk_cfg, bulk_schema, "enabled", True))
        try:
            bulk_threshold = int(self._schema_value(
                bulk_cfg,
                bulk_schema,
                "threshold",
                Config.BULK_ADD_CONFIRMATION_DEFAULT_THRESHOLD,
            ))
        except (TypeError, ValueError):
            bulk_threshold = Config.BULK_ADD_CONFIRMATION_DEFAULT_THRESHOLD
        bulk_threshold = max(
            Config.BULK_ADD_CONFIRMATION_MIN_THRESHOLD,
            min(Config.BULK_ADD_CONFIRMATION_MAX_THRESHOLD, bulk_threshold),
        )

        live_cfg = ui_cfg.get("live", {}) if isinstance(ui_cfg.get("live"), dict) else {}
        live_schema = ui_schema.get("live", {}) if isinstance(ui_schema.get("live"), dict) else {}
        live_cfg = self._merge(live_cfg, live_schema)

        files_cfg = ui_cfg.get("files", {}) if isinstance(ui_cfg.get("files"), dict) else {}
        files_schema = ui_schema.get("files", {}) if isinstance(ui_schema.get("files"), dict) else {}
        files_cfg = self._merge(files_cfg, files_schema)

        live_mode = self._enum_str(
            self._schema_value(live_cfg, live_schema, "mode", "transcribe"),
            ("transcribe", "transcribe_translate"),
            "app.ui.live.mode",
        )
        live_preset = self._enum_str(
            self._schema_value(live_cfg, live_schema, "preset", "balanced"),
            ("low_latency", "balanced", "high_context"),
            "app.ui.live.preset",
        )
        live_output_mode = self._enum_str(
            self._schema_value(live_cfg, live_schema, "output_mode", "cumulative"),
            ("stream", "cumulative"),
            "app.ui.live.output_mode",
        )
        live_device = str(self._schema_value(live_cfg, live_schema, "device_name", "") or "").strip()

        def _last_used_source(cfg: dict[str, Any], cfg_schema: dict[str, Any], field: str) -> str:
            raw = self._schema_value(cfg, cfg_schema, field, Config.LANGUAGE_AUTO_VALUE)
            value = Config.normalize_last_used_source_language(raw)
            supported = set(SettingsCatalog.transcription_language_codes())
            if Config.is_auto_language_value(value):
                return Config.LANGUAGE_AUTO_VALUE
            if supported:
                return value if value in supported else Config.LANGUAGE_AUTO_VALUE
            return value if self._looks_like_lang_code(value) else Config.LANGUAGE_AUTO_VALUE

        def _last_used_target(cfg: dict[str, Any], cfg_schema: dict[str, Any], field: str) -> str:
            raw = self._schema_value(cfg, cfg_schema, field, Config.LANGUAGE_DEFAULT_UI_VALUE)
            value = Config.normalize_last_used_target_language(raw)
            supported = set(SettingsCatalog.translation_language_codes())
            if Config.is_default_ui_language_value(value):
                return Config.LANGUAGE_DEFAULT_UI_VALUE
            if supported:
                return value if value in supported else Config.LANGUAGE_DEFAULT_UI_VALUE
            return value if self._looks_like_lang_code(value) else Config.LANGUAGE_DEFAULT_UI_VALUE

        return {
            "language": str(self._schema_value(src, schema, "language", "auto") or "auto"),
            "theme": self._enum_str(self._schema_value(src, schema, "theme", "auto"), ("auto", "light", "dark"), "app.theme"),
            "logging": {
                "enabled": bool(self._schema_value(logging_cfg, logging_schema, "enabled", True)),
                "level": level,
            },
            "ui": {
                "show_advanced_settings": show_adv,
                "bulk_add_confirmation": {
                    "enabled": bulk_enabled,
                    "threshold": bulk_threshold,
                },
                "live": {
                    "mode": live_mode,
                    "preset": live_preset,
                    "output_mode": live_output_mode,
                    "device_name": live_device,
                    "last_used_source_language": _last_used_source(live_cfg, live_schema, "last_used_source_language"),
                    "last_used_target_language": _last_used_target(live_cfg, live_schema, "last_used_target_language"),
                },
                "files": {
                    "last_used_source_language": _last_used_source(files_cfg, files_schema, "last_used_source_language"),
                    "last_used_target_language": _last_used_target(files_cfg, files_schema, "last_used_target_language"),
                },
            },
        }

    def _validate_engine(self, src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        src = self._merge(src, schema)
        return {
            "preferred_device": self._enum_str(
                self._schema_value(src, schema, "preferred_device", "auto"),
                ("auto", "cpu", "cuda"),
                "engine.preferred_device",
            ),
            "precision": self._enum_str(
                self._schema_value(src, schema, "precision", "auto"),
                ("auto", "float32", "float16", "bfloat16"),
                "engine.precision",
            ),
            "fp32_math_mode": self._enum_str(
                self._schema_value(src, schema, "fp32_math_mode", "ieee"),
                ("ieee", "tf32"),
                "engine.fp32_math_mode",
            ),
            "low_cpu_mem_usage": bool(self._schema_value(src, schema, "low_cpu_mem_usage", True)),
        }

    def _validate_model(self, src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        src = self._merge(src, schema)

        def _d(v: Any) -> dict[str, Any]:
            return v if isinstance(v, dict) else {}

        t_schema = _d(schema.get("transcription_model"))
        x_schema = _d(schema.get("translation_model"))

        t = self._merge(_d(src.get("transcription_model")), t_schema)
        x = self._merge(_d(src.get("translation_model")), x_schema)

        preset = str(self._schema_value(t, t_schema, "quality_preset", "balanced") or "balanced").strip().lower()
        if preset not in ("fast", "balanced", "accurate"):
            preset = str(t_schema.get("quality_preset", "balanced") or "balanced").strip().lower()

        preset_tr = str(self._schema_value(x, x_schema, "quality_preset", "balanced") or "balanced").strip().lower()
        if preset_tr not in ("fast", "balanced", "accurate"):
            preset_tr = str(x_schema.get("quality_preset", "balanced") or "balanced").strip().lower()

        def _engine_meta(cfg: dict[str, Any]) -> dict[str, str | None]:
            engine_name = str(cfg.get("engine_name", "none") or "none").strip()
            low = engine_name.lower()
            model_type = str(cfg.get("engine_model_type", "") or "").strip().lower() or None
            signature = str(cfg.get("engine_signature", "") or "").strip().lower() or None

            if Config.is_disabled_engine_name(low) or low == "auto":
                return {
                    "engine_model_type": None,
                    "engine_signature": None,
                }

            desc = Config.local_model_descriptor(engine_name)
            if desc:
                model_type = str(desc.get("model_type", model_type or "") or "").strip().lower() or None
                signature = str(desc.get("signature", signature or "") or "").strip().lower() or None

            return {
                "engine_model_type": model_type,
                "engine_signature": signature,
            }

        t_meta = _engine_meta(t)
        x_meta = _engine_meta(x)

        return {
            "transcription_model": {
                "engine_name": str(self._schema_value(t, t_schema, "engine_name", "none") or "none").strip(),
                "engine_model_type": t_meta["engine_model_type"],
                "engine_signature": t_meta["engine_signature"],
                "quality_preset": preset,
                "text_consistency": bool(self._schema_value(t, t_schema, "text_consistency", True)),
                "ignore_warning": bool(self._schema_value(t, t_schema, "ignore_warning", False)),
            },
            "translation_model": {
                "engine_name": str(self._schema_value(x, x_schema, "engine_name", "none") or "none").strip(),
                "engine_model_type": x_meta["engine_model_type"],
                "engine_signature": x_meta["engine_signature"],
                "max_new_tokens": int(self._schema_value(x, x_schema, "max_new_tokens", 256)),
                "chunk_max_chars": int(self._schema_value(x, x_schema, "chunk_max_chars", 1200)),
                "quality_preset": preset_tr,
            },
        }

    def _validate_transcription(self, src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        src = self._merge(src, schema)

        default_source_raw = Config.normalize_language_choice_value(
            self._schema_value(src, schema, "default_source_language", Config.LANGUAGE_AUTO_VALUE)
        )
        supported_source = set(SettingsCatalog.transcription_language_codes())
        if Config.is_last_used_language_value(default_source_raw):
            default_source = Config.LANGUAGE_LAST_USED_VALUE
        elif Config.is_auto_language_value(default_source_raw):
            default_source = Config.LANGUAGE_AUTO_VALUE
        else:
            norm_source = self._coerce_lang_code(default_source_raw)
            if supported_source:
                default_source = norm_source if norm_source in supported_source else Config.LANGUAGE_AUTO_VALUE
            else:
                default_source = norm_source if self._looks_like_lang_code(norm_source) else Config.LANGUAGE_AUTO_VALUE

        mode_ids = [str(m.get("id", "")).strip().lower() for m in SettingsCatalog.transcription_output_modes()]
        mode_ids = [m for m in mode_ids if m]
        schema_formats = schema.get("output_formats")
        if isinstance(schema_formats, (list, tuple)):
            default_selected = [str(x or "").strip().lower() for x in schema_formats if str(x or "").strip()]
        else:
            default_selected = ["txt"]

        raw_formats = src.get("output_formats")
        if isinstance(raw_formats, (list, tuple)):
            selected = [str(x or "").strip().lower() for x in raw_formats if str(x or "").strip()]
        else:
            selected = list(default_selected)

        norm: list[str] = []
        seen: set[str] = set()
        for mid in selected:
            if mid in mode_ids and mid not in seen:
                norm.append(mid)
                seen.add(mid)
        if not norm:
            norm = default_selected[:1] if default_selected else ["txt"]

        return {
            "default_source_language": default_source,
            "output_formats": tuple(norm),
            "download_audio_only": bool(self._schema_value(src, schema, "download_audio_only", True)),
            "url_keep_audio": bool(self._schema_value(src, schema, "url_keep_audio", False)),
            "url_audio_ext": self._enum_str(
                str(self._schema_value(src, schema, "url_audio_ext", "m4a") or "m4a").strip().lower().lstrip("."),
                SettingsCatalog.download_audio_exts(),
                "transcription.url_audio_ext",
            ),
            "url_keep_video": bool(self._schema_value(src, schema, "url_keep_video", False)),
            "url_video_ext": self._enum_str(
                str(self._schema_value(src, schema, "url_video_ext", "mp4") or "mp4").strip().lower().lstrip("."),
                SettingsCatalog.download_video_exts(),
                "transcription.url_video_ext",
            ),
            "translate_after_transcription": bool(self._schema_value(src, schema, "translate_after_transcription", False)),
        }

    def _validate_translation(self, src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        src = self._merge(src, schema)

        tgt_lang_raw = Config.normalize_language_choice_value(
            src.get("default_target_language", "") or Config.LANGUAGE_DEFAULT_UI_VALUE
        )
        deferred_target = Config.LANGUAGE_DEFAULT_UI_VALUE

        if Config.is_last_used_language_value(tgt_lang_raw):
            return {
                "default_target_language": Config.LANGUAGE_LAST_USED_VALUE,
            }

        tgt_codes = set(SettingsCatalog.translation_language_codes())
        if Config.is_default_ui_language_value(tgt_lang_raw) or not tgt_lang_raw:
            tgt_lang = deferred_target
        else:
            norm = self._coerce_lang_code(tgt_lang_raw)
            if not norm:
                tgt_lang = deferred_target
            elif tgt_codes:
                tgt_lang = norm if norm in tgt_codes else deferred_target
            else:
                tgt_lang = norm if self._looks_like_lang_code(norm) else deferred_target

        return {
            "default_target_language": tgt_lang,
        }

    def _validate_downloader(self, src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        src = self._merge(src, schema)
        min_h = self._as_int(src.get("min_video_height"), "downloader.min_video_height")
        max_h = self._as_int(src.get("max_video_height"), "downloader.max_video_height")
        if min_h > max_h:
            raise SettingsError(
                "error.settings.invalid_video_height_range",
                min_height=min_h,
                max_height=max_h,
            )
        return {
            "min_video_height": min_h,
            "max_video_height": max_h,
        }

    def _validate_network(self, src: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        src = self._merge(src, schema)
        bw = src.get("max_bandwidth_kbps")
        if bw is not None and not isinstance(bw, int):
            raise SettingsError("error.type.int", field="network.max_bandwidth_kbps")
        return {
            "max_bandwidth_kbps": bw,
            "retries": self._as_int(src.get("retries"), "network.retries"),
            "concurrent_fragments": self._as_int(src.get("concurrent_fragments"), "network.concurrent_fragments"),
            "http_timeout_s": self._as_int(src.get("http_timeout_s"), "network.http_timeout_s"),
        }

    def load(self) -> SettingsSnapshot:
        if not self._defaults_path.exists():
            raise SettingsError("error.settings.defaults_missing", path=str(self._defaults_path))
        defaults = self._read_json(self._defaults_path)

        if not self._settings_path.exists():
            raise SettingsError("error.settings.settings_missing", path=str(self._settings_path))
        settings = self._read_json(self._settings_path)

        schema_app = self._ensure_dict(defaults.get("app", {}), "app")
        schema_engine = self._ensure_dict(defaults.get("engine", {}), "engine")
        schema_model = self._ensure_dict(defaults.get("model", {}), "model")
        schema_transcription = self._ensure_dict(defaults.get("transcription", {}), "transcription")
        schema_translation = self._ensure_dict(defaults.get("translation", {}), "translation")
        schema_downloader = self._ensure_dict(defaults.get("downloader", {}), "downloader")
        schema_network = self._ensure_dict(defaults.get("network", {}), "network")

        app = self._ensure_dict(settings.get("app", {}), "app")
        engine = self._ensure_dict(settings.get("engine", {}), "engine")
        model = self._ensure_dict(settings.get("model", {}), "model")
        transcription = self._ensure_dict(settings.get("transcription", {}), "transcription")
        translation = self._ensure_dict(settings.get("translation", {}), "translation")
        downloader = self._ensure_dict(settings.get("downloader", {}), "downloader")
        network = self._ensure_dict(settings.get("network", {}), "network")

        if "low_cpu_mem_usage" not in engine:
            t_old = (model.get("transcription_model") or {}) if isinstance(model.get("transcription_model"), dict) else {}
            x_old = (model.get("translation_model") or {}) if isinstance(model.get("translation_model"), dict) else {}
            for cand in (t_old.get("low_cpu_mem_usage"), x_old.get("low_cpu_mem_usage")):
                if isinstance(cand, bool):
                    engine["low_cpu_mem_usage"] = cand
                    break

        model_validated = self._validate_model(model, schema_model)
        return SettingsSnapshot(
            app=self._validate_app(app, schema_app),
            engine=self._validate_engine(engine, schema_engine),
            model=model_validated,
            transcription=self._validate_transcription(transcription, schema_transcription),
            translation=self._validate_translation(translation, schema_translation),
            downloader=self._validate_downloader(downloader, schema_downloader),
            network=self._validate_network(network, schema_network),
        )

    def load_or_restore(self) -> tuple[SettingsSnapshot, bool, str]:
        try:
            snap = self.load()
            return snap, False, ""
        except SettingsError as ex:
            if ex.key == "error.settings.defaults_missing":
                raise
            reason = ex.key if ex.key == "error.settings.settings_missing" else "error.settings.settings_invalid"
            self.restore_defaults()
            snap = self.load()
            return snap, True, reason

    @staticmethod
    def _deep_merge(base: Any, patch: Any) -> Any:
        if isinstance(base, dict) and isinstance(patch, dict):
            out: dict[str, Any] = dict(base)
            for k, v in patch.items():
                out[k] = SettingsService._deep_merge(out.get(k), v) if k in out else v
            return out
        return patch

    def save(self, payload: dict[str, Any]) -> SettingsSnapshot:
        if not self._settings_path.exists():
            raise SettingsError("error.settings.settings_missing", path=str(self._settings_path))

        raw = self._read_json(self._settings_path)
        if not isinstance(raw, dict):
            raise SettingsError(
                "error.settings.settings_invalid",
                path=str(self._settings_path),
                detail="root-not-object",
            )

        updated = dict(raw)
        for section, patch in (payload or {}).items():
            if section not in (
                "app",
                "engine",
                "model",
                "transcription",
                "translation",
                "downloader",
                "network",
            ):
                continue
            base = updated.get(section, {})
            if isinstance(base, dict) and isinstance(patch, dict):
                updated[section] = self._deep_merge(base, patch)
            else:
                updated[section] = patch

        tmp_path = self._settings_path.with_suffix(self._settings_path.suffix + ".tmp")
        try:
            self._write_json(tmp_path, updated)

            tmp_svc = SettingsService(
                defaults_path=self._defaults_path,
                settings_path=tmp_path,
            )
            snap = tmp_svc.load()

            self._write_json(self._settings_path, snapshot_to_dict(snap))
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as ex:
                _LOG.debug("Settings temp file cleanup skipped. path=%s detail=%s", tmp_path, ex)

        return self.load()

    def restore_defaults(self) -> SettingsSnapshot:
        defaults = self._read_json(self._defaults_path)
        tmp_path = self._settings_path.with_suffix(self._settings_path.suffix + ".tmp")
        try:
            self._write_json(tmp_path, defaults)
            tmp_svc = SettingsService(
                defaults_path=self._defaults_path,
                settings_path=tmp_path,
            )
            snap = tmp_svc.load()
            self._write_json(self._settings_path, snapshot_to_dict(snap))
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as ex:
                _LOG.debug("Settings temp file cleanup skipped. path=%s detail=%s", tmp_path, ex)
        return self.load()
