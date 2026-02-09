# controller/tasks/settings_task.py

"""controller/tasks/settings_task.py

Background worker for loading/saving application settings.

Note: `from __future__ import annotations` must be the first import (after this docstring),
otherwise Python raises a SyntaxError.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5 import QtCore

from model.config.app_config import AppConfig as Config
from model.services.settings_service import SettingsService, SettingsError
from view.utils.translating import tr


class SettingsWorker(QtCore.QObject):
    """Background worker for loading/saving application settings."""

    settings_loaded = QtCore.pyqtSignal(object)
    settings_loaded_snapshot = QtCore.pyqtSignal(object)
    saved = QtCore.pyqtSignal(object)
    saved_snapshot = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, *, action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._action = action
        self._payload = payload or {}

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if self._action == "load":
                self._do_load()
            elif self._action == "save":
                self._do_save()
            elif self._action == "restore_defaults":
                self._do_restore_defaults()
            else:
                msg = tr("error.config.unknown_action", action=self._action)
                self.error.emit(tr("error.config.generic", detail=msg))
        finally:
            self.finished.emit()

    @staticmethod
    def _snapshot_to_dict(snap) -> Dict[str, Any]:
        return {
            "app": dict(snap.app),
            "engine": dict(snap.engine),
            "model": dict(snap.model),
            "transcription": dict(snap.transcription),
            "translation": dict(getattr(snap, "translation", {}) or {}),
            "downloader": dict(snap.downloader),
            "network": dict(snap.network),
        }

    def _emit_settings_error(self, ex: SettingsError) -> None:
        try:
            msg = tr(ex.key, **ex.params)
        except Exception:
            msg = f"{ex.key}: {ex}"
        self.error.emit(msg)

    def _do_load(self) -> None:
        try:
            svc = SettingsService(Config.ROOT_DIR)
            snap = svc.load()
            self.settings_loaded.emit(self._snapshot_to_dict(snap))
            self.settings_loaded_snapshot.emit(snap)
        except SettingsError as ex:
            self._emit_settings_error(ex)
        except Exception as ex:
            self.error.emit(tr("error.config.generic", detail=str(ex)))

    def _do_restore_defaults(self) -> None:
        """Restore user-editable sections from defaults.json and reload."""
        try:
            svc = SettingsService(Config.ROOT_DIR)
            svc.restore_defaults()

            snap = svc.load()
            self.saved.emit(self._snapshot_to_dict(snap))
            self.saved_snapshot.emit(snap)
        except SettingsError as ex:
            self._emit_settings_error(ex)
        except Exception as ex:
            self.error.emit(tr("error.config.generic", detail=str(ex)))

    def _do_save(self) -> None:
        """Save selected settings sections back to settings.json."""
        try:
            svc = SettingsService(Config.ROOT_DIR)
            defaults_path: Path = svc._defaults_path  # type: ignore[attr-defined]
            settings_path: Path = svc._settings_path  # type: ignore[attr-defined]

            if not settings_path.exists():
                raise SettingsError(
                    "error.settings.settings_missing",
                    path=str(settings_path),
                )

            raw = svc._read_json(settings_path)  # type: ignore[attr-defined]
            if not isinstance(raw, dict):
                raise SettingsError(
                    "error.settings.settings_invalid",
                    path=str(settings_path),
                    detail="root-not-object",
                )

            updated = dict(raw)

            for section in (
                "app",
                "engine",
                "model",
                "transcription",
                "translation",
                "downloader",
                "network",
            ):
                if section not in self._payload:
                    continue
                base = updated.get(section, {})
                patch = self._payload[section]
                if isinstance(base, dict) and isinstance(patch, dict):
                    merged = dict(base)
                    merged.update(patch)
                    updated[section] = merged
                else:
                    updated[section] = patch


            tmp_path = settings_path.with_suffix(settings_path.suffix + ".tmp")
            try:
                svc._write_json(tmp_path, updated)  # type: ignore[attr-defined]

                tmp_svc = SettingsService(
                    Config.ROOT_DIR,
                    defaults_path=defaults_path,
                    settings_path=tmp_path,
                )
                _ = tmp_svc.load()

                svc._write_json(settings_path, updated)  # type: ignore[attr-defined]
                final_snap = svc.load()
                self.saved.emit(self._snapshot_to_dict(final_snap))
                self.saved_snapshot.emit(final_snap)
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)  # type: ignore[call-arg]
                except Exception:
                    pass

        except SettingsError as ex:
            self._emit_settings_error(ex)
        except Exception as ex:
            self.error.emit(tr("error.config.generic", detail=str(ex)))
