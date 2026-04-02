# app/controller/coordinators/runtime_coordinator.py
from __future__ import annotations

from typing import Callable

from PyQt5 import QtCore

from app.controller.workers.runtime_state_worker import RuntimeStateWorker
from app.controller.workers.worker_runner import WorkerRunner
from app.model.core.domain.entities import SettingsSnapshot
from app.model.engines.manager import EngineManager


class RuntimeCoordinator(QtCore.QObject):
    """Owns runtime-state worker wiring for startup and engine reload flows."""

    busy_changed = QtCore.pyqtSignal(bool)

    def __init__(self, engine_manager: EngineManager, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._engine_manager = engine_manager
        self._runner = WorkerRunner(self)
        self._worker: RuntimeStateWorker | None = None

    def is_busy(self) -> bool:
        return self._runner.is_running()

    def start(
        self,
        snapshot: SettingsSnapshot,
        *,
        labels: dict[str, str],
        connect: Callable[[RuntimeStateWorker], None] | None = None,
    ) -> RuntimeStateWorker | None:
        if self._runner.is_running():
            return self._worker

        worker = RuntimeStateWorker(engine_manager=self._engine_manager, snapshot=snapshot, labels=labels)
        self._worker = worker
        self.busy_changed.emit(True)

        def _done() -> None:
            self._worker = None
            self.busy_changed.emit(False)

        return self._runner.start(worker, connect=connect, on_finished=_done)

    def cancel(self) -> None:
        self._runner.cancel()
