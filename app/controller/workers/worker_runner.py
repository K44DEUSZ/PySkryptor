# app/controller/workers/worker_runner.py
from __future__ import annotations

import logging
from typing import Callable, TypeVar

from PyQt5 import QtCore

from app.controller.workers.base_worker import BaseWorker

TWorker = TypeVar("TWorker", bound=BaseWorker)

_LOG = logging.getLogger(__name__)


class WorkerRunner(QtCore.QObject):
    """Shared runner for QThread + Worker lifecycle wiring."""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QtCore.QThread | None = None
        self._worker: BaseWorker | None = None
        self._on_finished: Callable[[], None] | None = None

    @property
    def thread(self) -> QtCore.QThread | None:
        return self._thread

    @property
    def worker(self) -> BaseWorker | None:
        return self._worker

    def is_running(self) -> bool:
        return self._thread is not None

    def cancel(self) -> None:
        wk = self._worker
        th = self._thread
        if th is not None:
            try:
                th.requestInterruption()
            except RuntimeError as ex:
                _LOG.debug("Thread interruption request skipped. detail=%s", ex)
        if wk is not None:
            try:
                wk.cancel()
            except RuntimeError as ex:
                _LOG.debug("Worker cancel request skipped. detail=%s", ex)

    def stop(self) -> None:
        wk = self._worker
        if wk is None:
            return
        try:
            wk.stop()
        except RuntimeError as ex:
            _LOG.debug("Worker stop request skipped. detail=%s", ex)

    def start(
        self,
        worker: TWorker,
        *,
        connect: Callable[[TWorker], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> TWorker:
        if self.is_running():
            return worker

        th = QtCore.QThread(self)
        worker.moveToThread(th)

        if connect is not None:
            connect(worker)

        self._on_finished = on_finished
        worker.finished.connect(th.quit)
        worker.finished.connect(worker.deleteLater)

        th.finished.connect(th.deleteLater)
        th.finished.connect(self._cleanup)
        th.started.connect(worker.run, QtCore.Qt.QueuedConnection)

        self._thread = th
        self._worker = worker
        th.start()
        return worker

    @QtCore.pyqtSlot()
    def _cleanup(self) -> None:
        callback = self._on_finished
        self._thread = None
        self._worker = None
        self._on_finished = None
        if callback is None:
            return
        self._run_finished_callback(callback)

    @staticmethod
    def _run_finished_callback(callback: Callable[[], None]) -> None:
        callback()
