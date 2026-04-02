# app/controller/coordinators/live_coordinator.py
from __future__ import annotations

from typing import Any

from PyQt5 import QtCore

from app.controller.panel_protocols import LivePanelViewProtocol
from app.controller.platform.microphone import list_input_device_names
from app.controller.support.panel_support import (
    push_runtime_state_to_panel,
    rebind_live_panel_view,
    start_quick_options_save,
    start_worker_lifecycle,
)
from app.controller.workers.live_worker import LiveWorker
from app.controller.workers.settings_worker import SettingsWorker
from app.controller.workers.worker_runner import WorkerRunner
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.entities import SettingsSnapshot
from app.model.core.domain.state import AppRuntimeState
from app.model.engines.manager import EngineManager
from app.model.transcription.writer import TranscriptWriter


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
    quick_options_saved = QtCore.pyqtSignal(object)
    quick_options_save_failed = QtCore.pyqtSignal(str, dict)

    def __init__(self, engine_manager: EngineManager, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._engines = engine_manager
        self._runner = WorkerRunner(self)
        self._worker: LiveWorker | None = None
        self._settings_runner = WorkerRunner(self)
        self._settings_worker: SettingsWorker | None = None
        self._view: LivePanelViewProtocol | None = None
        self._runtime_state = AppRuntimeState()
        self._input_devices_provider = list_input_device_names

    def bind_view(self, panel: LivePanelViewProtocol) -> None:
        if self._view is panel:
            return
        rebind_live_panel_view(
            previous_view=self._view,
            new_view=panel,
            status=self.status,
            failed=self.failed,
            detected_language=self.detected_language,
            source_text=self.source_text,
            target_text=self.target_text,
            archive_source_text=self.archive_source_text,
            archive_target_text=self.archive_target_text,
            spectrum=self.spectrum,
            finished=self.finished,
            quick_options_saved=self.quick_options_saved,
            quick_options_save_failed=self.quick_options_save_failed,
        )
        self._view = panel
        self._push_runtime_state()

    def set_runtime_state(self, state: AppRuntimeState | None) -> None:
        self._runtime_state = state if state is not None else AppRuntimeState()
        self._push_runtime_state()

    def _push_runtime_state(self) -> None:
        push_runtime_state_to_panel(panel=self._view, state=self._runtime_state)

    def is_running(self) -> bool:
        return self._runner.is_running()

    def is_options_save_running(self) -> bool:
        return self._settings_runner.is_running()

    def _set_worker(self, worker: LiveWorker | None) -> None:
        self._worker = worker

    def list_input_devices(self) -> list[str]:
        return self._input_devices_provider()

    @staticmethod
    def save_transcript(
        *,
        target_path: str,
        source_text: str,
        target_text: str,
        write_source_companion: bool,
    ) -> list[str]:
        """Persist the current live transcript using the shared writer."""
        return [
            str(path)
            for path in TranscriptWriter.save_live_transcript(
                target_path=target_path,
                source_text=source_text,
                target_text=target_text,
                write_source_companion=write_source_companion,
            )
        ]

    def start_session(
        self,
        *,
        device_name: str = "",
        source_language: str = "",
        target_language: str = "",
        translate_enabled: bool = False,
        profile: str = RuntimeProfiles.LIVE_DEFAULT_PROFILE,
        runtime_profile: dict[str, object] | None = None,
        output_mode: str = RuntimeProfiles.LIVE_OUTPUT_MODE_CUMULATIVE,
    ) -> LiveWorker | None:
        if not self._runtime_state.transcription.ready:
            self.failed.emit("error.model.not_ready", {})
            return None

        def _connect(wk: LiveWorker) -> None:
            wk.status.connect(self.status)
            wk.detected_language.connect(self.detected_language)
            wk.source_text.connect(self.source_text)
            wk.target_text.connect(self.target_text)
            wk.archive_source_text.connect(self.archive_source_text)
            wk.archive_target_text.connect(self.archive_target_text)
            wk.spectrum.connect(self.spectrum)
            wk.failed.connect(self.failed)

        def _on_started(_worker: LiveWorker) -> None:
            self.busy_changed.emit(True)

        def _on_finished(_worker: LiveWorker) -> None:
            self.busy_changed.emit(False)
            self.finished.emit()

        return start_worker_lifecycle(
            runner=self._runner,
            current_worker=self._worker,
            build_worker=lambda: LiveWorker(
                transcription_engine=self._engines.transcription_engine,
                translation_engine=self._engines.translation_engine,
                device_name=device_name,
                source_language=source_language,
                target_language=target_language,
                translate_enabled=translate_enabled,
                profile=profile,
                runtime_profile=runtime_profile,
                output_mode=output_mode,
            ),
            set_worker=self._set_worker,
            on_started=_on_started,
            connect_worker=_connect,
            on_finished=_on_finished,
        )

    def save_quick_options(self, payload: dict[str, Any]) -> SettingsWorker | None:
        return start_quick_options_save(
            runner=self._settings_runner,
            current_worker=self._settings_worker,
            payload=payload,
            on_failed=lambda wk: wk.failed.connect(self.quick_options_save_failed),
            on_saved=self._bind_quick_options_saved,
            set_worker=self._set_settings_worker,
        )

    def _set_settings_worker(self, worker: SettingsWorker | None) -> None:
        self._settings_worker = worker

    def _bind_quick_options_saved(self, worker: SettingsWorker) -> None:
        worker.saved.connect(self._on_quick_options_saved)

    def _on_quick_options_saved(self, action: str, snap: SettingsSnapshot) -> None:
        if str(action or "").strip().lower() != "save":
            return
        self.quick_options_saved.emit(snap)

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
