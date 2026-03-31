# app/controller/workers/base_worker.py
from __future__ import annotations

import logging
from abc import ABCMeta
from typing import Any

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.model.core.domain.errors import AppError


class _BaseWorkerMeta(type(QtCore.QObject), ABCMeta):
    """Metaclass that combines QObject and abstract worker requirements."""

    pass


def _app_error_log_detail(ex: AppError) -> str:
    params = dict(ex.params or {})
    parts: list[str] = []
    for field in ("detail", "path", "reason", "code", "field", "action", "lang", "seconds", "cmd"):
        value = params.get(field)
        if value in (None, "", [], {}, ()):
            continue
        parts.append(f"{field}={value}")
    if parts:
        return " ".join(parts)
    return f"error_type={ex.__class__.__name__}"


class BaseWorker(QtCore.QObject, metaclass=_BaseWorkerMeta):
    """Shared lifecycle contract for all background workers."""

    finished = QtCore.pyqtSignal()
    failed = QtCore.pyqtSignal(str, dict)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, *, cancel_token: CancellationToken | None = None) -> None:
        super().__init__()
        self._cancel = cancel_token or CancellationToken()
        self._log = logging.getLogger(self.__class__.__module__)
        self._finalized = False

    def cancel(self) -> None:
        self._cancel.cancel()

    def stop(self) -> None:
        self.cancel()

    def run(self) -> None:
        raise NotImplementedError

    def is_finalized(self) -> bool:
        return self._finalized

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
        if isinstance(ex, AppError):
            return str(ex.key), dict(ex.params or {})
        return "error.generic", {"detail": str(ex)}

    def _emit_failure(
        self,
        key: str,
        params: dict[str, Any] | None = None,
        *signals: QtCore.pyqtBoundSignal,
    ) -> None:
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

    def _log_failure(self, ex: BaseException) -> None:
        if isinstance(ex, AppError):
            self._log.warning("%s failed. %s", self.__class__.__name__, _app_error_log_detail(ex))
            return
        self._log.exception("%s failed.", self.__class__.__name__)

    def _finish_success(self) -> bool:
        if self._finalized:
            return False
        self._finalized = True
        self.finished.emit()
        return True

    def _finish_cancelled(self) -> bool:
        if self._finalized:
            return False
        self._finalized = True
        self._log.info("%s cancelled.", self.__class__.__name__)
        self.cancelled.emit()
        self.finished.emit()
        return True

    def _finish_failure(self, ex: BaseException) -> bool:
        if self._finalized:
            return False
        self._finalized = True
        self._log_failure(ex)
        self._handle_failure(ex)
        self.finished.emit()
        return True
