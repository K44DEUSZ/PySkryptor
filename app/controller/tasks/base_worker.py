# app/controller/tasks/base_worker.py
from __future__ import annotations

import logging
import threading
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.model.helpers.errors import AppError, OperationCancelled


class _WorkerMeta(type(QtCore.QObject), ABCMeta):
    pass

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


class BaseWorker(QtCore.QObject, metaclass=_WorkerMeta):
    """Base worker that provides a consistent run/cancel/error contract."""

    finished = QtCore.pyqtSignal()
    progress = QtCore.pyqtSignal(int)

    failed = QtCore.pyqtSignal(str, dict)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, *, cancel_token: CancellationToken | None = None) -> None:
        super().__init__()
        self._cancel = cancel_token or CancellationToken()
        self._log = logging.getLogger(self.__class__.__module__)

    # ----- Control -----

    def cancel(self) -> None:
        self._cancel.cancel()

    # ----- Internals -----

    def cancel_check(self) -> bool:
        if self._cancel.is_cancelled:
            return True
        try:
            th = QtCore.QThread.currentThread()
            return bool(th is not None and th.isInterruptionRequested())
        except (AttributeError, RuntimeError):
            return False

    @staticmethod
    def _exception_to_i18n(ex: BaseException) -> tuple[str, dict[str, Any]]:
        key = getattr(ex, "key", None)
        params = getattr(ex, "params", None)
        if key:
            return str(key), dict(params or {})

        msg = str(ex)
        return "error.generic", {"detail": msg}

    def _emit_failure(self, key: str, params: dict[str, Any] | None = None, *signals: QtCore.pyqtBoundSignal) -> None:
        payload = dict(params or {})
        self.failed.emit(str(key), dict(payload))
        for signal in signals:
            try:
                signal.emit(str(key), dict(payload))
            except (RuntimeError, TypeError):
                continue

    def _handle_failure(self, ex: BaseException) -> None:
        key, params = self._exception_to_i18n(ex)
        self._emit_failure(str(key), dict(params or {}))

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

    # ----- Template -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self._execute()

            if self.cancel_check():
                self.cancelled.emit()
        except Exception as ex:
            if isinstance(ex, OperationCancelled) or self.cancel_check():
                self._log.info("%s cancelled.", self.__class__.__name__)
                self.cancelled.emit()
            else:
                if isinstance(ex, AppError):
                    self._log.warning(
                        "%s failed. key=%s params=%s",
                        self.__class__.__name__,
                        getattr(ex, "key", "error.generic"),
                        dict(getattr(ex, "params", {}) or {}),
                    )
                else:
                    self._log.exception("%s failed.", self.__class__.__name__)
                self._handle_failure(ex)
        finally:
            self.finished.emit()

    @abstractmethod
    def _execute(self) -> None:
        """Run the worker logic (executed on a background thread)."""
