# app/controller/tasks/settings_task.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PyQt5 import QtCore

from app.controller.tasks.base_worker import BaseWorker
from app.model.services.settings_service import SettingsService, snapshot_to_dict

_LOG = logging.getLogger(__name__)


class SettingsWorker(BaseWorker):
    """Background worker for loading/saving application settings."""

    settings_loaded = QtCore.pyqtSignal(object)
    settings_loaded_snapshot = QtCore.pyqtSignal(object)
    saved = QtCore.pyqtSignal(object)
    saved_snapshot = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str, dict)

    def __init__(self, *, action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._action = str(action or "").strip().lower()
        self._payload = payload or {}

    # ----- Errors -----

    def _handle_failure(self, ex: BaseException) -> None:
        key, params = self._exception_to_i18n(ex)
        self._emit_failure(str(key), dict(params or {}), self.error)

    def _execute(self) -> None:
        if self._action == "load":
            self._do_load()
            return
        if self._action == "save":
            self._do_save()
            return
        if self._action == "restore_defaults":
            self._do_restore_defaults()
            return

        self.error.emit("error.config.unknown_action", {"action": self._action})

    def _do_load(self) -> None:
        svc = SettingsService()
        snap = svc.load()
        self.settings_loaded.emit(snapshot_to_dict(snap))
        self.settings_loaded_snapshot.emit(snap)

    def _do_restore_defaults(self) -> None:
        svc = SettingsService()
        snap = svc.restore_defaults()
        self.saved.emit(snapshot_to_dict(snap))
        self.saved_snapshot.emit(snap)

    def _do_save(self) -> None:
        svc = SettingsService()
        snap = svc.save(self._payload)
        self.saved.emit(snapshot_to_dict(snap))
        self.saved_snapshot.emit(snap)
