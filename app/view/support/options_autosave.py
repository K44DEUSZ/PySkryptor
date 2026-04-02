# app/view/support/options_autosave.py
from __future__ import annotations

from typing import Any, Callable

from PyQt5 import QtCore


class OptionsAutosave(QtCore.QObject):
    """Small debounced autosave helper for UI quick-options."""

    def __init__(
        self,
        parent: QtCore.QObject,
        *,
        build_payload: Callable[[], dict[str, Any]],
        commit: Callable[[dict[str, Any]], None],
        is_busy: Callable[[], bool],
        interval_ms: int = 1200,
        pending_delay_ms: int = 300,
    ) -> None:
        super().__init__(parent)
        self._build_payload = build_payload
        self._commit = commit
        self._is_busy = is_busy
        self._interval_ms = int(interval_ms)
        self._pending_delay_ms = int(pending_delay_ms)

        self._blocked: bool = False

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._commit_now)

    def set_blocked(self, blocked: bool) -> None:
        self._blocked = bool(blocked)

    def trigger(self) -> None:
        if self._blocked:
            return
        if self._is_busy():
            self._timer.start(self._pending_delay_ms)
            return
        self._timer.start(self._interval_ms)

    def _commit_now(self) -> None:
        if self._blocked:
            return
        if self._is_busy():
            self._timer.start(self._pending_delay_ms)
            return
        payload = dict(self._build_payload() or {})
        self._commit(payload)
