# model/services/settings_service.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Set

from model.constants.m2m100_languages import m2m100_language_codes
from model.constants.whisper_languages import whisper_language_codes
from model.config.app_config import AppConfig as Config


class SettingsError(RuntimeError):
    def __init__(self, key: str, **params: Any) -> None:
        self.key = key
        self.params = params
        super().__init__(key)


@dataclass(frozen=True)
class SettingsSnapshot:
    app: Dict[str, Any]
    engine: Dict[str, Any]
    model: Dict[str, Any]
    transcription: Dict[str, Any]
    translation: Dict[str, Any]
    downloader: Dict[str, Any]
    network: Dict[str, Any]


class SettingsCatalog:
    TRANSCRIPTION_OUTPUT_MODES: Tuple[Dict[str, Any], ...] = (
        {"id": "txt", "ext": "txt", "timestamps": False, "tr_key": "transcription.output_mode.plain_txt.label"},
        {"id": "txt_ts", "ext": "txt", "timestamps": True, "tr_key": "transcription.output_mode.txt_timestamps.label"},
        {"id": "srt", "ext": "srt", "timestamps": True, "tr_key": "transcription.output_mode.srt.label"},
    )

    DOWNLOAD_AUDIO_EXTS: Tuple[str, ...] = ("m4a", "mp3", "wav", "flac", "ogg", "opus", "aac")
    DOWNLOAD_VIDEO_EXTS: Tuple[str, ...] = ("mp4", "webm", "mkv", "mov")

    @classmethod
    def transcription_output_modes(cls) -> Tuple[Dict[str, Any], ...]:
        return Config.get_transcription_output_modes()

    @classmethod
    def transcript_extensions(cls) -> Tuple[str, ...]:
        return tuple(
            sorted(
                {
                    str(m.get("ext", "")).strip().lower()
                    for m in cls.TRANSCRIPTION_OUTPUT_MODES
                    if m.get("ext")
                }
            )
        )

    @classmethod
    def download_audio_exts(cls) -> Tuple[str, ...]:
        return tuple(Config.AUDIO_EXTS)

    @classmethod
    def download_video_exts(cls) -> Tuple[str, ...]:
        return tuple(Config.VIDEO_EXTS)

    @classmethod
    def translation_language_codes(cls) -> Set[str]:
        return set(m2m100_language_codes())

    @classmethod
    def translation_target_allowed(cls) -> Set[str]:
        return cls.translation_language_codes() | {"auto"}

    @classmethod
    def transcription_language_codes(cls) -> Set[str]:
        return set(whisper_language_codes())

    @classmethod
    def transcription_language_allowed(cls) -> Set[str]:
        return cls.transcription_language_codes() | {"auto"}

    @classmethod
    def transcription_source_allowed(cls) -> Set[str]:
        return cls.transcription_language_allowed()


class RuntimeConfigService:
    @staticmethod
    def initialize(config_cls: Any, snap: "SettingsSnapshot") -> None:
        config_cls.initialize_from_snapshot(snap)

    @staticmethod
    def update(
        config_cls: Any,
        snap: "SettingsSnapshot",
        *,
        sections: Tuple[str, ...] = ("transcription", "translation"),
    ) -> None:
        config_cls.update_from_snapshot(snap, sections=sections)


