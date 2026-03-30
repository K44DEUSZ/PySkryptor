# app/controller/workers/transcription_worker.py
from __future__ import annotations

from typing import Any

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.controller.workers.access_task_worker import AccessTaskWorker
from app.controller.workers.task_worker import PendingDecision
from app.model.core.domain.entities import TranscriptionSessionRequest
from app.model.download.domain import SourceAccessInterventionRequest, SourceAccessInterventionRequired
from app.model.transcription.service import TranscriptionService

SourceEntry = str | dict[str, Any]


class TranscriptionWorker(AccessTaskWorker):
    """Background worker that orchestrates a transcription session."""

    item_status = QtCore.pyqtSignal(str, str)
    item_progress = QtCore.pyqtSignal(str, int)
    item_path_update = QtCore.pyqtSignal(str, str)
    transcript_ready = QtCore.pyqtSignal(str, str)
    item_error = QtCore.pyqtSignal(str, str, dict)
    item_output_dir = QtCore.pyqtSignal(str, str)

    conflict_check = QtCore.pyqtSignal(str, str)
    session_done = QtCore.pyqtSignal(str, bool, bool, bool)

    def __init__(
        self,
        *,
        pipe: Any,
        entries: list[SourceEntry],
        session_request: TranscriptionSessionRequest,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        super().__init__(cancel_token=cancel_token)
        self._pipe = pipe
        self._entries = list(entries or [])
        self._session_request = session_request
        self._session_reported = False

        self.failed.connect(self._on_failed)
        self.cancelled.connect(self._on_cancelled)

        self._conflict_decision = PendingDecision(default_action="skip")

    def cancel(self) -> None:
        super().cancel()
        self._cancel_pending_decision(self._conflict_decision)

    @QtCore.pyqtSlot(str, str)
    def on_conflict_decided(self, action: str, new_stem: str = "") -> None:
        self._set_pending_decision(
            self._conflict_decision,
            action=str(action or "skip").strip().lower(),
            value=str(new_stem or "").strip(),
        )

    def _conflict_resolver(self, stem: str, existing_dir: str) -> tuple[str, str, bool]:
        self._conflict_decision.reset()

        self.conflict_check.emit(str(stem), str(existing_dir))
        action, new_stem = self._wait_for_pending_decision(self._conflict_decision)

        if self.cancel_check():
            return "skip", "", False

        action = str(action or "skip").strip().lower()
        new_stem = str(new_stem or "").strip()
        return action, new_stem, False

    def _on_failed(self, err_key: str, params: dict) -> None:
        if self._session_reported:
            return
        self.item_error.emit("", str(err_key), dict(params or {}))
        self.session_done.emit("", False, True, False)
        self._session_reported = True

    def _on_cancelled(self) -> None:
        if self._session_reported:
            return
        self.session_done.emit("", False, False, True)
        self._session_reported = True

    def _access_intervention_resolver(
        self,
        source_key: str,
        request: SourceAccessInterventionRequest,
        browser_cookies_mode_override: str | None,
        cookie_file_override: str | None,
        access_mode_override: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        return self._next_access_intervention_overrides(
            SourceAccessInterventionRequired(request),
            payload_key_name="source_key",
            payload_key=source_key,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            access_mode_override=access_mode_override,
        )

    def _execute(self) -> None:
        svc = TranscriptionService()
        res = svc.run_session(
            pipe=self._pipe,
            entries=self._entries,
            session_request=self._session_request,
            progress=lambda pct: self.progress.emit(int(pct)),
            item_status=lambda key, st: self.item_status.emit(str(key), str(st)),
            item_progress=lambda key, pct: self.item_progress.emit(str(key), int(pct)),
            item_path_update=lambda old, new: self.item_path_update.emit(str(old), str(new)),
            transcript_ready=lambda key, p: self.transcript_ready.emit(str(key), str(p)),
            item_error=lambda key, err_key, params: self.item_error.emit(str(key), str(err_key), dict(params or {})),
            item_output_dir=lambda key, d: self.item_output_dir.emit(str(key), str(d)),
            conflict_resolver=self._conflict_resolver,
            access_intervention_resolver=self._access_intervention_resolver,
            cancel_check=self.cancel_check,
        )

        self.session_done.emit(
            str(res.session_dir),
            bool(res.processed_any),
            bool(res.had_errors),
            bool(res.was_cancelled),
        )
        self._session_reported = True
