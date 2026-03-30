# app/controller/support/panel_support.py
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from app.controller.panel_protocols import (
    DownloaderPanelViewProtocol,
    FilesPanelViewProtocol,
    LivePanelViewProtocol,
    SettingsPanelViewProtocol,
)
from app.controller.workers.settings_worker import SettingsWorker
from app.controller.workers.worker_runner import WorkerRunner
from app.model.core.domain.state import AppRuntimeState

SignalHandler = Callable[..., None]
RuntimeStatePanelProtocol = FilesPanelViewProtocol | LivePanelViewProtocol

class BoundSignalProtocol(Protocol):
    """Minimal Qt signal surface needed by the panel rebind helpers."""

    def connect(self, slot: SignalHandler) -> object: ...

    def disconnect(self, slot: SignalHandler) -> object: ...

def _disconnect_signal(signal: BoundSignalProtocol, slot: SignalHandler | None) -> None:
    if slot is None:
        return
    try:
        signal.disconnect(slot)
    except (TypeError, RuntimeError):
        pass

def _rebind_signal(
    *,
    signal: BoundSignalProtocol,
    previous_slot: SignalHandler | None,
    new_slot: SignalHandler,
) -> None:
    _disconnect_signal(signal, previous_slot)
    signal.connect(new_slot)

def _build_runtime_state_payload(
    state: AppRuntimeState,
    *,
    pipeline: object | None = None,
) -> dict[str, object]:
    return {
        "transcription_ready": bool(state.transcription_ready and pipeline is not None),
        "transcription_error_key": state.transcription_error_key,
        "transcription_error_params": dict(state.transcription_error_params or {}),
        "translation_ready": bool(state.translation_ready),
        "translation_error_key": state.translation_error_key,
        "translation_error_params": dict(state.translation_error_params or {}),
    }

def rebind_files_panel_view(
    *,
    previous_view: FilesPanelViewProtocol | None,
    new_view: FilesPanelViewProtocol,
    probe_table_ready: BoundSignalProtocol,
    probe_item_error: BoundSignalProtocol,
    probe_finished: BoundSignalProtocol,
    expansion_busy_changed: BoundSignalProtocol,
    expansion_status_changed: BoundSignalProtocol,
    expansion_ready: BoundSignalProtocol,
    expansion_failed: BoundSignalProtocol,
    progress: BoundSignalProtocol,
    item_status: BoundSignalProtocol,
    item_progress: BoundSignalProtocol,
    item_path_update: BoundSignalProtocol,
    transcript_ready: BoundSignalProtocol,
    item_error: BoundSignalProtocol,
    item_output_dir: BoundSignalProtocol,
    conflict_check: BoundSignalProtocol,
    session_done: BoundSignalProtocol,
    transcription_finished: BoundSignalProtocol,
    quick_options_save_failed: BoundSignalProtocol,
) -> None:
    """Reconnect Files coordinator signals to the active Files panel view."""
    _rebind_signal(
        signal=probe_table_ready,
        previous_slot=previous_view.on_meta_rows_ready if previous_view is not None else None,
        new_slot=new_view.on_meta_rows_ready,
    )
    _rebind_signal(
        signal=probe_item_error,
        previous_slot=previous_view.on_meta_item_error if previous_view is not None else None,
        new_slot=new_view.on_meta_item_error,
    )
    _rebind_signal(
        signal=probe_finished,
        previous_slot=previous_view.on_meta_finished if previous_view is not None else None,
        new_slot=new_view.on_meta_finished,
    )
    _rebind_signal(
        signal=expansion_busy_changed,
        previous_slot=previous_view.on_expansion_busy_changed if previous_view is not None else None,
        new_slot=new_view.on_expansion_busy_changed,
    )
    _rebind_signal(
        signal=expansion_status_changed,
        previous_slot=previous_view.on_expansion_status_changed if previous_view is not None else None,
        new_slot=new_view.on_expansion_status_changed,
    )
    _rebind_signal(
        signal=expansion_ready,
        previous_slot=previous_view.on_expansion_ready if previous_view is not None else None,
        new_slot=new_view.on_expansion_ready,
    )
    _rebind_signal(
        signal=expansion_failed,
        previous_slot=previous_view.on_expansion_error if previous_view is not None else None,
        new_slot=new_view.on_expansion_error,
    )
    _rebind_signal(
        signal=progress,
        previous_slot=previous_view.on_global_progress if previous_view is not None else None,
        new_slot=new_view.on_global_progress,
    )
    _rebind_signal(
        signal=item_status,
        previous_slot=previous_view.on_item_status if previous_view is not None else None,
        new_slot=new_view.on_item_status,
    )
    _rebind_signal(
        signal=item_progress,
        previous_slot=previous_view.on_item_progress if previous_view is not None else None,
        new_slot=new_view.on_item_progress,
    )
    _rebind_signal(
        signal=item_path_update,
        previous_slot=previous_view.on_item_path_update if previous_view is not None else None,
        new_slot=new_view.on_item_path_update,
    )
    _rebind_signal(
        signal=transcript_ready,
        previous_slot=previous_view.on_transcript_ready if previous_view is not None else None,
        new_slot=new_view.on_transcript_ready,
    )
    _rebind_signal(
        signal=item_error,
        previous_slot=previous_view.on_item_error if previous_view is not None else None,
        new_slot=new_view.on_item_error,
    )
    _rebind_signal(
        signal=item_output_dir,
        previous_slot=previous_view.on_item_output_dir if previous_view is not None else None,
        new_slot=new_view.on_item_output_dir,
    )
    _rebind_signal(
        signal=conflict_check,
        previous_slot=previous_view.on_conflict_check if previous_view is not None else None,
        new_slot=new_view.on_conflict_check,
    )
    _rebind_signal(
        signal=session_done,
        previous_slot=previous_view.on_session_done if previous_view is not None else None,
        new_slot=new_view.on_session_done,
    )
    _rebind_signal(
        signal=transcription_finished,
        previous_slot=previous_view.on_transcribe_finished if previous_view is not None else None,
        new_slot=new_view.on_transcribe_finished,
    )
    _rebind_signal(
        signal=quick_options_save_failed,
        previous_slot=previous_view.on_quick_options_save_error if previous_view is not None else None,
        new_slot=new_view.on_quick_options_save_error,
    )

