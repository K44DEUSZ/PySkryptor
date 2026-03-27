# app/controller/workers/settings_worker.py
from __future__ import annotations

import logging
from typing import Any

from PyQt5 import QtCore

from app.controller.workers.task_worker import TaskWorker
from app.model.config.app_config import AppConfig as Config
from app.model.domain.entities import SettingsSnapshot
from app.model.domain.errors import AppError
from app.model.services.settings_service import SettingsService

_LOG = logging.getLogger(__name__)


class SettingsWorker(TaskWorker):
    """Background worker for loading/saving application settings."""

    settings_loaded = QtCore.pyqtSignal(object)
    saved = QtCore.pyqtSignal(str, object)

    def __init__(self, *, action: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._action = str(action or "").strip().lower()
        self._payload = payload or {}

    def _execute(self) -> None:
        if self._action == "load":
            self._do_load()
            return
        if self._action in {"save", "save_ui_state"}:
            self._do_save(self._action)
            return
        if self._action == "restore_defaults":
            self._do_restore_defaults()
            return

        raise AppError(key="error.settings.unknown_action", params={"action": self._action})

    def _do_load(self) -> None:
        svc = SettingsService()
        snap = svc.load()
        self._apply_runtime_snapshot(snap)
        self.settings_loaded.emit(snap)

    def _do_restore_defaults(self) -> None:
        svc = SettingsService()
        snap = svc.restore_defaults()
        self._apply_runtime_snapshot(snap)
        self.saved.emit("restore_defaults", snap)

    def _do_save(self, action: str) -> None:
        svc = SettingsService()
        snap = svc.save(self._payload)
        self._apply_runtime_snapshot(snap)
        self.saved.emit(str(action or "save"), snap)

    @staticmethod
    def _apply_runtime_snapshot(snap: SettingsSnapshot) -> None:
        try:
            Config.initialize_from_snapshot(snap)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            _LOG.debug("Settings runtime snapshot apply skipped.", exc_info=True)
