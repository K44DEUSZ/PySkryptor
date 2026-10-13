# app/application/coordinators/app_coordinator.py
from __future__ import annotations

from dataclasses import replace

from PyQt6 import QtCore

from app.application.coordinators.downloader_coordinator import DownloaderCoordinator
from app.application.coordinators.files_coordinator import FilesCoordinator
from app.application.coordinators.live_coordinator import LiveCoordinator
from app.application.coordinators.runtime_coordinator import RuntimeCoordinator
from app.application.coordinators.settings_coordinator import SettingsCoordinator
from app.application.panel_protocols import (
  DownloaderCoordinatorProtocol,
  FilesCoordinatorProtocol,
  LiveCoordinatorProtocol,
  MainWindowPanelsHostProtocol,
)
from app.application.workers.runtime_state_worker import RuntimeStateWorker
from app.application.workers.worker_runner import WorkerRunner
from app.core.config.config import AppConfig
from app.core.domain.entities import SettingsSnapshot
from app.core.domain.state import AppRuntimeState
from app.core.runtime.bootstrap import build_startup_labels
from app.features.models.switch_policy import rollback_payload
from app.runtime.manager import EngineManager


class AppCoordinator(QtCore.QObject):
  """Root controller object that groups panel coordinators and runtime reload flow."""

  def __init__(self, parent: QtCore.QObject | None = None) -> None:
    super().__init__(parent)

    self._engines = EngineManager()
    self.runtime = RuntimeCoordinator(self._engines, self)

    self._files = FilesCoordinator(self._engines, self)
    self._live = LiveCoordinator(self._engines, self)
    self._downloader = DownloaderCoordinator(self)
    self._settings = SettingsCoordinator(self)
    assert isinstance(self._files, FilesCoordinatorProtocol)
    assert isinstance(self._live, LiveCoordinatorProtocol)
    assert isinstance(self._downloader, DownloaderCoordinatorProtocol)
    self._files_api: FilesCoordinatorProtocol = self._files
    self._live_api: LiveCoordinatorProtocol = self._live
    self._downloader_api: DownloaderCoordinatorProtocol = self._downloader

    self.main_window: MainWindowPanelsHostProtocol | None = None
    self._runtime_state = AppRuntimeState()
    self._pending_runtime_snapshot: SettingsSnapshot | None = None
    self._reload_previous_state: AppRuntimeState | None = None
    self._shutting_down = False

    self.runtime.busy_changed.connect(self._on_runtime_activity_changed)
    self._files.busy_changed.connect(self._on_runtime_activity_changed)
    self._live.busy_changed.connect(self._on_runtime_activity_changed)
    self._settings.settings_applied.connect(self._on_settings_applied)
    self._settings.busy_changed.connect(self._on_runtime_activity_changed)
    self.runtime.busy_changed.connect(self._on_runtime_busy_changed)

  def _on_runtime_busy_changed(self, busy: bool) -> None:
    if self.main_window is not None and not self._shutting_down:
      self.main_window.set_runtime_busy(busy)

  @property
  def files(self) -> FilesCoordinatorProtocol:
    return self._files_api

  @property
  def live(self) -> LiveCoordinatorProtocol:
    return self._live_api

  @property
  def downloader(self) -> DownloaderCoordinatorProtocol:
    return self._downloader_api

  @property
  def settings(self) -> SettingsCoordinator:
    return self._settings

  def set_runtime_state(self, state: AppRuntimeState | None) -> None:
    self._runtime_state = state if state is not None else AppRuntimeState()
    self._files.set_runtime_state(self._runtime_state)
    self._live.set_runtime_state(self._runtime_state)

  def shutdown(self) -> None:
    self._shutting_down = True
    self._pending_runtime_snapshot = None
    for runner in self.findChildren(WorkerRunner):
      runner.cancel()
    self._engines.close()

  def has_active_work(self) -> bool:
    return any(runner.is_running() for runner in self.findChildren(WorkerRunner))

  def _on_settings_applied(self, snapshot: SettingsSnapshot) -> None:
    if self._shutting_down:
      return
    window = self.main_window
    if window is not None:
      files_panel = window.files_panel
      if files_panel is not None:
        files_panel.refresh_defaults_from_settings()
      live_panel = window.live_panel
      if live_panel is not None:
        live_panel.refresh_defaults_from_settings()

    previous_snapshot = self._runtime_state.settings_snapshot
    if not EngineManager.settings_require_reload(previous_snapshot, snapshot):
      AppConfig.SETTINGS = snapshot
      self._pending_runtime_snapshot = None
      self.set_runtime_state(replace(self._runtime_state, settings_snapshot=snapshot))
      return

    self._pending_runtime_snapshot = snapshot
    self._maybe_start_pending_runtime_reload()

  def _on_runtime_activity_changed(self, busy: bool) -> None:
    if not busy:
      self._maybe_start_pending_runtime_reload()

  def _can_start_runtime_reload(self) -> bool:
    if self.runtime.is_busy() or self._settings.is_busy():
      return False
    if self._files.is_transcribing():
      return False
    if self._live.is_running():
      return False
    return True

  def _maybe_start_pending_runtime_reload(self) -> None:
    if self._shutting_down:
      return
    snapshot = self._pending_runtime_snapshot
    if snapshot is None or not self._can_start_runtime_reload():
      return

    def _connect(worker: RuntimeStateWorker) -> None:
      worker.failed.connect(self._on_runtime_reload_failed)
      worker.failed.connect(self._settings.failed)
      worker.ready.connect(self._on_runtime_reload_ready)
      if self.main_window is not None:
        worker.status.connect(self.main_window.set_runtime_status)

    self._reload_previous_state = self._runtime_state
    self._engines.shutdown()
    self._pending_runtime_snapshot = None
    scheduled = self.runtime.start(snapshot, labels=build_startup_labels(), connect=_connect)
    if scheduled is None:
      self._pending_runtime_snapshot = snapshot

  def _on_runtime_reload_failed(self, _key: str, _params: dict[str, object]) -> None:
    self._maybe_start_pending_runtime_reload()

  def _on_runtime_reload_ready(self, state: AppRuntimeState) -> None:
    payload = rollback_payload(self._reload_previous_state, state)
    self._reload_previous_state = None
    self.set_runtime_state(state)
    if payload is not None and self._settings.restore_runtime(payload) is not None:
      details = next(
        (
          role.error_params.get('detail', role.error_key)
          for role in (state.transcription, state.translation)
          if role.error_key
        ),
        '',
      )
      self._settings.failed.emit('error.model.switch_failed', {'detail': str(details)})
      return
    if state.settings_snapshot is not None:
      self._settings.settings_loaded.emit(state.settings_snapshot)
    if self.main_window is not None:
      for panel in (self.main_window.files_panel, self.main_window.live_panel):
        if panel is not None:
          panel.refresh_defaults_from_settings()

  def bind_main_window(self, window: MainWindowPanelsHostProtocol) -> None:
    self.main_window = window

    if window.files_panel is not None:
      window.files_panel.bind_coordinator(self.files)
      self._files.bind_view(window.files_panel)

    if window.live_panel is not None:
      window.live_panel.bind_coordinator(self.live)
      self._live.bind_view(window.live_panel)

    if window.downloader_panel is not None:
      window.downloader_panel.bind_coordinator(self.downloader)
      self._downloader.bind_view(window.downloader_panel)

    if window.settings_panel is not None:
      window.settings_panel.bind_coordinator(self.settings)
      self._settings.bind_view(window.settings_panel)
      self._settings.load()
