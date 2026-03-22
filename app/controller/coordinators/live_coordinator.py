# app/controller/coordinators/live_coordinator.py
from __future__ import annotations

from typing import Any

from PyQt5 import QtCore

from app.controller.contracts import LivePanelViewProtocol
from app.controller.platform.microphone import list_input_device_names
from app.controller.workers.live_transcription_worker import LiveTranscriptionWorker
from app.controller.workers.settings_worker import SettingsWorker
from app.controller.workers.task_thread_runner import TaskThreadRunner
from app.model.domain.runtime_state import AppRuntimeState


class LiveCoordinator(QtCore.QObject):
    """Owns the live-transcription worker lifecycle for the Live panel."""

    busy_changed = QtCore.pyqtSignal(bool)
    status = QtCore.pyqtSignal(str)
    detected_language = QtCore.pyqtSignal(str)
    source_text = QtCore.pyqtSignal(str)
    target_text = QtCore.pyqtSignal(str)
    archive_source_text = QtCore.pyqtSignal(str)
    archive_target_text = QtCore.pyqtSignal(str)
    spectrum = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str, dict)
    finished = QtCore.pyqtSignal()
    quick_options_save_failed = QtCore.pyqtSignal(str, dict)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = TaskThreadRunner(self)
        self._worker: LiveTranscriptionWorker | None = None
        self._settings_runner = TaskThreadRunner(self)
        self._settings_worker: SettingsWorker | None = None
        self._view: LivePanelViewProtocol | None = None
        self._runtime_state = AppRuntimeState()
        self._pipe: Any | None = None

    def bind_view(self, panel: LivePanelViewProtocol) -> None:
        if self._view is panel:
            return
        previous = self._view
        if previous is not None:
            for signal, slot in (
                (self.status, previous.on_status),
                (self.failed, previous.on_worker_failed),
                (self.detected_language, previous.on_detected_language),
                (self.source_text, previous.on_source_text),
                (self.target_text, previous.on_target_text),
                (self.archive_source_text, previous.on_archive_source_text),
                (self.archive_target_text, previous.on_archive_target_text),
                (self.spectrum, previous.on_spectrum),
                (self.finished, previous.on_live_finished),
                (self.quick_options_save_failed, previous.on_quick_options_save_error),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._view = panel
        self.status.connect(panel.on_status)
        self.failed.connect(panel.on_worker_failed)
        self.detected_language.connect(panel.on_detected_language)
        self.source_text.connect(panel.on_source_text)
        self.target_text.connect(panel.on_target_text)
        self.archive_source_text.connect(panel.on_archive_source_text)
        self.archive_target_text.connect(panel.on_archive_target_text)
        self.spectrum.connect(panel.on_spectrum)
        self.finished.connect(panel.on_live_finished)
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

    def is_running(self) -> bool:
        return self._runner.is_running()

    @staticmethod
    def list_input_devices() -> list[str]:
        return list_input_device_names()

    def start_session(
        self,
        *,
        device_name: str = "",
        source_language: str = "",
        target_language: str = "",
        translate_enabled: bool = False,
        preset_id: str = "default",
        output_mode: str = "cumulative",
    ) -> LiveTranscriptionWorker | None:
        if self._runner.is_running():
            return self._worker
        if self._pipe is None:
            self.failed.emit("error.model.not_ready", {})
            return None

        worker = LiveTranscriptionWorker(
            pipe=self._pipe,
            device_name=device_name,
            source_language=source_language,
            target_language=target_language,
            translate_enabled=translate_enabled,
            preset_id=preset_id,
            output_mode=output_mode,
        )
        self._worker = worker
        self.busy_changed.emit(True)

        def _connect(wk: LiveTranscriptionWorker) -> None:
            wk.status.connect(self.status)
            wk.detected_language.connect(self.detected_language)
            wk.source_text.connect(self.source_text)
            wk.target_text.connect(self.target_text)
            wk.archive_source_text.connect(self.archive_source_text)
            wk.archive_target_text.connect(self.archive_target_text)
            wk.spectrum.connect(self.spectrum)
            wk.failed.connect(self.failed)

        def _done() -> None:
            self._worker = None
            self.busy_changed.emit(False)
            self.finished.emit()

        return self._runner.start(worker, connect=_connect, on_finished=_done)

    def save_quick_options(self, payload: dict[str, Any]) -> SettingsWorker | None:
        if self._settings_runner.is_running():
            return self._settings_worker
        worker = SettingsWorker(action="save", payload=dict(payload or {}))
        self._settings_worker = worker

        def _connect(wk: SettingsWorker) -> None:
            wk.failed.connect(self.quick_options_save_failed)

        def _done() -> None:
            self._settings_worker = None

        return self._settings_runner.start(worker, connect=_connect, on_finished=_done)

    def cancel(self) -> None:
        self._runner.cancel()

    def stop(self) -> None:
        self._runner.stop()

    def pause(self) -> None:
        wk = self._worker
        if wk is None:
            return
        try:
            wk.pause()
        except RuntimeError:
            return

    def resume(self) -> None:
        wk = self._worker
        if wk is None:
            return
        try:
            wk.resume()
        except RuntimeError:
            return
