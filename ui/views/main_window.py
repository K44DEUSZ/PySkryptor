# ui/views/main_window.py
from __future__ import annotations

from typing import Optional
from PyQt5 import QtWidgets

from ui.utils.translating import tr
from ui.views.files_panel import FilesPanel
from ui.views.downloader_panel import DownloaderPanel


class MainWindow(QtWidgets.QMainWindow):
    """Thin shell: radio tabs + stacked panels. Logic lives inside panels."""
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("app.title"))
        self.resize(1280, 820)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Tabs (radio buttons)
        tabs_box = QtWidgets.QGroupBox(tr("tabs.group"))
        tabs_layout = QtWidgets.QHBoxLayout(tabs_box)
        self.rb_files = QtWidgets.QRadioButton(tr("tabs.files"))
        self.rb_down = QtWidgets.QRadioButton(tr("tabs.downloader"))
        self.rb_live = QtWidgets.QRadioButton(tr("tabs.live"))
        self.rb_settings = QtWidgets.QRadioButton(tr("tabs.settings"))
        self.rb_files.setChecked(True)
        for rb in (self.rb_files, self.rb_down, self.rb_live, self.rb_settings):
            tabs_layout.addWidget(rb)
        tabs_layout.addStretch(1)
        layout.addWidget(tabs_box)

        # Stacked panels
        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack, 1)

        # Files
        self.files_panel = FilesPanel(self)
        self.stack.addWidget(self.files_panel)

        # Downloader
        self.downloader_panel = DownloaderPanel(self)
        self.stack.addWidget(self.downloader_panel)

        # Placeholders
        self.stack.addWidget(self._make_placeholder(tr("tabs.live")))
        self.stack.addWidget(self._make_placeholder(tr("tabs.settings")))

        # Switch handlers
        self.rb_files.toggled.connect(lambda: self.stack.setCurrentIndex(0))
        self.rb_down.toggled.connect(lambda: self.stack.setCurrentIndex(1))
        self.rb_live.toggled.connect(lambda: self.stack.setCurrentIndex(2))
        self.rb_settings.toggled.connect(lambda: self.stack.setCurrentIndex(3))

    def _make_placeholder(self, title: str) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lbl = QtWidgets.QLabel(title + " — " + tr("ui.placeholder.soon"))
        lay.addWidget(lbl)
        lay.addStretch(1)
        return page

    def closeEvent(self, e):
        """Allow panels to perform best-effort cleanup."""
        for pnl in (self.files_panel, self.downloader_panel):
            try:
                pnl.on_parent_close()
            except Exception:
                pass
        super().closeEvent(e)
