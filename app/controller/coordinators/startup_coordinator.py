# app/controller/coordinators/startup_coordinator.py
from __future__ import annotations

from typing import Any, Callable

from PyQt5 import QtCore

from app.controller.workers.startup_worker import StartupWorker, build_startup_tasks
from app.controller.workers.task_thread_runner import TaskThreadRunner

class StartupCoordinator(QtCore.QObject):
    """Owns startup worker wiring for bootstrap and loading-screen flows."""

    busy_changed = QtCore.pyqtSignal(bool)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = TaskThreadRunner(self)
        self._worker: StartupWorker | None = None

    def is_busy(self) -> bool:
        return self._runner.is_running()

    def current_worker(self) -> StartupWorker | None:
        return self._worker

    def start(
        self,
        tasks: list[Any],
        *,
        connect: Callable[[StartupWorker], None] | None = None,
    ) -> StartupWorker | None:
        if self._runner.is_running():
            return self._worker

        worker = StartupWorker(tasks)
        self._worker = worker
        self.busy_changed.emit(True)

        def _done() -> None:
            self._worker = None
            self.busy_changed.emit(False)

        return self._runner.start(worker, connect=connect, on_finished=_done)

    def build_and_start(
        self,
        config_cls: Any,
        snap: Any,
        labels: dict[str, str],
        *,
        connect: Callable[[StartupWorker], None] | None = None,
    ) -> StartupWorker | None:
        return self.start(build_startup_tasks(config_cls, snap, labels), connect=connect)

    def cancel(self) -> None:
        self._runner.cancel()
