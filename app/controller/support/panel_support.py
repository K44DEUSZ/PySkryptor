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
SlotBindingSpec = str | tuple[str, str]
SignalBindingSpec = tuple["BoundSignalProtocol", SlotBindingSpec]
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


def _resolve_slot_names(binding: SlotBindingSpec) -> tuple[str, str]:
    if isinstance(binding, tuple):
        return binding
    return binding, binding


def _view_slot(view: object | None, name: str) -> SignalHandler | None:
    if view is None:
        return None
    slot = getattr(view, name, None)
    return slot if callable(slot) else None


def _rebind_view_signals(
    *,
    previous_view: object | None,
    new_view: object,
    bindings: tuple[SignalBindingSpec, ...],
) -> None:
    for signal, binding in bindings:
        previous_name, new_name = _resolve_slot_names(binding)
        previous_slot = _view_slot(previous_view, previous_name)
        new_slot = _view_slot(new_view, new_name)
        if new_slot is None:
            continue
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
    access_intervention_required: BoundSignalProtocol,
    session_done: BoundSignalProtocol,
    transcription_finished: BoundSignalProtocol,
    quick_options_save_failed: BoundSignalProtocol,
) -> None:
    """Reconnect Files coordinator signals to the active Files panel view."""
    _rebind_view_signals(
        previous_view=previous_view,
        new_view=new_view,
        bindings=(
            (probe_table_ready, "on_meta_rows_ready"),
            (probe_item_error, "on_meta_item_error"),
            (probe_finished, "on_meta_finished"),
            (expansion_busy_changed, "on_expansion_busy_changed"),
            (expansion_status_changed, "on_expansion_status_changed"),
            (expansion_ready, "on_expansion_ready"),
            (expansion_failed, "on_expansion_error"),
            (progress, "on_global_progress"),
            (item_status, "on_item_status"),
            (item_progress, "on_item_progress"),
            (item_path_update, "on_item_path_update"),
            (transcript_ready, "on_transcript_ready"),
            (item_error, "on_item_error"),
            (item_output_dir, "on_item_output_dir"),
            (conflict_check, "on_conflict_check"),
            (access_intervention_required, "on_access_intervention_required"),
            (session_done, "on_session_done"),
            (transcription_finished, "on_transcribe_finished"),
            (quick_options_save_failed, "on_quick_options_save_error"),
        ),
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
    _rebind_view_signals(
        previous_view=previous_view,
        new_view=new_view,
        bindings=(
            (status, "on_status"),
            (failed, "on_worker_failed"),
            (detected_language, "on_detected_language"),
            (source_text, "on_source_text"),
            (target_text, "on_target_text"),
            (archive_source_text, "on_archive_source_text"),
            (archive_target_text, "on_archive_target_text"),
            (spectrum, "on_spectrum"),
            (finished, "on_live_finished"),
            (quick_options_save_failed, "on_quick_options_save_error"),
        ),
    )


def rebind_downloader_panel_view(
    *,
    previous_view: DownloaderPanelViewProtocol | None,
    new_view: DownloaderPanelViewProtocol,
    probe_meta_ready: BoundSignalProtocol,
    probe_failed: BoundSignalProtocol,
    access_intervention_required: BoundSignalProtocol,
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
    _rebind_view_signals(
        previous_view=previous_view,
        new_view=new_view,
        bindings=(
            (probe_meta_ready, "on_probe_ready"),
            (probe_failed, "on_probe_error"),
            (access_intervention_required, "on_access_intervention_required"),
            (expansion_busy_changed, "on_expansion_busy_changed"),
            (expansion_status_changed, "on_expansion_status_changed"),
            (expansion_ready, "on_expansion_ready"),
            (expansion_failed, "on_expansion_error"),
            (progress_pct, "on_progress_pct"),
            (stage_changed, "on_stage_changed"),
            (duplicate_check, "on_duplicate_check"),
            (download_finished, "on_download_finished"),
            (failed, "on_download_error"),
            (cancelled, "on_download_cancelled"),
            (finished, "on_download_cycle_finished"),
        ),
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
    _rebind_view_signals(
        previous_view=previous_view,
        new_view=new_view,
        bindings=(
            (failed, "on_error"),
            (settings_loaded, "on_settings_loaded"),
            (saved, "on_saved"),
        ),
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
