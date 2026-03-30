# app/model/settings/service.py
from __future__ import annotations

import logging
from pathlib import Path

from app.model.core.config.config import AppConfig
from app.model.core.domain.entities import SettingsSnapshot, snapshot_to_dict
from app.model.settings.store import apply_settings_payload, read_json_dict, write_json_dict
from app.model.settings.validation import validate_settings

_LOG = logging.getLogger(__name__)


class SettingsService:
    """Load, validate, and persist application settings snapshots."""

    def __init__(
        self,
        *,
        defaults_path: Path | None = None,
        settings_path: Path | None = None,
    ) -> None:
        self._defaults_path = Path(defaults_path) if defaults_path else AppConfig.PATHS.DEFAULTS_FILE
        self._settings_path = Path(settings_path) if settings_path else AppConfig.PATHS.SETTINGS_FILE

    def _load_defaults(self) -> dict[str, object]:
        return read_json_dict(
            self._defaults_path,
            missing_key="error.settings.defaults_missing",
        )

    def _load_settings(self) -> dict[str, object]:
        return read_json_dict(
            self._settings_path,
            missing_key="error.settings.settings_missing",
        )

    def load(self) -> SettingsSnapshot:
        defaults = self._load_defaults()
        settings = self._load_settings()
        return validate_settings(defaults, settings)

    def save(self, payload: dict[str, object]) -> SettingsSnapshot:
        raw_settings = read_json_dict(
            self._settings_path,
            missing_key="error.settings.settings_missing",
            root_error_key="error.settings.settings_invalid",
        )
        defaults = self._load_defaults()
        updated = apply_settings_payload(raw_settings, payload)

        tmp_path = self._settings_path.with_suffix(self._settings_path.suffix + ".tmp")
        try:
            write_json_dict(tmp_path, updated)
            snap = validate_settings(defaults, updated)
            write_json_dict(self._settings_path, snapshot_to_dict(snap))
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as ex:
                _LOG.debug("Settings temp file cleanup skipped. path=%s detail=%s", tmp_path, ex)

        return self.load()

    def restore_defaults(self) -> SettingsSnapshot:
        defaults = self._load_defaults()
        snap = validate_settings(defaults, defaults)

        tmp_path = self._settings_path.with_suffix(self._settings_path.suffix + ".tmp")
        try:
            write_json_dict(tmp_path, defaults)
            write_json_dict(self._settings_path, snapshot_to_dict(snap))
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as ex:
                _LOG.debug("Settings temp file cleanup skipped. path=%s detail=%s", tmp_path, ex)

        return self.load()
