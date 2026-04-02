# app/controller/coordinators/files_coordinator.py
from __future__ import annotations

from typing import Any

from PyQt5 import QtCore

from app.controller.panel_protocols import FilesPanelViewProtocol
from app.controller.support.expansion_flow import start_source_expansion
from app.controller.support.panel_support import (
    push_runtime_state_to_panel,
    rebind_files_panel_view,
    start_quick_options_save,
    start_worker_lifecycle,
)
from app.controller.workers.media_probe_worker import MediaProbeWorker
from app.controller.workers.settings_worker import SettingsWorker
from app.controller.workers.source_expansion_worker import SourceExpansionWorker
from app.controller.workers.transcription_worker import TranscriptionWorker
from app.controller.workers.worker_runner import WorkerRunner
from app.model.core.domain.entities import TranscriptionSessionRequest
from app.model.core.domain.state import AppRuntimeState
from app.model.download.domain import SourceAccessInterventionResolution
from app.model.engines.manager import EngineManager


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
    access_intervention_required = QtCore.pyqtSignal(str, dict)
    session_done = QtCore.pyqtSignal(str, bool, bool, bool)
    quick_options_save_failed = QtCore.pyqtSignal(str, dict)

    def __init__(self, engine_manager: EngineManager, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._engines = engine_manager
        self._probe_runner = WorkerRunner(self)
        self._probe_worker: MediaProbeWorker | None = None
        self._pending_probe_entries: list[dict[str, Any]] | None = None

        self._expansion_runner = WorkerRunner(self)
        self._expansion_worker: SourceExpansionWorker | None = None

        self._transcription_runner = WorkerRunner(self)
        self._transcription_worker: TranscriptionWorker | None = None

        self._settings_runner = WorkerRunner(self)
        self._settings_worker: SettingsWorker | None = None

        self._view: FilesPanelViewProtocol | None = None
        self._runtime_state = AppRuntimeState()
        self._access_intervention_worker: object | None = None

    def bind_view(self, panel: FilesPanelViewProtocol) -> None:
        if self._view is panel:
            return
        rebind_files_panel_view(
            previous_view=self._view,
            new_view=panel,
            probe_table_ready=self.probe_table_ready,
            probe_item_error=self.probe_item_error,
            probe_finished=self.probe_finished,
            expansion_busy_changed=self.expansion_busy_changed,
            expansion_status_changed=self.expansion_status_changed,
            expansion_ready=self.expansion_ready,
            expansion_failed=self.expansion_failed,
            progress=self.progress,
            item_status=self.item_status,
            item_progress=self.item_progress,
            item_path_update=self.item_path_update,
            transcript_ready=self.transcript_ready,
            item_error=self.item_error,
            item_output_dir=self.item_output_dir,
            conflict_check=self.conflict_check,
            access_intervention_required=self.access_intervention_required,
            session_done=self.session_done,
            transcription_finished=self.transcription_finished,
            quick_options_save_failed=self.quick_options_save_failed,
        )
        self._view = panel
        self._push_runtime_state()

    def set_runtime_state(self, state: AppRuntimeState | None) -> None:
        self._runtime_state = state if state is not None else AppRuntimeState()
        self._push_runtime_state()

    def _push_runtime_state(self) -> None:
        push_runtime_state_to_panel(panel=self._view, state=self._runtime_state)

    def is_probe_running(self) -> bool:
        return self._probe_runner.is_running()

    def is_transcribing(self) -> bool:
        return self._transcription_runner.is_running()

    def is_expanding(self) -> bool:
        return self._expansion_runner.is_running()

    def is_options_save_running(self) -> bool:
        return self._settings_runner.is_running()

    def is_busy(self) -> bool:
        return self.is_probe_running() or self.is_transcribing() or self.is_expanding()

    def _emit_access_intervention_payload(self, payload: dict[str, object]) -> None:
        if self._expansion_runner.is_running() and self._expansion_worker is not None:
            self._set_access_intervention_worker(self._expansion_worker)
        source_key = str((payload or {}).get("source_key") or (payload or {}).get("job_key") or "")
        self.access_intervention_required.emit(source_key, dict(payload or {}))

    def _set_access_intervention_worker(self, worker: object | None) -> None:
        self._access_intervention_worker = worker

    def _set_transcription_worker(self, worker: TranscriptionWorker | None) -> None:
        self._transcription_worker = worker

    def expand_manual_input(self, raw: str) -> SourceExpansionWorker | None:
        return start_source_expansion(
            runner=self._expansion_runner,
            current_worker=self._expansion_worker,
            mode="manual_input",
            raw=str(raw or ""),
            set_worker=self._set_expansion_worker,
            emit_expansion_busy=self.expansion_busy_changed.emit,
            emit_busy=self.busy_changed.emit,
            emit_status=self.expansion_status_changed.emit,
            emit_ready=self.expansion_ready.emit,
            emit_failed=self.expansion_failed.emit,
            emit_access_intervention=self._emit_access_intervention_payload,
            is_busy=self.is_busy,
        )

    def expand_local_paths(self, paths: list[str], origin_kind: str) -> SourceExpansionWorker | None:
        return start_source_expansion(
            runner=self._expansion_runner,
            current_worker=self._expansion_worker,
            mode="local_paths",
            paths=list(paths or []),
            origin_kind=str(origin_kind or "local_paths"),
            set_worker=self._set_expansion_worker,
            emit_expansion_busy=self.expansion_busy_changed.emit,
            emit_busy=self.busy_changed.emit,
            emit_status=self.expansion_status_changed.emit,
            emit_ready=self.expansion_ready.emit,
            emit_failed=self.expansion_failed.emit,
            emit_access_intervention=self._emit_access_intervention_payload,
            is_busy=self.is_busy,
        )

    def _set_expansion_worker(self, worker: SourceExpansionWorker | None) -> None:
        if worker is None and self._access_intervention_worker is self._expansion_worker:
            self._set_access_intervention_worker(None)
        self._expansion_worker = worker

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
            def _emit_access_intervention(payload: dict[str, object]) -> None:
                self._set_access_intervention_worker(wk)
                source_key = str((payload or {}).get("source_key") or "")
                self.access_intervention_required.emit(source_key, dict(payload or {}))

            wk.table_ready.connect(self.probe_table_ready)
            wk.item_error.connect(self.probe_item_error)
            wk.access_intervention_required.connect(_emit_access_intervention)

        def _done() -> None:
            if self._access_intervention_worker is self._probe_worker:
                self._set_access_intervention_worker(None)
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
        if not self._runtime_state.transcription.ready:
            self.failed.emit("error.model.not_ready", {})
            return None

        def _connect(wk: TranscriptionWorker) -> None:
            def _emit_access_intervention(payload: dict[str, object]) -> None:
                self._set_access_intervention_worker(wk)
                source_key = str((payload or {}).get("source_key") or "")
                self.access_intervention_required.emit(source_key, dict(payload or {}))

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
            wk.access_intervention_required.connect(_emit_access_intervention)
            wk.session_done.connect(self.session_done)

        def _on_started(_worker: TranscriptionWorker) -> None:
            self.transcription_busy_changed.emit(True)
            self.busy_changed.emit(True)

        def _on_finished(worker: TranscriptionWorker) -> None:
            if self._access_intervention_worker is worker:
                self._set_access_intervention_worker(None)
            self.transcription_busy_changed.emit(False)
            self.busy_changed.emit(self.is_busy())
            self.transcription_finished.emit()

        return start_worker_lifecycle(
            runner=self._transcription_runner,
            current_worker=self._transcription_worker,
            build_worker=lambda: TranscriptionWorker(
                transcription_engine=self._engines.transcription_engine,
                translation_engine=self._engines.translation_engine,
                entries=entries,
                session_request=session_request,
            ),
            set_worker=self._set_transcription_worker,
            on_started=_on_started,
            connect_worker=_connect,
            on_finished=_on_finished,
        )

    def cancel_transcription(self) -> None:
        self._transcription_runner.cancel()

    def save_quick_options(self, payload: dict[str, Any]) -> SettingsWorker | None:
        return start_quick_options_save(
            runner=self._settings_runner,
            current_worker=self._settings_worker,
            payload=payload,
            on_failed=lambda wk: wk.failed.connect(self.quick_options_save_failed),
            on_saved=None,
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

    def resolve_access_intervention(
        self,
        _source_key: str,
        resolution: SourceAccessInterventionResolution,
    ) -> None:
        worker = self._access_intervention_worker
        if worker is None and self._probe_runner.is_running():
            worker = self._probe_worker
        if worker is None and self._expansion_runner.is_running():
            worker = self._expansion_worker
        if worker is None and self._transcription_runner.is_running():
            worker = self._transcription_worker
        if worker is None:
            return
        try:
            worker.on_access_intervention_decided(resolution)
        except (AttributeError, RuntimeError, TypeError):
            return
