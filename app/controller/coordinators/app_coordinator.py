# app/controller/coordinators/app_coordinator.py
from __future__ import annotations

from PyQt5 import QtCore

from app.controller.coordinators.downloader_coordinator import DownloaderCoordinator
from app.controller.coordinators.files_coordinator import FilesCoordinator
from app.controller.coordinators.live_coordinator import LiveCoordinator
from app.controller.coordinators.settings_coordinator import SettingsCoordinator
from app.controller.coordinators.startup_coordinator import StartupCoordinator
from app.controller.panel_protocols import (
    DownloaderCoordinatorProtocol,
    FilesCoordinatorProtocol,
    LiveCoordinatorProtocol,
    MainWindowPanelsHostProtocol,
    SettingsCoordinatorProtocol,
)
from app.model.core.domain.state import AppRuntimeState


class AppCoordinator(QtCore.QObject):
    """Root controller object that groups panel coordinators and global busy state."""

    section_busy_changed = QtCore.pyqtSignal(str, bool)
    global_busy_changed = QtCore.pyqtSignal(bool)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)

        self.startup = StartupCoordinator(self)

        self._files = FilesCoordinator(self)
        self._live = LiveCoordinator(self)
        self._downloader = DownloaderCoordinator(self)
        self._settings = SettingsCoordinator(self)

        self.main_window: MainWindowPanelsHostProtocol | None = None
        self._busy_sections: set[str] = set()
        self._runtime_state = AppRuntimeState()

        self.startup.busy_changed.connect(lambda busy: self._set_section_busy("startup", busy))
        self._files.busy_changed.connect(lambda busy: self._set_section_busy("files", busy))
        self._live.busy_changed.connect(lambda busy: self._set_section_busy("live", busy))
        self._downloader.busy_changed.connect(lambda busy: self._set_section_busy("downloader", busy))
        self._settings.busy_changed.connect(lambda busy: self._set_section_busy("settings", busy))
        self._settings.settings_applied.connect(self._on_settings_applied)

    @property
    def files(self) -> FilesCoordinatorProtocol:
        coordinator = self._files
        assert isinstance(coordinator, FilesCoordinatorProtocol)
        return coordinator

    @property
    def live(self) -> LiveCoordinatorProtocol:
        coordinator = self._live
        assert isinstance(coordinator, LiveCoordinatorProtocol)
        return coordinator

    @property
    def downloader(self) -> DownloaderCoordinatorProtocol:
        coordinator = self._downloader
        assert isinstance(coordinator, DownloaderCoordinatorProtocol)
        return coordinator

    @property
    def settings(self) -> SettingsCoordinatorProtocol:
        coordinator = self._settings
        assert isinstance(coordinator, SettingsCoordinatorProtocol)
        return coordinator

    def set_runtime_state(self, state: AppRuntimeState | None) -> None:
        self._runtime_state = state if state is not None else AppRuntimeState()
        self._files.set_runtime_state(self._runtime_state)
        self._live.set_runtime_state(self._runtime_state)

    def is_busy(self) -> bool:
        return bool(self._busy_sections)

    def _set_section_busy(self, section: str, busy: bool) -> None:
        key = str(section or "").strip().lower()
        if not key:
            return

        before = bool(self._busy_sections)

        if busy:
            self._busy_sections.add(key)
        else:
            self._busy_sections.discard(key)

        after = bool(self._busy_sections)
        self.section_busy_changed.emit(key, bool(busy))
        if after != before:
            self.global_busy_changed.emit(after)

    def _on_settings_applied(self) -> None:
        window = self.main_window
        if window is None:
            return
        files_panel = window.files_panel
        if files_panel is not None:
            files_panel.refresh_defaults_from_settings()
        live_panel = window.live_panel
        if live_panel is not None:
            live_panel.refresh_defaults_from_settings()

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
