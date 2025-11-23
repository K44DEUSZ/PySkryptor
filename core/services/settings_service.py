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
    model: Dict[str, Any]
    user: Dict[str, Any]


class SettingsService:
    """
    Loads and validates settings.json against defaults.json schema.
    Defaults are a template (no implicit value fallback).
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

    # ----- I/O -----

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            raise SettingsError("error.json_invalid", path=str(path), detail=str(ex))
        if not isinstance(data, dict):
            raise SettingsError("error.settings_section_invalid", section="root")
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
            raise SettingsError("error.settings_section_invalid", section=name)
        return obj

    @staticmethod
    def _require_keys(sec_name: str, src: Dict[str, Any], schema: Dict[str, Any]) -> None:
        req = set(schema.keys())
        have = set(src.keys())
        missing = sorted(req - have)
        if missing:
            raise SettingsError("error.settings_missing_keys", section=sec_name, keys=", ".join(missing))

    @staticmethod
    def _as_list_of_str(val: Any, field: str) -> List[str]:
        if not isinstance(val, list) or not all(isinstance(x, (str, int, float)) for x in val):
            raise SettingsError("error.type_list_strings", field=field)
        return [str(x).lower() for x in val]

    @staticmethod
    def _as_nonempty_str(val: Any, field: str) -> str:
        if not isinstance(val, str) or not val:
            raise SettingsError("error.type_string_nonempty", field=field)
        return val

    @staticmethod
    def _as_int(val: Any, field: str) -> int:
        if not isinstance(val, int):
            raise SettingsError("error.type_int", field=field)
        return int(val)

    @staticmethod
    def _enum_str(val: Any, allowed: Tuple[str, ...], field: str) -> str:
        s = str(val).lower()
        if s not in allowed:
            raise SettingsError("error.enum_invalid", field=field, value=s, allowed=", ".join(allowed))
        return s

    # ----- Section validators -----

    def _validate_paths(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        NEW schema: settings contain *final folders*, no '*_subdir':
          - resources_dir, ffmpeg_dir, models_dir, ai_engine_dir, locales_dir,
            data_dir, downloads_dir, input_tmp_dir, transcriptions_dir
        """
        self._require_keys("paths", src, schema)
        out: Dict[str, Any] = {}
        for k in schema.keys():
            out[k] = self._as_nonempty_str(src.get(k), f"paths.{k}")
        return out

    def _validate_media(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("media", src, schema)
        return {
            "audio_ext": self._as_list_of_str(src.get("audio_ext"), "media.audio_ext"),
            "video_ext": self._as_list_of_str(src.get("video_ext"), "media.video_ext"),
        }

    def _validate_model(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("model", src, schema)
        out: Dict[str, Any] = {}
        out["ai_engine_name"] = self._as_nonempty_str(src.get("ai_engine_name"), "model.ai_engine_name")
        out["local_models_only"] = bool(src.get("local_models_only", True))
        out["chunk_length_s"] = self._as_int(src.get("chunk_length_s"), "model.chunk_length_s")
        out["stride_length_s"] = self._as_int(src.get("stride_length_s"), "model.stride_length_s")
        out["pipeline_task"] = self._as_nonempty_str(src.get("pipeline_task"), "model.pipeline_task")
        out["ignore_warning"] = bool(src.get("ignore_warning", True))
        out["default_language"] = src.get("default_language", None)
        out["return_timestamps"] = bool(src.get("return_timestamps", True))
        out["use_safetensors"] = bool(src.get("use_safetensors", True))
        out["low_cpu_mem_usage"] = bool(src.get("low_cpu_mem_usage", True))
        return out

    def _validate_user(self, src: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        self._require_keys("user", src, schema)
        out: Dict[str, Any] = {}
        out["language"] = self._enum_str(src.get("language"), ("pl", "en"), "user.language")
        out["preferred_device"] = self._enum_str(src.get("preferred_device"), ("auto", "cpu", "gpu"), "user.preferred_device")
        out["precision"] = self._enum_str(src.get("precision"), ("auto", "float32", "float16", "bfloat16"), "user.precision")
        out["allow_tf32"] = bool(src.get("allow_tf32", True))
        out["timestamps_output"] = bool(src.get("timestamps_output", True))
        out["keep_downloaded_files"] = bool(src.get("keep_downloaded_files", True))
        out["keep_wav_temp"] = bool(src.get("keep_wav_temp", False))
        return out

    # ----- Public API -----

    def load(self) -> SettingsSnapshot:
        if not self._defaults_path.exists():
            raise SettingsError("error.defaults_missing", path=str(self._defaults_path))
        defaults = self._read_json(self._defaults_path)

        schema_paths = self._ensure_dict(defaults.get("paths"), "paths")
        schema_media = self._ensure_dict(defaults.get("media"), "media")
        schema_model = self._ensure_dict(defaults.get("model"), "model")
        schema_user = self._ensure_dict(defaults.get("user"), "user")

        if not self._settings_path.exists():
            raise SettingsError("error.settings_missing", path=str(self._settings_path))
        try:
            settings = self._read_json(self._settings_path)
            src_paths = self._ensure_dict(settings.get("paths"), "paths")
            src_media = self._ensure_dict(settings.get("media"), "media")
            src_model = self._ensure_dict(settings.get("model"), "model")
            src_user = self._ensure_dict(settings.get("user"), "user")

            v_paths = self._validate_paths(src_paths, schema_paths)
            v_media = self._validate_media(src_media, schema_media)
            v_model = self._validate_model(src_model, schema_model)
            v_user = self._validate_user(src_user, schema_user)
        except SettingsError:
            raise
        except Exception as ex:
            raise SettingsError("error.settings_invalid", path=str(self._settings_path), detail=str(ex))

        return SettingsSnapshot(paths=v_paths, media=v_media, model=v_model, user=v_user)

    def restore_defaults(self, sections: Optional[List[str]] = None) -> None:
        """Overwrite settings.json with defaults (all or selected sections)."""
        if not self._defaults_path.exists():
            raise SettingsError("error.defaults_missing", path=str(self._defaults_path))
        defaults = self._read_json(self._defaults_path)

        if sections is None:
            data = defaults
        else:
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

    # ----- Convenience loader with auto-restore -----

    def load_or_restore(self) -> Tuple[SettingsSnapshot, bool, str]:
        """Try load; if settings missing/invalid, restore from defaults and load again."""
        try:
            snap = self.load()
            return snap, False, ""
        except SettingsError as ex:
            if ex.key == "error.defaults_missing":
                raise
            reason = ex.key
            if reason not in ("error.settings_missing", "error.settings_invalid"):
                raise
            if not self._defaults_path.exists():
                raise
            self.restore_defaults(sections=None)
            snap = self.load()
            return snap, True, reason
