# ui/workers/settings_worker.py
# ui/workers/settings_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.services.settings_service import SettingsService, SettingsError
from ui.utils.translating import tr


class SettingsWorker(QtCore.QObject):
    """
    Worker for loading and saving settings.json via SettingsService.
    Uses existing validation logic (load()) to avoid duplication.
    """

    settings_loaded = QtCore.pyqtSignal(object)  # dict with sections
    saved = QtCore.pyqtSignal(object)            # dict with sections after save
    error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, *, action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._action = action
        self._payload = payload or {}

    # ----- Qt entry point -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if self._action == "load":
                self._do_load()
            elif self._action == "save":
                self._do_save()
            else:
                msg = tr("error.config.unknown_action", action=self._action)
                self.error.emit(tr("error.config.generic", detail=msg))
        finally:
            self.finished.emit()

    # ----- Helpers -----

    @staticmethod
    def _snapshot_to_dict(snap) -> Dict[str, Any]:
        """
        Convert SettingsSnapshot into a simple dict of sections.
        Only sections that are useful for the SettingsPanel are exposed.
        """
        return {
            "app": dict(snap.app),
            "engine": dict(snap.engine),
            "model": dict(snap.model),
            "transcription": dict(snap.transcription),
            "downloader": dict(snap.downloader),
            "network": dict(snap.network),
        }

    # ----- Actions -----

    def _do_load(self) -> None:
        """Load and validate settings, then emit normalized dict."""
        try:
            svc = SettingsService(Config.ROOT_DIR)
            snap = svc.load()
            data = self._snapshot_to_dict(snap)
            self.settings_loaded.emit(data)
        except SettingsError as ex:
            # Use i18n key + params if available.
            try:
                msg = tr(ex.key, **ex.params)
            except Exception:
                msg = f"{ex.key}: {ex}"
            self.error.emit(msg)
        except Exception as ex:
            self.error.emit(tr("error.config.generic", detail=str(ex)))

    def _do_save(self) -> None:
        """
        Save selected sections to settings.json.

        Steps:
          1) Read current settings.json via SettingsService helpers.
          2) Apply incoming sections (app/engine/model/transcription/downloader/network).
          3) Write to temporary file and re-run SettingsService.load() to validate.
          4) If OK, write final file and emit updated snapshot.
        """
        try:
            svc = SettingsService(Config.ROOT_DIR)
            defaults_path: Path = svc._defaults_path  # type: ignore[attr-defined]
            settings_path: Path = svc._settings_path  # type: ignore[attr-defined]

            if not settings_path.exists():
                raise SettingsError("error.settings_missing", path=str(settings_path))

            # 1) Read current raw settings
            raw = svc._read_json(settings_path)  # type: ignore[attr-defined]
            if not isinstance(raw, dict):
                raise SettingsError("error.settings_invalid", path=str(settings_path))

            # 2) Apply only known sections from payload
            updated = dict(raw)
            for section in ("app", "engine", "model", "transcription", "downloader", "network"):
                if section in self._payload:
                    updated[section] = self._payload[section]

            # 3) Validate via temporary file
            tmp_path = settings_path.with_suffix(settings_path.suffix + ".tmp")
            try:
                svc._write_json(tmp_path, updated)  # type: ignore[attr-defined]

                tmp_svc = SettingsService(
                    Config.ROOT_DIR,
                    defaults_path=defaults_path,
                    settings_path=tmp_path,
                )
                snap = tmp_svc.load()  # may raise SettingsError

                # 4) Commit: write final file and reload snapshot from canonical service
                svc._write_json(settings_path, updated)  # type: ignore[attr-defined]
                final_snap = svc.load()
                data = self._snapshot_to_dict(final_snap)
                self.saved.emit(data)
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)  # type: ignore[call-arg]
                except Exception:
                    pass

        except SettingsError as ex:
            try:
                msg = tr(ex.key, **ex.params)
            except Exception:
                msg = f"{ex.key}: {ex}"
            self.error.emit(msg)
        except Exception as ex:
            self.error.emit(tr("error.config.generic", detail=str(ex)))