def rebind_live_panel_view(
    *,
    previous_view: LivePanelViewProtocol | None,
    new_view: LivePanelViewProtocol,
    status: BoundSignalProtocol,
    failed: BoundSignalProtocol,
    detected_language: BoundSignalProtocol,
    source_text: BoundSignalProtocol,
    target_text: BoundSignalProtocol,
    archive_source_text: BoundSignalProtocol,
    archive_target_text: BoundSignalProtocol,
    spectrum: BoundSignalProtocol,
    finished: BoundSignalProtocol,
    quick_options_save_failed: BoundSignalProtocol,
) -> None:
    """Reconnect Live coordinator signals to the active Live panel view."""
    _rebind_signal(
        signal=status,
        previous_slot=previous_view.on_status if previous_view is not None else None,
        new_slot=new_view.on_status,
    )
    _rebind_signal(
        signal=failed,
        previous_slot=previous_view.on_worker_failed if previous_view is not None else None,
        new_slot=new_view.on_worker_failed,
    )
    _rebind_signal(
        signal=detected_language,
        previous_slot=previous_view.on_detected_language if previous_view is not None else None,
        new_slot=new_view.on_detected_language,
    )
    _rebind_signal(
        signal=source_text,
        previous_slot=previous_view.on_source_text if previous_view is not None else None,
        new_slot=new_view.on_source_text,
    )
    _rebind_signal(
        signal=target_text,
        previous_slot=previous_view.on_target_text if previous_view is not None else None,
        new_slot=new_view.on_target_text,
    )
    _rebind_signal(
        signal=archive_source_text,
        previous_slot=previous_view.on_archive_source_text if previous_view is not None else None,
        new_slot=new_view.on_archive_source_text,
    )
    _rebind_signal(
        signal=archive_target_text,
        previous_slot=previous_view.on_archive_target_text if previous_view is not None else None,
        new_slot=new_view.on_archive_target_text,
    )
    _rebind_signal(
        signal=spectrum,
        previous_slot=previous_view.on_spectrum if previous_view is not None else None,
        new_slot=new_view.on_spectrum,
    )
    _rebind_signal(
        signal=finished,
        previous_slot=previous_view.on_live_finished if previous_view is not None else None,
        new_slot=new_view.on_live_finished,
    )
    _rebind_signal(
        signal=quick_options_save_failed,
        previous_slot=previous_view.on_quick_options_save_error if previous_view is not None else None,
        new_slot=new_view.on_quick_options_save_error,
    )

