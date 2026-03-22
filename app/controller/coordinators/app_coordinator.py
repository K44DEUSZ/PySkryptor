# app/controller/coordinators/app_coordinator.py
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from app.controller.coordinators.downloader_coordinator import DownloaderCoordinator
from app.controller.coordinators.files_coordinator import FilesCoordinator
from app.controller.coordinators.live_coordinator import LiveCoordinator
from app.controller.coordinators.settings_coordinator import SettingsCoordinator
from app.controller.coordinators.startup_coordinator import StartupCoordinator
from app.controller.contracts import MainWindowPanelsHostProtocol
from app.model.domain.runtime_state import AppRuntimeState


class AppCoordinator(QtCore.QObject):
    """Root controller object that groups panel coordinators and global busy state."""

    section_busy_changed = QtCore.pyqtSignal(str, bool)
    global_busy_changed = QtCore.pyqtSignal(bool)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)

        self.startup = StartupCoordinator(self)
        self.files = FilesCoordinator(self)
        self.live = LiveCoordinator(self)
        self.downloader = DownloaderCoordinator(self)
        self.settings = SettingsCoordinator(self)

        self.main_window: QtWidgets.QMainWindow | None = None
        self._busy_sections: set[str] = set()
        self._runtime_state = AppRuntimeState()

        self.startup.busy_changed.connect(lambda busy: self._set_section_busy("startup", busy))
        self.files.busy_changed.connect(lambda busy: self._set_section_busy("files", busy))
        self.live.busy_changed.connect(lambda busy: self._set_section_busy("live", busy))
        self.downloader.busy_changed.connect(lambda busy: self._set_section_busy("downloader", busy))
        self.settings.busy_changed.connect(lambda busy: self._set_section_busy("settings", busy))

    def set_runtime_state(self, state: AppRuntimeState | None) -> None:
        self._runtime_state = state if state is not None else AppRuntimeState()
        self.files.set_runtime_state(self._runtime_state)
        self.live.set_runtime_state(self._runtime_state)

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

    def bind_main_window(self, window: MainWindowPanelsHostProtocol) -> None:
        if isinstance(window, QtWidgets.QMainWindow):
            self.main_window = window

        if window.files_panel is not None:
            window.files_panel.bind_coordinator(self.files)
            self.files.bind_view(window.files_panel)

        if window.live_panel is not None:
            window.live_panel.bind_coordinator(self.live)
            self.live.bind_view(window.live_panel)

        if window.downloader_panel is not None:
            window.downloader_panel.bind_coordinator(self.downloader)
            self.downloader.bind_view(window.downloader_panel)

        if window.settings_panel is not None:
            window.settings_panel.bind_coordinator(self.settings)
            self.settings.bind_view(window.settings_panel)
            self.settings.load()
