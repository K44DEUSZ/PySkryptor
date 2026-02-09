# controller/tasks/transcription_task.py
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple, Union

from PyQt5 import QtCore

from model.services.transcription_service import TranscriptionService
from view.utils.translating import tr

GUIEntry = Union[str, Dict[str, Any]]


class TranscriptionWorker(QtCore.QObject):
    """Background worker that orchestrates a transcription session.

    This is a thin Controller wrapper around model.services.TranscriptionService.
    Business logic (I/O, downloading, conflict cleanup, saving transcripts,
    translation) lives in the Model layer.
    """

    finished = QtCore.pyqtSignal()
    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)

    item_status = QtCore.pyqtSignal(str, str)         # key, status string
    item_progress = QtCore.pyqtSignal(str, int)       # key, pct
    item_path_update = QtCore.pyqtSignal(str, str)    # old_key, new_key
    transcript_ready = QtCore.pyqtSignal(str, str)    # key, transcript path
    item_error = QtCore.pyqtSignal(str, str)          # key, error detail
    item_output_dir = QtCore.pyqtSignal(str, str)     # key, output directory

    conflict_check = QtCore.pyqtSignal(str, str)      # stem, existing_dir

    # session_dir, processed_any, had_errors, was_cancelled
    session_done = QtCore.pyqtSignal(str, bool, bool, bool)

    def __init__(self, pipe: Any, entries: List[GUIEntry], overrides: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._pipe = pipe
        self._entries = list(entries or [])
        self._overrides: Dict[str, Any] = dict(overrides or {})

        self._cancelled = threading.Event()

        self._conflict_event = threading.Event()
        self._conflict_action: str = "skip"
        self._conflict_new_stem: str = ""

    def cancel(self) -> None:
        self._cancelled.set()
        self._conflict_event.set()

    def on_conflict_decided(self, action: str, new_stem: str = "") -> None:
        self._conflict_action = str(action or "skip").strip().lower()
        self._conflict_new_stem = str(new_stem or "").strip()
        self._conflict_event.set()

    def _cancel_check(self) -> bool:
        if self._cancelled.is_set():
            return True
        try:
            th = QtCore.QThread.currentThread()
            if th is not None and th.isInterruptionRequested():
                return True
        except Exception:
            pass
        return False

    def _conflict_resolver(self, stem: str, existing_dir: str) -> Tuple[str, str, bool]:
        """Ask the UI how to resolve an output conflict.

        The apply-all feature is handled in the View (FilesPanel), therefore we
        always return apply_all=False here.
        """
        self._conflict_action = "skip"
        self._conflict_new_stem = ""
        self._conflict_event.clear()

        self.conflict_check.emit(str(stem), str(existing_dir))
        self._conflict_event.wait()

        if self._cancel_check():
            return "skip", "", False

        action = str(self._conflict_action or "skip").strip().lower()
        new_stem = str(self._conflict_new_stem or "").strip()
        return action, new_stem, False

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            svc = TranscriptionService()
            res = svc.run_session(
                pipe=self._pipe,
                entries=self._entries,
                translate=tr,
                log=lambda m: self.log.emit(str(m)),
                progress=lambda pct: self.progress.emit(int(pct)),
                item_status=lambda key, st: self.item_status.emit(str(key), str(st)),
                item_progress=lambda key, pct: self.item_progress.emit(str(key), int(pct)),
                item_path_update=lambda old, new: self.item_path_update.emit(str(old), str(new)),
                transcript_ready=lambda key, p: self.transcript_ready.emit(str(key), str(p)),
                item_error=lambda key, msg: self.item_error.emit(str(key), str(msg)),
                item_output_dir=lambda key, d: self.item_output_dir.emit(str(key), str(d)),
                conflict_resolver=self._conflict_resolver,
                cancel_check=self._cancel_check,
                overrides=self._overrides,
            )
            self.session_done.emit(
                str(res.session_dir),
                bool(res.processed_any),
                bool(res.had_errors),
                bool(res.was_cancelled),
            )
        except Exception as ex:
            # Keep the UI responsive; report and finish gracefully.
            try:
                self.log.emit(tr("log.worker_error", detail=str(ex)))
            except Exception:
                self.log.emit(str(ex))
            self.session_done.emit("", False, True, self._cancel_check())
        finally:
            self.finished.emit()
