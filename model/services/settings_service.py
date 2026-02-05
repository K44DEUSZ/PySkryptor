# model/services/settings_service.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List, Set

from model.constants.m2m100_languages import m2m100_language_codes


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
    """Central catalog of selectable/allowed settings values.

    This is intentionally UI-agnostic: both the UI and services should source their
    allowed values from here (and SettingsService uses it for validation).
    """

    TRANSCRIPTION_OUTPUT_MODES: Tuple[Dict[str, Any], ...] = (
        {"id": "txt", "ext": "txt", "timestamps": False, "tr_key": "settings.transcription.output.plain_txt"},
        {"id": "txt_ts", "ext": "txt", "timestamps": True, "tr_key": "settings.transcription.output.txt_timestamps"},
        {"id": "srt", "ext": "srt", "timestamps": False, "tr_key": "settings.transcription.output.srt"},
    )

    DOWNLOAD_AUDIO_EXTS: Tuple[str, ...] = ("m4a", "mp3", "wav", "flac", "ogg", "opus", "aac")
    DOWNLOAD_VIDEO_EXTS: Tuple[str, ...] = ("mp4", "webm", "mkv", "mov")

    @classmethod
    def transcription_output_modes(cls) -> Tuple[Dict[str, Any], ...]:
        return cls.TRANSCRIPTION_OUTPUT_MODES

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
        return cls.DOWNLOAD_AUDIO_EXTS

    @classmethod
    def download_video_exts(cls) -> Tuple[str, ...]:
        return cls.DOWNLOAD_VIDEO_EXTS

    @classmethod
    def translation_language_codes(cls) -> Set[str]:
        return set(m2m100_language_codes())

    @classmethod
    def translation_target_allowed(cls) -> Set[str]:
        return cls.translation_language_codes() | {"auto"}

class RuntimeConfigService:
    """Applies a validated SettingsSnapshot to the runtime AppConfig.

    AppConfig is a runtime map of paths and resolved runtime parameters.
    SettingsService owns parsing/validation; this service owns applying.
    """

    @staticmethod
    def initialize(config_cls: Any, snap: "SettingsSnapshot") -> None:
        # Backward-compatible: delegate to AppConfig implementation.
        config_cls.initialize_from_snapshot(snap)

    @staticmethod
    def update(config_cls: Any, snap: "SettingsSnapshot", *, sections: Tuple[str, ...] = ("transcription", "translation")) -> None:
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
        return {
            "language": str(src.get("language", "auto") or "auto"),
            "theme": self._enum_str(src.get("theme", "auto"), ("auto", "light", "dark"), "app.theme"),
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
        }

    def _validate_model(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        src = self._merge(src, schema)

        def _d(v: Any) -> Dict[str, Any]:
            return v if isinstance(v, dict) else {}

        t_schema = _d(schema.get("transcription_model"))
        x_schema = _d(schema.get("translation_model"))

        t = self._merge(_d(src.get("transcription_model")), t_schema)
        x = self._merge(_d(src.get("translation_model")), x_schema)

        return {
            "transcription_model": {
                "engine_name": str(t.get("engine_name", "auto") or "auto"),
                "chunk_length_s": int(t.get("chunk_length_s", 60)),
                "stride_length_s": int(t.get("stride_length_s", 5)),
                "ignore_warning": bool(t.get("ignore_warning", True)),
                "default_language": t.get("default_language", None),
                "low_cpu_mem_usage": bool(t.get("low_cpu_mem_usage", True)),
            },
            "translation_model": {
                "engine_name": str(x.get("engine_name", "none") or "none").strip().lower(),
                "dtype": str(x.get("dtype", "auto") or "auto").strip().lower(),
                "max_new_tokens": int(x.get("max_new_tokens", 256)),
                "chunk_max_chars": int(x.get("chunk_max_chars", 1200)),
                "low_cpu_mem_usage": bool(x.get("low_cpu_mem_usage", True)),
                "local_files_only": bool(x.get("local_files_only", True)),
            },
        }

    def _validate_transcription(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        src = self._merge(src, schema)
        return {
            "output_ext": self._enum_str(src.get("output_ext", "txt"), SettingsCatalog.transcript_extensions(), "transcription.output_ext"),
            "timestamps_output": bool(src.get("timestamps_output", False)),
            "download_audio_only": bool(src.get("download_audio_only", True)),
            "url_keep_audio": bool(src.get("url_keep_audio", False)),
            "url_audio_ext": self._enum_str(str(src.get("url_audio_ext", "m4a") or "m4a").strip().lower().lstrip("."), SettingsCatalog.download_audio_exts(), "transcription.url_audio_ext"),
            "url_keep_video": bool(src.get("url_keep_video", False)),
            "url_video_ext": self._enum_str(str(src.get("url_video_ext", "mp4") or "mp4").strip().lower().lstrip("."), SettingsCatalog.download_video_exts(), "transcription.url_video_ext"),
            "translate_after_transcription": bool(src.get("translate_after_transcription", False)),
        }

    def _validate_translation(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        src = self._merge(src, schema)
        tgt = str(src.get("target_language", "auto") or "auto").strip().lower()
        if tgt not in SettingsCatalog.translation_target_allowed():
            raise SettingsError("error.type.enum", field="translation.target_language", allowed=", ".join(sorted(SettingsCatalog.translation_target_allowed())))
        return {"target_language": tgt}

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
