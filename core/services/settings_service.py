# core/services/settings_service.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class SettingsError(RuntimeError):
    """Settings error carrying a translation key + params (UI will localize)."""

    def __init__(self, key: str, **params: Any) -> None:
        self.key = key
        self.params = params
        super().__init__(key)


@dataclass(frozen=True)
class SettingsSnapshot:
    """Immutable snapshot of validated settings."""

    paths: Dict[str, Any]
    media: Dict[str, Any]
    app: Dict[str, Any]
    engine: Dict[str, Any]
    model: Dict[str, Any]
    transcription: Dict[str, Any]
    downloader: Dict[str, Any]
    network: Dict[str, Any]


class SettingsService:
    """
    Loads and validates settings.json against defaults.json schema.

    Internal sections (paths, media) are defined in code only.
    User-facing sections (app, engine, model, transcription, downloader, network)
    come from JSON and are validated against defaults.json.
    """

    def __init__(
        self,
        root_dir: Optional[Path] = None,
        *,
        defaults_path: Optional[Path] = None,
        settings_path: Optional[Path] = None,
    ) -> None:
        self._root = Path(root_dir) if root_dir else Path.cwd()
        cfg_dir = self._root / "core" / "config"
        self._defaults_path = Path(defaults_path) if defaults_path else (cfg_dir / "defaults.json")
        self._settings_path = Path(settings_path) if settings_path else (cfg_dir / "settings.json")

    # ----- Internal defaults for paths/media -----

    @staticmethod
    def _default_paths_dict() -> Dict[str, Any]:
        return {
            "resources_dir": "resources",
            "ffmpeg_dir": "resources/ffmpeg",
            "models_dir": "resources/models",
            "ai_engine_dir": "resources/models/whisper-turbo",
            "locales_dir": "resources/locales",
            "data_dir": "data",
            "downloads_dir": "data/downloads",
            "input_tmp_dir": "data/.input_tmp",
            "transcriptions_dir": "data/transcriptions",
        }

    @staticmethod
    def _default_media_dict() -> Dict[str, Any]:
        return {
            "input": {
                "audio_ext": [".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"],
                "video_ext": [".mp4", ".webm", ".mkv", ".mov", ".avi"],
            },
            "downloader": {
                "audio_ext": ["m4a", "mp3"],
                "video_ext": ["mp4", "webm"],
            },
            "transcripts": {
                "default_ext": "txt",
                "ext": ["txt", "srt", "sub"],
            },
        }

    # ----- I/O -----

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            raise SettingsError(
                "error.settings.json_invalid",
                path=str(path),
                detail=str(ex),
            )
        if not isinstance(data, dict):
            raise SettingsError(
                "error.settings.section_invalid",
                section="root",
            )
        return data

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ----- Validation helpers -----

    @staticmethod
    def _ensure_dict(obj: Any, name: str) -> Dict[str, Any]:
        if not isinstance(obj, dict):
            raise SettingsError(
                "error.settings.section_invalid",
                section=name,
            )
        return obj

    @staticmethod
    def _require_keys(sec_name: str, src: Dict[str, Any], schema: Dict[str, Any]) -> None:
        req = set(schema.keys())
        have = set(src.keys())
        missing = sorted(req - have)
        if missing:
            raise SettingsError(
                "error.settings.missing_keys",
                section=sec_name,
                keys=", ".join(missing),
            )

    @staticmethod
    def _as_list_of_str(val: Any, field: str) -> List[str]:
        if not isinstance(val, list) or not all(isinstance(x, (str, int, float)) for x in val):
            raise SettingsError("error.type.list_strings", field=field)
        return [str(x).lower() for x in val]

    @staticmethod
    def _as_nonempty_str(val: Any, field: str) -> str:
        if not isinstance(val, str) or not val:
            raise SettingsError("error.type.string_nonempty", field=field)
        return val

    @staticmethod
    def _as_int(val: Any, field: str) -> int:
        if not isinstance(val, int):
            raise SettingsError("error.type.int", field=field)
        return int(val)

    @staticmethod
    def _enum_str(val: Any, allowed: Tuple[str, ...], field: str) -> str:
        s = str(val).lower()
        if s not in allowed:
            raise SettingsError(
                "error.type.enum_invalid",
                field=field,
                value=s,
                allowed=", ".join(allowed),
            )
        return s

    # ----- Section validators -----

    def _validate_paths(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("paths", src, schema)
        out: Dict[str, Any] = {}
        for k in schema.keys():
            out[k] = self._as_nonempty_str(src.get(k), f"paths.{k}")
        return out

    def _validate_media(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("media", src, schema)

        out: Dict[str, Any] = {}

        schema_input = self._ensure_dict(schema.get("input"), "media.input")
        src_input = self._ensure_dict(src.get("input"), "media.input")
        self._require_keys("media.input", src_input, schema_input)
        out["input"] = {
            "audio_ext": self._as_list_of_str(
                src_input.get("audio_ext"),
                "media.input.audio_ext",
            ),
            "video_ext": self._as_list_of_str(
                src_input.get("video_ext"),
                "media.input.video_ext",
            ),
        }

        schema_down = self._ensure_dict(schema.get("downloader"), "media.downloader")
        src_down = self._ensure_dict(src.get("downloader"), "media.downloader")
        self._require_keys("media.downloader", src_down, schema_down)
        out["downloader"] = {
            "audio_ext": self._as_list_of_str(
                src_down.get("audio_ext"),
                "media.downloader.audio_ext",
            ),
            "video_ext": self._as_list_of_str(
                src_down.get("video_ext"),
                "media.downloader.video_ext",
            ),
        }

        schema_tr = self._ensure_dict(schema.get("transcripts"), "media.transcripts")
        src_tr = self._ensure_dict(src.get("transcripts"), "media.transcripts")
        self._require_keys("media.transcripts", src_tr, schema_tr)
        out["transcripts"] = {
            "default_ext": self._as_nonempty_str(
                src_tr.get("default_ext"),
                "media.transcripts.default_ext",
            ).lower(),
            "ext": self._as_list_of_str(
                src_tr.get("ext"),
                "media.transcripts.ext",
            ),
        }

        return out

    def _validate_app(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("app", src, schema)
        out: Dict[str, Any] = {}
        out["language"] = self._as_nonempty_str(src.get("language"), "app.language")
        out["theme"] = self._enum_str(
            src.get("theme"),
            ("auto", "light", "dark"),
            "app.theme",
        )
        return out

    def _validate_engine(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("engine", src, schema)
        out: Dict[str, Any] = {}
        out["preferred_device"] = self._enum_str(
            src.get("preferred_device"),
            ("auto", "cpu", "gpu"),
            "engine.preferred_device",
        )
        out["precision"] = self._enum_str(
            src.get("precision"),
            ("auto", "float32", "float16", "bfloat16"),
            "engine.precision",
        )
        out["allow_tf32"] = bool(src.get("allow_tf32", True))
        return out

    def _validate_model(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("model", src, schema)
        out: Dict[str, Any] = {}
        out["ai_engine_name"] = self._as_nonempty_str(
            src.get("ai_engine_name"),
            "model.ai_engine_name",
        )
        out["local_models_only"] = bool(src.get("local_models_only", True))

        out["chunk_length_s"] = self._as_int(
            src.get("chunk_length_s"),
            "model.chunk_length_s",
        )
        out["stride_length_s"] = self._as_int(
            src.get("stride_length_s"),
            "model.stride_length_s",
        )

        out["pipeline_task"] = self._as_nonempty_str(
            src.get("pipeline_task"),
            "model.pipeline_task",
        )
        out["ignore_warning"] = bool(src.get("ignore_warning", True))
        out["default_language"] = src.get("default_language", None)

        out["return_timestamps"] = bool(src.get("return_timestamps", False))
        out["use_safetensors"] = bool(src.get("use_safetensors", True))
        out["low_cpu_mem_usage"] = bool(src.get("low_cpu_mem_usage", True))
        return out

    def _validate_transcription(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("transcription", src, schema)
        out: Dict[str, Any] = {}
        out["timestamps_output"] = bool(src.get("timestamps_output", False))
        out["keep_downloaded_files"] = bool(src.get("keep_downloaded_files", True))
        out["keep_wav_temp"] = bool(src.get("keep_wav_temp", False))
        out["download_audio_only"] = bool(src.get("download_audio_only", True))

        raw_ext = src.get("output_ext", "txt")
        ext = str(raw_ext).lower().strip()
        if ext.startswith("."):
            ext = ext[1:]
        out["output_ext"] = ext or "txt"

        return out

    def _validate_downloader(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("downloader", src, schema)
        out: Dict[str, Any] = {}
        out["min_video_height"] = self._as_int(
            src.get("min_video_height"),
            "downloader.min_video_height",
        )
        out["max_video_height"] = self._as_int(
            src.get("max_video_height"),
            "downloader.max_video_height",
        )
        return out

    def _validate_network(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("network", src, schema)
        out: Dict[str, Any] = {}
        bw = src.get("max_bandwidth_kbps")
        if bw is not None and not isinstance(bw, int):
            raise SettingsError("error.type.int", field="network.max_bandwidth_kbps")
        out["max_bandwidth_kbps"] = bw
        out["retries"] = self._as_int(src.get("retries"), "network.retries")
        out["concurrent_fragments"] = self._as_int(
            src.get("concurrent_fragments"),
            "network.concurrent_fragments",
        )
        out["http_timeout_s"] = self._as_int(
            src.get("http_timeout_s"),
            "network.http_timeout_s",
        )
        proxy = src.get("proxy", None)
        if proxy is not None and not isinstance(proxy, str):
            raise SettingsError("error.type.string_nonempty", field="network.proxy")
        out["proxy"] = proxy
        out["throttle_startup_s"] = self._as_int(
            src.get("throttle_startup_s"),
            "network.throttle_startup_s",
        )
        return out

    # ----- Public load API -----

    def load(self) -> SettingsSnapshot:
        if not self._defaults_path.exists():
            raise SettingsError(
                "error.settings.defaults_missing",
                path=str(self._defaults_path),
            )
        defaults = self._read_json(self._defaults_path)

        schema_app = self._ensure_dict(defaults.get("app"), "app")
        schema_engine = self._ensure_dict(defaults.get("engine"), "engine")
        schema_model = self._ensure_dict(defaults.get("model"), "model")
        schema_transcription = self._ensure_dict(
            defaults.get("transcription"),
            "transcription",
        )
        schema_downloader = self._ensure_dict(
            defaults.get("downloader"),
            "downloader",
        )
        schema_network = self._ensure_dict(defaults.get("network"), "network")

        if not self._settings_path.exists():
            raise SettingsError(
                "error.settings.settings_missing",
                path=str(self._settings_path),
            )

        try:
            settings = self._read_json(self._settings_path)

            schema_paths = self._default_paths_dict()
            schema_media = self._default_media_dict()
            src_paths = self._default_paths_dict()
            src_media = self._default_media_dict()

            src_app = self._ensure_dict(settings.get("app"), "app")
            src_engine = self._ensure_dict(settings.get("engine"), "engine")
            src_model = self._ensure_dict(settings.get("model"), "model")
            src_transcription = self._ensure_dict(
                settings.get("transcription"),
                "transcription",
            )
            src_downloader = self._ensure_dict(
                settings.get("downloader"),
                "downloader",
            )
            src_network = self._ensure_dict(settings.get("network"), "network")

            v_paths = self._validate_paths(src_paths, schema_paths)
            v_media = self._validate_media(src_media, schema_media)
            v_app = self._validate_app(src_app, schema_app)
            v_engine = self._validate_engine(src_engine, schema_engine)
            v_model = self._validate_model(src_model, schema_model)
            v_transcription = self._validate_transcription(
                src_transcription,
                schema_transcription,
            )
            v_downloader = self._validate_downloader(
                src_downloader,
                schema_downloader,
            )
            v_network = self._validate_network(src_network, schema_network)
        except SettingsError:
            raise
        except Exception as ex:
            raise SettingsError(
                "error.settings.settings_invalid",
                path=str(self._settings_path),
                detail=str(ex),
            )

        return SettingsSnapshot(
            paths=v_paths,
            media=v_media,
            app=v_app,
            engine=v_engine,
            model=v_model,
            transcription=v_transcription,
            downloader=v_downloader,
            network=v_network,
        )

    def restore_defaults(self, sections: Optional[List[str]] = None) -> None:
        """
        Overwrite user-editable sections in settings.json with defaults.

        Internal sections (paths, media) are never written to settings.json.
        """
        if not self._defaults_path.exists():
            raise SettingsError(
                "error.settings.defaults_missing",
                path=str(self._defaults_path),
            )
        defaults = self._read_json(self._defaults_path)

        user_sections = ("app", "engine", "model", "transcription", "downloader", "network")

        if sections is None:
            sections = list(user_sections)
        else:
            sections = [sec for sec in sections if sec in user_sections]

        data: Dict[str, Any] = {}
        if self._settings_path.exists():
            try:
                data = self._read_json(self._settings_path)
            except SettingsError:
                data = {}

        for sec in sections:
            if sec in defaults:
                data[sec] = defaults[sec]

        self._write_json(self._settings_path, data)

    def load_or_restore(self) -> Tuple[SettingsSnapshot, bool, str]:
        try:
            snap = self.load()
            return snap, False, ""
        except SettingsError as ex:
            if ex.key == "error.settings.defaults_missing":
                raise
            reason = ex.key
            if reason not in (
                "error.settings.settings_missing",
                "error.settings.settings_invalid",
            ):
                raise
            if not self._defaults_path.exists():
                raise
            self.restore_defaults(sections=None)
            snap = self.load()
            return snap, True, reason

    def save_sections(
        self,
        *,
        app: Optional[Dict[str, Any]] = None,
        engine: Optional[Dict[str, Any]] = None,
        model: Optional[Dict[str, Any]] = None,
        transcription: Optional[Dict[str, Any]] = None,
        downloader: Optional[Dict[str, Any]] = None,
        network: Optional[Dict[str, Any]] = None,
    ) -> SettingsSnapshot:
        """
        Validate and write chosen sections back to settings.json.

        Internal sections (paths, media) always come from code defaults.
        Any section set to None is loaded from current settings (or defaults).
        Returns a fresh validated snapshot.
        """
        if not self._defaults_path.exists():
            raise SettingsError(
                "error.settings.defaults_missing",
                path=str(self._defaults_path),
            )
        defaults = self._read_json(self._defaults_path)

        schema_app = self._ensure_dict(defaults.get("app"), "app")
        schema_engine = self._ensure_dict(defaults.get("engine"), "engine")
        schema_model = self._ensure_dict(defaults.get("model"), "model")
        schema_transcription = self._ensure_dict(
            defaults.get("transcription"),
            "transcription",
        )
        schema_downloader = self._ensure_dict(
            defaults.get("downloader"),
            "downloader",
        )
        schema_network = self._ensure_dict(defaults.get("network"), "network")

        if self._settings_path.exists():
            current = self._read_json(self._settings_path)
        else:
            current = {}

        def _section(name: str, override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            if override is not None:
                return self._ensure_dict(override, name)
            if name in current:
                return self._ensure_dict(current[name], name)
            return self._ensure_dict(defaults.get(name, {}), name)

        schema_paths = self._default_paths_dict()
        schema_media = self._default_media_dict()
        src_paths = self._default_paths_dict()
        src_media = self._default_media_dict()

        src_app = _section("app", app)
        src_engine = _section("engine", engine)
        src_model = _section("model", model)
        src_transcription = _section("transcription", transcription)
        src_downloader = _section("downloader", downloader)
        src_network = _section("network", network)

        v_paths = self._validate_paths(src_paths, schema_paths)
        v_media = self._validate_media(src_media, schema_media)
        v_app = self._validate_app(src_app, schema_app)
        v_engine = self._validate_engine(src_engine, schema_engine)
        v_model = self._validate_model(src_model, schema_model)
        v_transcription = self._validate_transcription(
            src_transcription,
            schema_transcription,
        )
        v_downloader = self._validate_downloader(
            src_downloader,
            schema_downloader,
        )
        v_network = self._validate_network(src_network, schema_network)

        data: Dict[str, Any] = {
            "app": v_app,
            "engine": v_engine,
            "model": v_model,
            "transcription": v_transcription,
            "downloader": v_downloader,
            "network": v_network,
        }
        self._write_json(self._settings_path, data)

        return SettingsSnapshot(
            paths=v_paths,
            media=v_media,
            app=v_app,
            engine=v_engine,
            model=v_model,
            transcription=v_transcription,
            downloader=v_downloader,
            network=v_network,
        )
