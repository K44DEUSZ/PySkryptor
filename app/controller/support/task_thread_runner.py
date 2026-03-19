# app/controller/support/task_thread_runner.py
from __future__ import annotations

from typing import Callable, TypeVar, cast

from PyQt5 import QtCore

TWorker = TypeVar("TWorker", bound=QtCore.QObject)


class TaskThreadRunner(QtCore.QObject):
    """Small helper that standardizes QThread + Worker wiring."""

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QtCore.QThread | None = None
        self._worker: QtCore.QObject | None = None
        self._on_finished: Callable[[], None] | None = None

    # ----- State -----

    @property
    def thread(self) -> QtCore.QThread | None:
        return self._thread

    @property
    def worker(self) -> QtCore.QObject | None:
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

    def stop(self) -> None:
        wk = self._worker
        try:
            if wk is not None and hasattr(wk, "stop"):
                getattr(wk, "stop")()
                return
        except Exception:
            pass
        self.cancel()

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
        run = getattr(worker, "run", None)
        if not callable(run):
            raise TypeError("TaskThreadRunner worker must define a callable run() method.")

        if hasattr(worker, "finished"):
            try:
                worker.finished.connect(th.quit)
                worker.finished.connect(worker.deleteLater)
            except Exception:
                pass

        th.finished.connect(th.deleteLater)
        th.finished.connect(self._cleanup)
        th.started.connect(run)

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

        if callback is None:
            return
        cast(Callable[[], None], callback)()
