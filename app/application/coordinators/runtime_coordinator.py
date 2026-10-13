# app/application/coordinators/runtime_coordinator.py
from __future__ import annotations

from typing import Callable

from PyQt6 import QtCore

from app.application.workers.runtime_state_worker import RuntimeStateWorker
from app.application.workers.worker_runner import WorkerRunner
from app.core.domain.entities import SettingsSnapshot
from app.runtime.manager import EngineManager


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
    cleanup_temporary: bool = False,
  ) -> RuntimeStateWorker | None:
    if self._runner.is_running():
      return self._worker

    worker = RuntimeStateWorker(
      engine_manager=self._engine_manager, snapshot=snapshot, labels=labels, cleanup_temporary=cleanup_temporary
    )
    self._worker = worker
    self.busy_changed.emit(True)

    def _done() -> None:
      self._worker = None
      self.busy_changed.emit(False)

    return self._runner.start(worker, connect=connect, on_finished=_done)

  def cancel(self) -> None:
    self._runner.cancel()