def rebind_downloader_panel_view(
    *,
    previous_view: DownloaderPanelViewProtocol | None,
    new_view: DownloaderPanelViewProtocol,
    probe_meta_ready: BoundSignalProtocol,
    probe_failed: BoundSignalProtocol,
    cookie_intervention_required: BoundSignalProtocol,
    expansion_busy_changed: BoundSignalProtocol,
    expansion_status_changed: BoundSignalProtocol,
    expansion_ready: BoundSignalProtocol,
    expansion_failed: BoundSignalProtocol,
    progress_pct: BoundSignalProtocol,
    stage_changed: BoundSignalProtocol,
    duplicate_check: BoundSignalProtocol,
    download_finished: BoundSignalProtocol,
    failed: BoundSignalProtocol,
    cancelled: BoundSignalProtocol,
    finished: BoundSignalProtocol,
) -> None:
    """Reconnect Downloader coordinator signals to the active Downloader panel view."""
    _rebind_signal(
        signal=probe_meta_ready,
        previous_slot=previous_view.on_probe_ready if previous_view is not None else None,
        new_slot=new_view.on_probe_ready,
    )
    _rebind_signal(
        signal=probe_failed,
        previous_slot=previous_view.on_probe_error if previous_view is not None else None,
        new_slot=new_view.on_probe_error,
    )
    _rebind_signal(
        signal=cookie_intervention_required,
        previous_slot=(
            previous_view.on_cookie_intervention_required if previous_view is not None else None
        ),
        new_slot=new_view.on_cookie_intervention_required,
    )
    _rebind_signal(
        signal=expansion_busy_changed,
        previous_slot=previous_view.on_expansion_busy_changed if previous_view is not None else None,
        new_slot=new_view.on_expansion_busy_changed,
    )
    _rebind_signal(
        signal=expansion_status_changed,
        previous_slot=previous_view.on_expansion_status_changed if previous_view is not None else None,
        new_slot=new_view.on_expansion_status_changed,
    )
    _rebind_signal(
        signal=expansion_ready,
        previous_slot=previous_view.on_expansion_ready if previous_view is not None else None,
        new_slot=new_view.on_expansion_ready,
    )
    _rebind_signal(
        signal=expansion_failed,
        previous_slot=previous_view.on_expansion_error if previous_view is not None else None,
        new_slot=new_view.on_expansion_error,
    )
    _rebind_signal(
        signal=progress_pct,
        previous_slot=previous_view.on_progress_pct if previous_view is not None else None,
        new_slot=new_view.on_progress_pct,
    )
    _rebind_signal(
        signal=stage_changed,
        previous_slot=previous_view.on_stage_changed if previous_view is not None else None,
        new_slot=new_view.on_stage_changed,
    )
    _rebind_signal(
        signal=duplicate_check,
        previous_slot=previous_view.on_duplicate_check if previous_view is not None else None,
        new_slot=new_view.on_duplicate_check,
    )
    _rebind_signal(
        signal=download_finished,
        previous_slot=previous_view.on_download_finished if previous_view is not None else None,
        new_slot=new_view.on_download_finished,
    )
    _rebind_signal(
        signal=failed,
        previous_slot=previous_view.on_download_error if previous_view is not None else None,
        new_slot=new_view.on_download_error,
    )
    _rebind_signal(
        signal=cancelled,
        previous_slot=previous_view.on_download_cancelled if previous_view is not None else None,
        new_slot=new_view.on_download_cancelled,
    )
    _rebind_signal(
        signal=finished,
        previous_slot=previous_view.on_download_cycle_finished if previous_view is not None else None,
        new_slot=new_view.on_download_cycle_finished,
    )

def rebind_settings_panel_view(
    *,
    previous_view: SettingsPanelViewProtocol | None,
    new_view: SettingsPanelViewProtocol,
    failed: BoundSignalProtocol,
    settings_loaded: BoundSignalProtocol,
    saved: BoundSignalProtocol,
) -> None:
    """Reconnect Settings coordinator signals to the active Settings panel view."""
    _rebind_signal(
        signal=failed,
        previous_slot=previous_view.on_error if previous_view is not None else None,
        new_slot=new_view.on_error,
    )
    _rebind_signal(
        signal=settings_loaded,
        previous_slot=previous_view.on_settings_loaded if previous_view is not None else None,
        new_slot=new_view.on_settings_loaded,
    )
    _rebind_signal(
        signal=saved,
        previous_slot=previous_view.on_saved if previous_view is not None else None,
        new_slot=new_view.on_saved,
    )

def push_runtime_state_to_panel(
    *,
    panel: RuntimeStatePanelProtocol | None,
    state: AppRuntimeState,
    pipeline: object | None,
) -> None:
    """Push the normalized runtime-state payload into a bound panel view."""
    if panel is None:
        return
    panel.on_runtime_state_changed(**_build_runtime_state_payload(state, pipeline=pipeline))

def start_quick_options_save(
    *,
    runner: WorkerRunner,
    current_worker: SettingsWorker | None,
    payload: dict[str, object],
    on_failed: Callable[[SettingsWorker], None],
    set_worker: Callable[[SettingsWorker | None], None],
) -> SettingsWorker | None:
    """Start the shared quick-options save flow for panel coordinators."""
    if runner.is_running():
        return current_worker

    worker = SettingsWorker(action="save", payload=dict(payload or {}))
    set_worker(worker)

    def _connect(wk: SettingsWorker) -> None:
        on_failed(wk)

    def _done() -> None:
        set_worker(None)

    return runner.start(worker, connect=_connect, on_finished=_done)