class SettingsService:
    def __init__(
        self,
        root_dir: Optional[Path] = None,
        *,
        defaults_path: Optional[Path] = None,
        settings_path: Optional[Path] = None,
    ) -> None:
        root = Path(root_dir) if root_dir else Path.cwd()
        cfg_dir = root / "model" / "config"
        self._defaults_path = Path(defaults_path) if defaults_path else (cfg_dir / "defaults.json")
        self._settings_path = Path(settings_path) if settings_path else (cfg_dir / "settings.json")

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as ex:
            raise SettingsError("error.settings.json_invalid", path=str(path), detail=str(ex))
        if not isinstance(data, dict):
            raise SettingsError("error.settings.section_invalid", section="root")
        return data

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _ensure_dict(obj: Any, section: str) -> Dict[str, Any]:
        if not isinstance(obj, dict):
            raise SettingsError("error.settings.section_invalid", section=section)
        return obj

    @staticmethod
    def _merge(src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(schema)
        out.update(src)
        return out

    @staticmethod
    def _enum_str(val: Any, allowed: Tuple[str, ...], field: str) -> str:
        s = str(val).strip().lower()
        if s not in allowed:
            raise SettingsError("error.type.enum", field=field, allowed=", ".join(allowed))
        return s

    @staticmethod
    def _as_int(val: Any, field: str) -> int:
        if not isinstance(val, int):
            raise SettingsError("error.type.int", field=field)
        return int(val)

    def _validate_app(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        src = self._merge(src, schema)

        logging_cfg = src.get("logging", {}) if isinstance(src.get("logging"), dict) else {}
        logging_schema = schema.get("logging", {}) if isinstance(schema.get("logging"), dict) else {}
        logging_cfg = self._merge(logging_cfg, logging_schema)

        level = str(logging_cfg.get("level", "info") or "info").strip().lower()
        if level not in ("debug", "info", "warning", "error"):
            level = "info"


        ui_cfg = src.get("ui", {}) if isinstance(src.get("ui"), dict) else {}
        ui_schema = schema.get("ui", {}) if isinstance(schema.get("ui"), dict) else {}
        ui_cfg = self._merge(ui_cfg, ui_schema)

        show_adv = bool(ui_cfg.get("show_advanced_settings", False))
        return {
            "language": str(src.get("language", "auto") or "auto"),
            "theme": self._enum_str(src.get("theme", "auto"), ("auto", "light", "dark"), "app.theme"),
            "logging": {
                "enabled": bool(logging_cfg.get("enabled", True)),
                "level": level,
            },
            "ui": {
                "show_advanced_settings": show_adv,
            },
        }

    def _validate_engine(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        src = self._merge(src, schema)
        return {
            "preferred_device": self._enum_str(
                src.get("preferred_device", "auto"),
                ("auto", "cpu", "cuda"),
                "engine.preferred_device",
            ),
            "precision": self._enum_str(
                src.get("precision", "auto"),
                ("auto", "float32", "float16", "bfloat16"),
                "engine.precision",
            ),
            "allow_tf32": bool(src.get("allow_tf32", True)),
            "low_cpu_mem_usage": bool(src.get("low_cpu_mem_usage", True)),
        }

    def _validate_model(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        src = self._merge(src, schema)

        def _d(v: Any) -> Dict[str, Any]:
            return v if isinstance(v, dict) else {}

        t_schema = _d(schema.get("transcription_model"))
        x_schema = _d(schema.get("translation_model"))

        t = self._merge(_d(src.get("transcription_model")), t_schema)
        x = self._merge(_d(src.get("translation_model")), x_schema)

        default_lang_raw = t.get("default_language", None)
        default_lang: Optional[str] = None
        if default_lang_raw is not None:
            s = str(default_lang_raw).strip().lower()
            if s and s != "auto":
                if s not in SettingsCatalog.transcription_language_codes():
                    raise SettingsError(
                        "error.type.enum",
                        field="model.transcription_model.default_language",
                        allowed=", ".join(sorted(SettingsCatalog.transcription_language_allowed())),
                    )
                default_lang = s

        preset = str(t.get("quality_preset", "balanced") or "balanced").strip().lower()
        if preset not in ("fast", "balanced", "accurate"):
            preset = "balanced"

        preset_tr = str(x.get("quality_preset", "balanced") or "balanced").strip().lower()
        if preset_tr not in ("fast", "balanced", "accurate"):
            preset_tr = "balanced"

        return {
            "transcription_model": {
                "engine_name": str(t.get("engine_name", "none") or "none"),
                "quality_preset": preset,
                "text_consistency": bool(t.get("text_consistency", True)),
                "chunk_length_s": int(t.get("chunk_length_s", 60)),
                "stride_length_s": int(t.get("stride_length_s", 5)),
                "ignore_warning": bool(t.get("ignore_warning", False)),
                "default_language": default_lang,
            },
            "translation_model": {
                "engine_name": str(x.get("engine_name", "none") or "none").strip().lower(),
                "dtype": str(x.get("dtype", "auto") or "auto").strip().lower(),
                "max_new_tokens": int(x.get("max_new_tokens", 256)),
                "chunk_max_chars": int(x.get("chunk_max_chars", 1200)),
                "quality_preset": preset_tr,
            },
        }

    def _validate_transcription(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        src = self._merge(src, schema)

        mode_ids = [str(m.get('id', '')).strip().lower() for m in SettingsCatalog.transcription_output_modes()]
        mode_ids = [m for m in mode_ids if m]

        raw_formats = src.get('output_formats')

        if isinstance(raw_formats, (list, tuple)):
            selected = [str(x or '').strip().lower() for x in raw_formats if str(x or '').strip()]
        else:
            selected = ['txt']

        norm: list[str] = []
        seen: set[str] = set()
        for mid in selected:
            if mid in mode_ids and mid not in seen:
                norm.append(mid)
                seen.add(mid)
        if not norm:
            norm = ['txt']

        return {
            'output_formats': tuple(norm),
            'download_audio_only': bool(src.get('download_audio_only', True)),
            'url_keep_audio': bool(src.get('url_keep_audio', False)),
            'url_audio_ext': self._enum_str(
                str(src.get('url_audio_ext', 'm4a') or 'm4a').strip().lower().lstrip('.'),
                SettingsCatalog.download_audio_exts(),
                'transcription.url_audio_ext',
            ),
            'url_keep_video': bool(src.get('url_keep_video', False)),
            'url_video_ext': self._enum_str(
                str(src.get('url_video_ext', 'mp4') or 'mp4').strip().lower().lstrip('.'),
                SettingsCatalog.download_video_exts(),
                'transcription.url_video_ext',
            ),
            'translate_after_transcription': bool(src.get('translate_after_transcription', False)),
        }

    def _validate_translation(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and persist translation preferences used by the Files panel."""
        src = self._merge(src, schema)

        src_lang = str(src.get("source_language", "") or "").strip().lower() or "auto"
        tgt_lang = str(src.get("target_language", "") or "").strip().lower() or "auto"

        return {
            "source_language": self._enum_str(
                src_lang,
                tuple(sorted(SettingsCatalog.transcription_source_allowed())),
                "translation.source_language",
            ),
            "target_language": self._enum_str(
                tgt_lang,
                tuple(sorted(SettingsCatalog.translation_target_allowed())),
                "translation.target_language",
            ),
        }

    def _validate_downloader(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        src = self._merge(src, schema)
        return {
            "min_video_height": self._as_int(src.get("min_video_height"), "downloader.min_video_height"),
            "max_video_height": self._as_int(src.get("max_video_height"), "downloader.max_video_height"),
        }

    def _validate_network(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
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

        # Backward-compat: older versions stored low_cpu_mem_usage inside model sections.
        if "low_cpu_mem_usage" not in engine:
            try:
                t_old = (model.get("transcription_model") or {}) if isinstance(model.get("transcription_model"), dict) else {}
                x_old = (model.get("translation_model") or {}) if isinstance(model.get("translation_model"), dict) else {}
                for cand in (t_old.get("low_cpu_mem_usage"), x_old.get("low_cpu_mem_usage")):
                    if isinstance(cand, bool):
                        engine["low_cpu_mem_usage"] = cand
                        break
            except Exception:
                pass

        return SettingsSnapshot(
            app=self._validate_app(app, schema_app),
            engine=self._validate_engine(engine, schema_engine),
            model=self._validate_model(model, schema_model),
            transcription=self._validate_transcription(transcription, schema_transcription),
            translation=self._validate_translation(translation, schema_translation),
            downloader=self._validate_downloader(downloader, schema_downloader),
            network=self._validate_network(network, schema_network),
        )

    def load_or_restore(self) -> Tuple[SettingsSnapshot, bool, str]:
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

    def save(
        self,
        *,
        app: Optional[Dict[str, Any]] = None,
        engine: Optional[Dict[str, Any]] = None,
        model: Optional[Dict[str, Any]] = None,
        transcription: Optional[Dict[str, Any]] = None,
        translation: Optional[Dict[str, Any]] = None,
        downloader: Optional[Dict[str, Any]] = None,
        network: Optional[Dict[str, Any]] = None,
    ) -> SettingsSnapshot:
        current = self._read_json(self._settings_path) if self._settings_path.exists() else {}
        defaults = self._read_json(self._defaults_path)

        def _sec(name: str, override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            if override is not None:
                return self._ensure_dict(override, name)
            return self._ensure_dict(current.get(name, defaults.get(name, {})), name)

        snap = SettingsSnapshot(
            app=self._validate_app(_sec("app", app), self._ensure_dict(defaults.get("app", {}), "app")),
            engine=self._validate_engine(_sec("engine", engine), self._ensure_dict(defaults.get("engine", {}), "engine")),
            model=self._validate_model(_sec("model", model), self._ensure_dict(defaults.get("model", {}), "model")),
            transcription=self._validate_transcription(
                _sec("transcription", transcription),
                self._ensure_dict(defaults.get("transcription", {}), "transcription"),
            ),
            translation=self._validate_translation(
                _sec("translation", translation),
                self._ensure_dict(defaults.get("translation", {}), "translation"),
            ),
            downloader=self._validate_downloader(
                _sec("downloader", downloader),
                self._ensure_dict(defaults.get("downloader", {}), "downloader"),
            ),
            network=self._validate_network(
                _sec("network", network),
                self._ensure_dict(defaults.get("network", {}), "network"),
            ),
        )

        self._write_json(
            self._settings_path,
            {
                "app": snap.app,
                "engine": snap.engine,
                "model": snap.model,
                "transcription": snap.transcription,
                "translation": snap.translation,
                "downloader": snap.downloader,
                "network": snap.network,
            },
        )
        return snap

    def restore_defaults(self) -> SettingsSnapshot:
        defaults = self._read_json(self._defaults_path)
        self._write_json(self._settings_path, defaults)
        return self.load()
