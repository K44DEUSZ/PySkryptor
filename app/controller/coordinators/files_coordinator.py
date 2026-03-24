# app/controller/coordinators/files_coordinator.py
from __future__ import annotations

from typing import Any

from PyQt5 import QtCore

from app.controller.contracts import FilesPanelViewProtocol
from app.controller.workers.media_probe_worker import MediaProbeWorker
from app.controller.workers.settings_worker import SettingsWorker
from app.controller.workers.source_expansion_worker import SourceExpansionWorker
from app.controller.workers.task_thread_runner import TaskThreadRunner
from app.controller.support.quick_settings import start_settings_save
from app.controller.workers.transcription_worker import TranscriptionWorker
from app.model.domain.entities import TranscriptionSessionRequest
from app.model.domain.runtime_state import AppRuntimeState


class FilesCoordinator(QtCore.QObject):
    """Owns Files-panel workers and re-emits a stable controller contract."""

    busy_changed = QtCore.pyqtSignal(bool)
    probe_busy_changed = QtCore.pyqtSignal(bool)
    transcription_busy_changed = QtCore.pyqtSignal(bool)
    expansion_busy_changed = QtCore.pyqtSignal(bool)
    expansion_status_changed = QtCore.pyqtSignal(str, dict)

    probe_table_ready = QtCore.pyqtSignal(list)
    probe_item_error = QtCore.pyqtSignal(str, str, dict)
    probe_finished = QtCore.pyqtSignal()

    expansion_ready = QtCore.pyqtSignal(object)
    expansion_failed = QtCore.pyqtSignal(str, dict)

    progress = QtCore.pyqtSignal(int)
    failed = QtCore.pyqtSignal(str, dict)
    cancelled = QtCore.pyqtSignal()
    transcription_finished = QtCore.pyqtSignal()
    item_status = QtCore.pyqtSignal(str, str)
    item_progress = QtCore.pyqtSignal(str, int)
    item_path_update = QtCore.pyqtSignal(str, str)
    transcript_ready = QtCore.pyqtSignal(str, str)
    item_error = QtCore.pyqtSignal(str, str, dict)
    item_output_dir = QtCore.pyqtSignal(str, str)
    conflict_check = QtCore.pyqtSignal(str, str)
    session_done = QtCore.pyqtSignal(str, bool, bool, bool)
    quick_options_save_failed = QtCore.pyqtSignal(str, dict)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._probe_runner = TaskThreadRunner(self)
        self._probe_worker: MediaProbeWorker | None = None
        self._pending_probe_entries: list[dict[str, Any]] | None = None

        self._expansion_runner = TaskThreadRunner(self)
        self._expansion_worker: SourceExpansionWorker | None = None

        self._transcription_runner = TaskThreadRunner(self)
        self._transcription_worker: TranscriptionWorker | None = None

        self._settings_runner = TaskThreadRunner(self)
        self._settings_worker: SettingsWorker | None = None

        self._view: FilesPanelViewProtocol | None = None
        self._runtime_state = AppRuntimeState()
        self._pipe: Any | None = None

    def bind_view(self, panel: FilesPanelViewProtocol) -> None:
        if self._view is panel:
            return
        previous = self._view
        if previous is not None:
            for signal, slot in (
                (self.probe_table_ready, previous.on_meta_rows_ready),
                (self.probe_item_error, previous.on_meta_item_error),
                (self.probe_finished, previous.on_meta_finished),
                (self.expansion_busy_changed, previous.on_expansion_busy_changed),
                (self.expansion_status_changed, previous.on_expansion_status_changed),
                (self.expansion_ready, previous.on_expansion_ready),
                (self.expansion_failed, previous.on_expansion_error),
                (self.progress, previous.on_global_progress),
                (self.item_status, previous.on_item_status),
                (self.item_progress, previous.on_item_progress),
                (self.item_path_update, previous.on_item_path_update),
                (self.transcript_ready, previous.on_transcript_ready),
                (self.item_error, previous.on_item_error),
                (self.item_output_dir, previous.on_item_output_dir),
                (self.conflict_check, previous.on_conflict_check),
                (self.session_done, previous.on_session_done),
                (self.transcription_finished, previous.on_transcribe_finished),
                (self.quick_options_save_failed, previous.on_quick_options_save_error),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._view = panel
        self.probe_table_ready.connect(panel.on_meta_rows_ready)
        self.probe_item_error.connect(panel.on_meta_item_error)
        self.probe_finished.connect(panel.on_meta_finished)
        self.expansion_busy_changed.connect(panel.on_expansion_busy_changed)
        self.expansion_status_changed.connect(panel.on_expansion_status_changed)
        self.expansion_ready.connect(panel.on_expansion_ready)
        self.expansion_failed.connect(panel.on_expansion_error)
        self.progress.connect(panel.on_global_progress)
        self.item_status.connect(panel.on_item_status)
        self.item_progress.connect(panel.on_item_progress)
        self.item_path_update.connect(panel.on_item_path_update)
        self.transcript_ready.connect(panel.on_transcript_ready)
        self.item_error.connect(panel.on_item_error)
        self.item_output_dir.connect(panel.on_item_output_dir)
        self.conflict_check.connect(panel.on_conflict_check)
        self.session_done.connect(panel.on_session_done)
        self.transcription_finished.connect(panel.on_transcribe_finished)
        self.quick_options_save_failed.connect(panel.on_quick_options_save_error)
        self._push_runtime_state()

    def set_runtime_state(self, state: AppRuntimeState | None) -> None:
        self._runtime_state = state if state is not None else AppRuntimeState()
        self._pipe = self._runtime_state.transcription_pipeline if self._runtime_state.transcription_ready else None
        self._push_runtime_state()

    def _push_runtime_state(self) -> None:
        panel = self._view
        if panel is None:
            return
        panel.on_runtime_state_changed(
            transcription_ready=bool(self._runtime_state.transcription_ready and self._pipe is not None),
            transcription_error_key=self._runtime_state.transcription_error_key,
            transcription_error_params=dict(self._runtime_state.transcription_error_params or {}),
            translation_ready=bool(self._runtime_state.translation_ready),
            translation_error_key=self._runtime_state.translation_error_key,
            translation_error_params=dict(self._runtime_state.translation_error_params or {}),
        )

    def is_probe_running(self) -> bool:
        return self._probe_runner.is_running()

    def is_transcribing(self) -> bool:
        return self._transcription_runner.is_running()

    def is_expanding(self) -> bool:
        return self._expansion_runner.is_running()

    def is_busy(self) -> bool:
        return self.is_probe_running() or self.is_transcribing() or self.is_expanding()

    def expand_manual_input(self, raw: str) -> SourceExpansionWorker | None:
        if self._expansion_runner.is_running():
            return self._expansion_worker
        worker = SourceExpansionWorker(mode="manual_input", raw=str(raw or ""))
        return self._start_expansion_worker(worker)

    def expand_local_paths(self, paths: list[str], origin_kind: str) -> SourceExpansionWorker | None:
        if self._expansion_runner.is_running():
            return self._expansion_worker
        worker = SourceExpansionWorker(mode="local_paths", paths=list(paths or []), origin_kind=str(origin_kind or "local_paths"))
        return self._start_expansion_worker(worker)

    def _start_expansion_worker(self, worker: SourceExpansionWorker) -> SourceExpansionWorker | None:
        self._expansion_worker = worker
        self.expansion_busy_changed.emit(True)
        self.busy_changed.emit(True)

        def _connect(wk: SourceExpansionWorker) -> None:
            wk.status_changed.connect(self.expansion_status_changed)
            wk.expanded.connect(self.expansion_ready)
            wk.failed.connect(self.expansion_failed)

        def _done() -> None:
            self._expansion_worker = None
            self.expansion_busy_changed.emit(False)
            self.expansion_status_changed.emit("", {})
            self.busy_changed.emit(self.is_busy())

        return self._expansion_runner.start(worker, connect=_connect, on_finished=_done)

    def cancel_expansion(self) -> None:
        self._expansion_runner.cancel()

    def start_probe(self, entries: list[dict[str, Any]]) -> MediaProbeWorker | None:
        normalized = [dict(entry or {}) for entry in entries or [] if isinstance(entry, dict)]
        if not normalized:
            return None
        self._pending_probe_entries = list(normalized)
        if self._probe_runner.is_running():
            self._probe_runner.cancel()
            return self._probe_worker
        return self._start_probe_worker(normalized)

    def _start_probe_worker(self, entries: list[dict[str, Any]]) -> MediaProbeWorker | None:
        self._pending_probe_entries = None
        worker = MediaProbeWorker(entries)
        self._probe_worker = worker
        self.probe_busy_changed.emit(True)
        self.busy_changed.emit(True)

        def _connect(wk: MediaProbeWorker) -> None:
            wk.table_ready.connect(self.probe_table_ready)
            wk.item_error.connect(self.probe_item_error)

        def _done() -> None:
            self._probe_worker = None
            pending = self._pending_probe_entries
            self._pending_probe_entries = None
            self.probe_busy_changed.emit(False)
            self.busy_changed.emit(self.is_busy())
            self.probe_finished.emit()
            if pending:
                pending_entries = [dict(entry) for entry in pending]
                QtCore.QTimer.singleShot(0, lambda: self._start_probe_worker(pending_entries))

        return self._probe_runner.start(worker, connect=_connect, on_finished=_done)

    def cancel_probe(self) -> None:
        self._probe_runner.cancel()

    def start_transcription(
        self,
        *,
        entries: list[str | dict[str, Any]],
        session_request: TranscriptionSessionRequest,
    ) -> TranscriptionWorker | None:
        if self._transcription_runner.is_running():
            return self._transcription_worker
        if self._pipe is None:
            self.failed.emit("error.model.not_ready", {})
            return None

        worker = TranscriptionWorker(pipe=self._pipe, entries=entries, session_request=session_request)
        self._transcription_worker = worker
        self.transcription_busy_changed.emit(True)
        self.busy_changed.emit(True)

        def _connect(wk: TranscriptionWorker) -> None:
            wk.progress.connect(self.progress)
            wk.failed.connect(self.failed)
            wk.cancelled.connect(self.cancelled)
            wk.item_status.connect(self.item_status)
            wk.item_progress.connect(self.item_progress)
            wk.item_path_update.connect(self.item_path_update)
            wk.transcript_ready.connect(self.transcript_ready)
            wk.item_error.connect(self.item_error)
            wk.item_output_dir.connect(self.item_output_dir)
            wk.conflict_check.connect(self.conflict_check)
            wk.session_done.connect(self.session_done)

        def _done() -> None:
            self._transcription_worker = None
            self.transcription_busy_changed.emit(False)
            self.busy_changed.emit(self.is_busy())
            self.transcription_finished.emit()

        return self._transcription_runner.start(worker, connect=_connect, on_finished=_done)

    def cancel_transcription(self) -> None:
        self._transcription_runner.cancel()

    def save_quick_options(self, payload: dict[str, Any]) -> SettingsWorker | None:
        return start_settings_save(
            runner=self._settings_runner,
            current_worker=self._settings_worker,
            payload=payload,
            on_failed=lambda wk: wk.failed.connect(self.quick_options_save_failed),
            set_worker=self._set_settings_worker,
        )

    def _set_settings_worker(self, worker: SettingsWorker | None) -> None:
        self._settings_worker = worker

    def resolve_conflict(self, action: str, new_stem: str = "") -> None:
        wk = self._transcription_worker
        if wk is None:
            return
        try:
            wk.on_conflict_decided(action, new_stem)
        except (AttributeError, RuntimeError, TypeError):
            return
