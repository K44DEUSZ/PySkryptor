# view/views/main_window.py
from __future__ import annotations

from typing import Optional

from PyQt5 import QtWidgets

from view.utils.translating import tr
from view.views.files_panel import FilesPanel
from view.views.live_panel import LivePanel
from view.views.downloader_panel import DownloaderPanel
from view.views.settings_panel import SettingsPanel
from view.views.about_panel import AboutPanel


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self.setObjectName("MainWindow")
        self.setWindowTitle(tr("app.title"))
        self.resize(1280, 820)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("MainTabs")
        root.addWidget(self.tabs)

        self.files_panel = FilesPanel(self)
        self.live_panel = LivePanel(self)
        self.down_panel = DownloaderPanel(self)
        self.settings_panel = SettingsPanel(self)
        self.about_panel = AboutPanel(self)

        self.tabs.addTab(self.files_panel, tr("tabs.files"))
        self.tabs.addTab(self.live_panel, tr("tabs.live"))
        self.tabs.addTab(self.down_panel, tr("tabs.downloader"))
        self.tabs.addTab(self.settings_panel, tr("tabs.settings"))
        self.tabs.addTab(self.about_panel, tr("tabs.about"))

    def closeEvent(self, e) -> None:
        try:
            if hasattr(self.files_panel, "on_parent_close"):
                self.files_panel.on_parent_close()
        except Exception:
            pass

        try:
            if hasattr(self.live_panel, "on_parent_close"):
                self.live_panel.on_parent_close()
        except Exception:
            pass

        try:
            if hasattr(self.down_panel, "on_parent_close"):
                self.down_panel.on_parent_close()
        except Exception:
            pass

        super().closeEvent(e)
