# app/controller/support/options_autosave_controller.py
from __future__ import annotations

from typing import Callable, Optional, Any, Dict

from PyQt5 import QtCore

from app.controller.tasks.settings_task import SettingsWorker
from app.controller.support.task_thread_runner import TaskThreadRunner


class OptionsAutosaveController(QtCore.QObject):
    """Debounced settings autosave for UI quick-options."""

    def __init__(
        self,
        parent: QtCore.QObject,
        *,
        build_payload: Callable[[], Dict[str, Any]],
        apply_snapshot: Callable[[object], None],
        on_error: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        is_busy: Callable[[], bool],
        interval_ms: int = 1200,
        pending_delay_ms: int = 300,
        retry_delay_ms: int = 600,
    ) -> None:
        super().__init__(parent)
        self._build_payload = build_payload
        self._apply_snapshot = apply_snapshot
        self._on_error = on_error
        self._is_busy = is_busy
        self._pending_delay_ms = int(pending_delay_ms)
        self._retry_delay_ms = int(retry_delay_ms)

        self._blocked: bool = False
        self._pending: bool = False

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self._save_now)

        self._runner = TaskThreadRunner(self)

    # ----- Control -----

    def set_blocked(self, blocked: bool) -> None:
        self._blocked = bool(blocked)

    def trigger(self) -> None:
        if self._blocked:
            return
        if self._is_busy():
            return
        self._timer.start()

    # ----- Internals -----

    def _save_now(self) -> None:
        if self._blocked:
            return
        if self._is_busy():
            return

        if self._runner.is_running():
            self._pending = True
            self._timer.start(self._retry_delay_ms)
            return

        payload = self._build_payload() or {}
        wk = SettingsWorker(action="save", payload=payload)

        def _connect(worker: SettingsWorker) -> None:
            worker.saved_snapshot.connect(self._apply_snapshot)
            if self._on_error is not None:
                worker.error.connect(self._on_error)

        def _done() -> None:
            if self._pending:
                self._pending = False
                self._timer.start(self._pending_delay_ms)

        self._runner.start(wk, connect=_connect, on_finished=_done)
