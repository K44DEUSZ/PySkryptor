# app/controller/workers/access_task_worker.py
from __future__ import annotations

import threading

from PyQt5 import QtCore

from app.controller.workers.task_worker import PendingDecision, TaskWorker
from app.model.core.domain.errors import OperationCancelled
from app.model.download.domain import SourceAccessInterventionRequired
from app.model.download.policy import DownloadPolicy


class AccessTaskWorker(TaskWorker):
    """Task worker variant that can pause for a source-access decision."""

    access_intervention_required = QtCore.pyqtSignal(dict)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._access_intervention_decision = PendingDecision(default_action="cancel")
        self._access_intervention_lock = threading.Lock()

    def cancel(self) -> None:
        super().cancel()
        with self._access_intervention_lock:
            self._cancel_pending_decision(self._access_intervention_decision)

    @QtCore.pyqtSlot(str, str)
    def on_access_intervention_decided(self, action: str, value: str = "") -> None:
        with self._access_intervention_lock:
            self._set_pending_decision(
                self._access_intervention_decision,
                action=str(action or "").strip().lower(),
                value=str(value or "").strip(),
            )

    def _next_access_intervention_overrides(
        self,
        ex: SourceAccessInterventionRequired,
        *,
        payload_key_name: str,
        payload_key: str,
        browser_cookies_mode_override: str | None,
        cookie_file_override: str | None,
        access_mode_override: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        payload = dict(ex.request.as_payload())
        payload[str(payload_key_name or "key")] = str(payload_key or "")

        with self._access_intervention_lock:
            self._access_intervention_decision.reset()
        self.access_intervention_required.emit(payload)
        action, value = self._wait_for_pending_decision(self._access_intervention_decision)
        action = str(action or "").strip().lower()
        value = str(value or "").strip()

        if self.cancel_check() or action == "cancel":
            raise OperationCancelled()
        if action == "without_cookies":
            return "none", None, access_mode_override
        if action == "use_cookie_file" and value:
            return "from_file", value, access_mode_override
        if action == DownloadPolicy.EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED:
            return (
                browser_cookies_mode_override,
                cookie_file_override,
                DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED,
            )
        if action == DownloadPolicy.EXTRACTOR_ACCESS_ACTION_CONTINUE_BASIC:
            return (
                browser_cookies_mode_override,
                cookie_file_override,
                DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC,
            )
        if action == DownloadPolicy.EXTRACTOR_ACCESS_ACTION_CONTINUE_DEGRADED:
            return (
                browser_cookies_mode_override,
                cookie_file_override,
                DownloadPolicy.EXTRACTOR_ACCESS_MODE_DEGRADED,
            )
        return browser_cookies_mode_override, cookie_file_override, access_mode_override
