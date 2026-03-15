# app/controller/tasks/transcription_task.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple, Union, Optional

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.controller.tasks.base_worker import BaseWorker, PendingDecision
from app.model.helpers.string_utils import normalize_lang_code
from app.model.services.transcription_service import TranscriptionService
from app.controller.support.runtime_resolver import resolve_translation_target
from app.controller.support.localization import Translator

_LOG = logging.getLogger(__name__)

SourceEntry = Union[str, Dict[str, Any]]


def build_overrides(
    *,
    source_language: str,
    target_language: str,
    translate_after_transcription: bool,
) -> Dict[str, Any]:
    src = normalize_lang_code(source_language, drop_region=True) if source_language else ''

    tgt_raw = str(target_language or '').strip().lower()
    tgt_resolved = resolve_translation_target(
        tgt_raw,
        ui_language=Translator.current_language(),
        cfg_target=None,
        supported=None,
    )

    tgt = normalize_lang_code(tgt_resolved, drop_region=True) if tgt_resolved else ''

    return {
        'source_language': '' if src in ('', 'auto') else src,
        'target_language': tgt,
        'translate_after_transcription': bool(translate_after_transcription),
    }


class TranscriptionWorker(BaseWorker):
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
        entries: List[SourceEntry],
        overrides: Optional[Dict[str, Any]] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> None:
        super().__init__(cancel_token=cancel_token)
        self._pipe = pipe
        self._entries = list(entries or [])
        self._overrides = dict(overrides or {})
        self._session_reported = False

        self.failed.connect(self._on_failed)
        self.cancelled.connect(self._on_cancelled)

        self._conflict_decision = PendingDecision(default_action="skip")

    # ----- Control -----

    def cancel(self) -> None:
        super().cancel()
        self._cancel_pending_decision(self._conflict_decision)

    @QtCore.pyqtSlot(str, str)
    def on_conflict_decided(self, action: str, new_stem: str = '') -> None:
        self._set_pending_decision(
            self._conflict_decision,
            action=str(action or 'skip').strip().lower(),
            value=str(new_stem or '').strip(),
        )

    # ----- Internals -----

    def _conflict_resolver(self, stem: str, existing_dir: str) -> Tuple[str, str, bool]:
        self._conflict_decision.reset()

        self.conflict_check.emit(str(stem), str(existing_dir))
        action, new_stem = self._wait_for_pending_decision(self._conflict_decision)

        if self.cancel_check():
            return ('skip', '', False)

        action = str(action or 'skip').strip().lower()
        new_stem = str(new_stem or '').strip()
        return (action, new_stem, False)

    # ----- Run -----

    def _on_failed(self, err_key: str, params: dict) -> None:
        if self._session_reported:
            return
        self.item_error.emit('', str(err_key), dict(params or {}))
        self.session_done.emit('', False, True, False)
        self._session_reported = True

    def _on_cancelled(self) -> None:
        if self._session_reported:
            return
        self.session_done.emit('', False, False, True)
        self._session_reported = True

    def _execute(self) -> None:
        svc = TranscriptionService()
        res = svc.run_session(
            pipe=self._pipe,
            entries=self._entries,
            overrides=self._overrides,
            progress=lambda pct: self.progress.emit(int(pct)),
            item_status=lambda key, st: self.item_status.emit(str(key), str(st)),
            item_progress=lambda key, pct: self.item_progress.emit(str(key), int(pct)),
            item_path_update=lambda old, new: self.item_path_update.emit(str(old), str(new)),
            transcript_ready=lambda key, p: self.transcript_ready.emit(str(key), str(p)),
            item_error=lambda key, err_key, params: self.item_error.emit(str(key), str(err_key), dict(params or {})),
            item_output_dir=lambda key, d: self.item_output_dir.emit(str(key), str(d)),
            conflict_resolver=self._conflict_resolver,
            cancel_check=self.cancel_check,
        )

        self.session_done.emit(
            str(res.session_dir),
            bool(res.processed_any),
            bool(res.had_errors),
            bool(res.was_cancelled),
        )
        self._session_reported = True
