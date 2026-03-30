# app/controller/workers/task_worker.py
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from PyQt5 import QtCore

from app.controller.workers.base_worker import BaseWorker, _BaseWorkerMeta
from app.model.core.domain.errors import OperationCancelled


@dataclass
class PendingDecision:
    """Shared wait state for worker decisions resolved by the UI thread."""

    default_action: str = "skip"
    default_value: str = ""
    action: str = field(init=False)
    value: str = field(init=False)
    event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.action = str(self.default_action or "")
        self.value = str(self.default_value or "")
        self.event.clear()


class TaskWorker(BaseWorker, metaclass=_BaseWorkerMeta):
    """One-shot worker that completes its lifecycle inside run()."""

    progress = QtCore.pyqtSignal(int)

    @staticmethod
    def _set_pending_decision(
        pending: PendingDecision,
        *,
        action: str,
        value: str = "",
    ) -> None:
        pending.action = str(action or pending.default_action or "")
        pending.value = str(value or "")
        pending.event.set()

    @staticmethod
    def _cancel_pending_decision(pending: PendingDecision) -> None:
        pending.action = str(pending.default_action or "")
        pending.value = str(pending.default_value or "")
        pending.event.set()

    def _wait_for_pending_decision(
        self,
        pending: PendingDecision,
        *,
        poll_interval_ms: int = 150,
    ) -> tuple[str, str]:
        timeout_s = max(0.01, float(int(poll_interval_ms)) / 1000.0)

        while not pending.event.wait(timeout_s):
            if self.cancel_check():
                self._cancel_pending_decision(pending)
                break

        return (
            str(pending.action or pending.default_action or ""),
            str(pending.value or pending.default_value or ""),
        )

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self._execute()
        except Exception as ex:
            if isinstance(ex, OperationCancelled) or self.cancel_check():
                self._finish_cancelled()
            else:
                self._finish_failure(ex)
            return

        if self.cancel_check():
            self._finish_cancelled()
            return

        self._finish_success()

    def _execute(self) -> None:
        """Run the worker logic on the background thread."""
        raise NotImplementedError
