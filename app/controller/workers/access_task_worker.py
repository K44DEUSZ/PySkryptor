# app/controller/workers/access_task_worker.py
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from PyQt5 import QtCore

from app.controller.workers.task_worker import TaskWorker
from app.model.core.domain.errors import OperationCancelled
from app.model.download.domain import (
    SourceAccessInterventionRequired,
    SourceAccessInterventionResolution,
)
from app.model.download.policy import DownloadPolicy


@dataclass
class PendingAccessInterventionDecision:
    """Shared wait state for source-access decisions resolved by the UI thread."""

    default_resolution: SourceAccessInterventionResolution = field(
        default_factory=SourceAccessInterventionResolution,
    )
    resolution: SourceAccessInterventionResolution = field(init=False)
    event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.resolution = self.default_resolution
        self.event.clear()


class AccessTaskWorker(TaskWorker):
    """Task worker variant that can pause for a source-access decision."""

    access_intervention_required = QtCore.pyqtSignal(dict)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._access_intervention_decision = PendingAccessInterventionDecision()
        self._access_intervention_lock = threading.Lock()

    @staticmethod
    def _set_pending_access_resolution(
        pending: PendingAccessInterventionDecision,
        *,
        resolution: SourceAccessInterventionResolution,
    ) -> None:
        pending.resolution = SourceAccessInterventionResolution.from_payload(resolution)
        pending.event.set()

    @staticmethod
    def _cancel_pending_access_resolution(pending: PendingAccessInterventionDecision) -> None:
        pending.resolution = pending.default_resolution
        pending.event.set()

    def _wait_for_pending_access_resolution(
        self,
        pending: PendingAccessInterventionDecision,
        *,
        poll_interval_ms: int = 150,
    ) -> SourceAccessInterventionResolution:
        timeout_s = max(0.01, float(int(poll_interval_ms)) / 1000.0)

        while not pending.event.wait(timeout_s):
            if self.cancel_check():
                self._cancel_pending_access_resolution(pending)
                break

        return SourceAccessInterventionResolution.from_payload(pending.resolution)

    def cancel(self) -> None:
        super().cancel()
        with self._access_intervention_lock:
            self._cancel_pending_access_resolution(self._access_intervention_decision)

    @QtCore.pyqtSlot(object)
    def on_access_intervention_decided(self, resolution: object = None) -> None:
        with self._access_intervention_lock:
            self._set_pending_access_resolution(
                self._access_intervention_decision,
                resolution=SourceAccessInterventionResolution.from_payload(resolution),
            )

    def _next_access_intervention_overrides(
        self,
        ex: SourceAccessInterventionRequired,
        *,
        payload_key_name: str,
        payload_key: str,
        browser_cookies_mode_override: str | None,
        cookie_file_override: str | None,
        browser_policy_override: str | None,
        access_mode_override: str | None,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        payload = dict(ex.request.as_payload())
        payload[str(payload_key_name or "key")] = str(payload_key or "")

        with self._access_intervention_lock:
            self._access_intervention_decision.reset()
        self.access_intervention_required.emit(payload)
        resolution = self._wait_for_pending_access_resolution(self._access_intervention_decision)
        action = str(resolution.action or "").strip().lower()
        cookie_file_path = str(resolution.cookie_file_path or "").strip()
        selected_browser_policy = str(resolution.browser_policy or "").strip().lower() or None

        if self.cancel_check() or action == "cancel":
            raise OperationCancelled()
        if action == "without_cookies":
            return "none", None, None, access_mode_override
        if action == "use_cookie_file" and cookie_file_path:
            return "from_file", cookie_file_path, None, access_mode_override
        if action == "retry":
            next_browser_policy = selected_browser_policy or browser_policy_override
            next_mode = browser_cookies_mode_override or "from_browser"
            return next_mode, cookie_file_override, next_browser_policy, access_mode_override
        if action == DownloadPolicy.EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED:
            return (
                browser_cookies_mode_override,
                cookie_file_override,
                browser_policy_override,
                DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED,
            )
        if action == DownloadPolicy.EXTRACTOR_ACCESS_ACTION_CONTINUE_BASIC:
            return (
                browser_cookies_mode_override,
                cookie_file_override,
                browser_policy_override,
                DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC,
            )
        if action == DownloadPolicy.EXTRACTOR_ACCESS_ACTION_CONTINUE_DEGRADED:
            return (
                browser_cookies_mode_override,
                cookie_file_override,
                browser_policy_override,
                DownloadPolicy.EXTRACTOR_ACCESS_MODE_DEGRADED,
            )
        return (
            browser_cookies_mode_override,
            cookie_file_override,
            browser_policy_override,
            access_mode_override,
        )
