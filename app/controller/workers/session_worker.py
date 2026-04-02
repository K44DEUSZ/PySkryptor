# app/controller/workers/session_worker.py
from __future__ import annotations

import threading

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.controller.workers.base_worker import BaseWorker, _BaseWorkerMeta
from app.model.core.domain.errors import OperationCancelled


class SessionWorker(BaseWorker, metaclass=_BaseWorkerMeta):
    """Worker whose lifecycle continues after run() returns."""

    def __init__(self, *, cancel_token: CancellationToken | None = None) -> None:
        super().__init__(cancel_token=cancel_token)
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        self._stop_requested.set()
        self._request_stop()

    def cancel(self) -> None:
        super().cancel()
        self._stop_requested.set()
        self._request_stop()

    def is_stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def _request_stop(self) -> None:
        """Wake the session so it can move toward shutdown."""

    def _shutdown_session(self) -> None:
        """Release session resources without deciding final outcome."""

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self._start_session()
        except Exception as ex:
            try:
                self._shutdown_session()
            except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
                self._log.error("%s shutdown after startup failure failed.", self.__class__.__name__, exc_info=True)
            if isinstance(ex, OperationCancelled) or self.cancel_check():
                self._finish_cancelled()
            else:
                self._finish_failure(ex)

    def _start_session(self) -> None:
        """Initialize the long-running session on the worker thread."""
        raise NotImplementedError
