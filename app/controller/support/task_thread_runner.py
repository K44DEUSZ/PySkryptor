# app/controller/support/task_thread_runner.py
from __future__ import annotations

from typing import Callable, Optional, TypeVar

from PyQt5 import QtCore

TWorker = TypeVar("TWorker", bound=QtCore.QObject)


class TaskThreadRunner(QtCore.QObject):
    """Small helper that standardizes QThread + Worker wiring."""

    def __init__(self, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[QtCore.QObject] = None
        self._on_finished: Optional[Callable[[], None]] = None

    # ----- State -----

    @property
    def thread(self) -> Optional[QtCore.QThread]:
        return self._thread

    @property
    def worker(self) -> Optional[QtCore.QObject]:
        return self._worker

    def is_running(self) -> bool:
        return self._thread is not None

    # ----- Control -----

    def cancel(self) -> None:
        wk = self._worker
        th = self._thread
        try:
            if th is not None:
                th.requestInterruption()
        except Exception:
            pass
        try:
            if wk is not None and hasattr(wk, "cancel"):
                getattr(wk, "cancel")()
        except Exception:
            pass

    def start(
        self,
        worker: TWorker,
        *,
        connect: Optional[Callable[[TWorker], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> TWorker:
        if self.is_running():
            return worker

        th = QtCore.QThread(self)
        worker.moveToThread(th)

        if connect is not None:
            connect(worker)

        self._on_finished = on_finished

        if hasattr(worker, "finished"):
            try:
                worker.finished.connect(th.quit)
                worker.finished.connect(worker.deleteLater)
            except Exception:
                pass

        th.finished.connect(th.deleteLater)
        th.finished.connect(self._cleanup)
        th.started.connect(getattr(worker, "run"))

        self._thread = th
        self._worker = worker
        th.start()
        return worker

    # ----- Internals -----

    @QtCore.pyqtSlot()
    def _cleanup(self) -> None:
        callback = self._on_finished
        self._thread = None
        self._worker = None
        self._on_finished = None

        if callback is not None:
            callback()
