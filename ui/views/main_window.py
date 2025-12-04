# ui/views/main_window.py
from __future__ import annotations

from typing import Optional

from PyQt5 import QtWidgets

from ui.utils.translating import tr
from ui.views.files_panel import FilesPanel
from ui.views.downloader_panel import DownloaderPanel
from ui.views.settings_panel import SettingsPanel
from ui.views.about_panel import AboutPanel


class MainWindow(QtWidgets.QMainWindow):
    """
    Thin shell: tab widget + panels.
    All functional logic lives inside individual panels.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("app.title"))
        self.resize(1280, 820)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)

        # Files panel
        self.files_panel = FilesPanel(self)
        self.tabs.addTab(self.files_panel, tr("tabs.files"))

        # Live tab – still a placeholder page for now
        self.live_page = self._make_placeholder(tr("tabs.live"))
        self.tabs.addTab(self.live_page, tr("tabs.live"))

        # Downloader panel
        self.downloader_panel = DownloaderPanel(self)
        self.tabs.addTab(self.downloader_panel, tr("tabs.downloader"))

        # Settings panel
        self.settings_panel = SettingsPanel(self)
        self.tabs.addTab(self.settings_panel, tr("tabs.settings"))

        # About / credits panel
        self.about_panel = AboutPanel(self)
        self.tabs.addTab(self.about_panel, tr("tabs.about"))

    # ----- Helpers -----

    def _make_placeholder(self, title: str) -> QtWidgets.QWidget:
        """
        Simple placeholder page (currently used for Live tab).
        All text is i18n-driven.
        """
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lbl = QtWidgets.QLabel(title + " — " + tr("ui.placeholder.soon"))
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        lay.addStretch(1)
        return page

    # ----- Close handling -----

    def closeEvent(self, e) -> None:
        """
        Allow panels to perform best-effort cleanup when the main window closes.
        """
        for pnl in (self.files_panel, self.downloader_panel, self.settings_panel, self.about_panel):
            try:
                if hasattr(pnl, "on_parent_close"):
                    pnl.on_parent_close()
            except Exception:
                pass
        super().closeEvent(e)
