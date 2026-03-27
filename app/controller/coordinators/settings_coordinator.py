# app/controller/coordinators/settings_coordinator.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PyQt5 import QtCore

from app.controller.contracts import SettingsPanelViewProtocol
from app.controller.workers.settings_worker import SettingsWorker
from app.controller.workers.task_thread_runner import TaskThreadRunner

if TYPE_CHECKING:
    from app.model.domain.entities import SettingsSnapshot


class SettingsCoordinator(QtCore.QObject):
    """Owns the Settings worker lifecycle for the Settings panel."""

    busy_changed = QtCore.pyqtSignal(bool)
    failed = QtCore.pyqtSignal(str, dict)
    settings_loaded = QtCore.pyqtSignal(object)
    saved = QtCore.pyqtSignal(str, object)
    settings_applied = QtCore.pyqtSignal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = TaskThreadRunner(self)
        self._worker: SettingsWorker | None = None
        self._view: SettingsPanelViewProtocol | None = None

    def bind_view(self, panel: SettingsPanelViewProtocol) -> None:
        if self._view is panel:
            return
        previous = self._view
        if previous is not None:
            for signal, slot in (
                (self.failed, previous.on_error),
                (self.settings_loaded, previous.on_settings_loaded),
                (self.saved, previous.on_saved),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._view = panel
        self.failed.connect(panel.on_error)
        self.settings_loaded.connect(panel.on_settings_loaded)
        self.saved.connect(panel.on_saved)

    def is_busy(self) -> bool:
        return self._runner.is_running()

    def load(self) -> SettingsWorker | None:
        return self._start_worker(action="load")

    def save(self, payload: dict[str, Any] | None = None) -> SettingsWorker | None:
        return self._start_worker(action="save", payload=payload)

    def save_ui_state(self, payload: dict[str, Any] | None = None) -> SettingsWorker | None:
        return self._start_worker(action="save_ui_state", payload=payload)

    def restore_defaults(self) -> SettingsWorker | None:
        return self._start_worker(action="restore_defaults")

    def cancel(self) -> None:
        self._runner.cancel()

    def _start_worker(self, *, action: str, payload: dict[str, Any] | None = None) -> SettingsWorker | None:
        if self._runner.is_running():
            return self._worker

        worker = SettingsWorker(action=action, payload=payload)
        self._worker = worker
        self.busy_changed.emit(True)

        def _connect(wk: SettingsWorker) -> None:
            def _on_loaded(snap: "SettingsSnapshot") -> None:
                self.settings_loaded.emit(snap)

            def _on_saved(saved_action: str, snap: "SettingsSnapshot") -> None:
                self.saved.emit(saved_action, snap)
                if str(saved_action or "").strip().lower() in {"save", "restore_defaults"}:
                    self.settings_applied.emit()

            wk.settings_loaded.connect(_on_loaded)
            wk.saved.connect(_on_saved)
            wk.failed.connect(self.failed)

        def _done() -> None:
            self._worker = None
            self.busy_changed.emit(False)

        return self._runner.start(worker, connect=_connect, on_finished=_done)
