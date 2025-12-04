# ui/views/about_panel.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from ui.utils.translating import tr


class AboutPanel(QtWidgets.QWidget):
    """'About' tab: basic app metadata and license link."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

    # ----- UI -----

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # App info group
        app_group = QtWidgets.QGroupBox(tr("about.section.app"))
        app_layout = QtWidgets.QVBoxLayout(app_group)

        app_label = QtWidgets.QLabel()
        app_label.setTextFormat(QtCore.Qt.RichText)
        app_label.setWordWrap(True)
        app_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        template = tr("about.app.description")
        app_label.setText(
            template.format(
                name=Config.APP_NAME,
                name_lower=Config.APP_NAME.lower(),
                version=Config.APP_VERSION,
                author=Config.APP_AUTHOR,
                years=Config.APP_COPYRIGHT_RANGE,
            )
        )
        app_layout.addWidget(app_label)
        layout.addWidget(app_group)

        # License group
        license_group = QtWidgets.QGroupBox(tr("about.section.license"))
        license_layout = QtWidgets.QVBoxLayout(license_group)

        summary_label = QtWidgets.QLabel()
        summary_label.setWordWrap(True)
        summary_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        summary_label.setText(tr("about.license.summary"))
        license_layout.addWidget(summary_label)

        link_label = QtWidgets.QLabel()
        link_label.setTextFormat(QtCore.Qt.RichText)
        link_label.setWordWrap(True)
        link_label.setOpenExternalLinks(False)
        link_label.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        # Text comes from i18n and should contain a single hyperlink, e.g.:
        # <a href="license">LICENSE</a>
        link_label.setText(tr("about.license.full_link"))
        link_label.linkActivated.connect(self._on_license_link_activated)
        license_layout.addWidget(link_label)

        layout.addWidget(license_group)
        layout.addStretch(1)

    # ----- Actions -----

    def _on_license_link_activated(self, _: str) -> None:
        """Open the local license file using the path from AppConfig."""
        try:
            path = Config.license_file_path()
        except Exception:
            # If AppConfig fails to resolve the path, do nothing.
            return
        self._open_path(path)

    @staticmethod
    def _open_path(path: Path) -> None:
        """Best-effort attempt to open a local file in the system file browser."""
        try:
            url = QtCore.QUrl.fromLocalFile(str(path))
            if url.isValid():
                QtGui.QDesktopServices.openUrl(url)
        except Exception:
            pass

    # ----- Cleanup hook for MainWindow -----

    def on_parent_close(self) -> None:
        """No background work here; provided for API symmetry."""
        pass
