# view/views/about_panel.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from model.config.app_config import AppConfig as Config
from view.utils.translating import tr


class AboutPanel(QtWidgets.QWidget):
    """About view with app metadata, logo placeholder and local license link."""

    _LOGO_SIZE: int = 220

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("AboutPanel")

        self._license_browser: Optional[QtWidgets.QTextBrowser] = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(24)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        logo_path = self._resolve_logo_svg_path()
        left_layout.addWidget(
            self._build_logo_widget(logo_path),
            0,
            QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter,
        )
        left_layout.addStretch(1)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)

        app_group = QtWidgets.QGroupBox(tr("about.section.app"))
        app_layout = QtWidgets.QVBoxLayout(app_group)

        app_label = QtWidgets.QLabel()
        app_label.setTextFormat(QtCore.Qt.RichText)
        app_label.setWordWrap(True)
        app_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        app_label.setText(
            tr("about.app.description").format(
                name=Config.APP_NAME,
                version=Config.APP_VERSION,
                author=Config.APP_AUTHOR,
                years=getattr(Config, "APP_DEVELOPMENT_YEARS", ""),
            )
        )
        app_layout.addWidget(app_label)
        right_layout.addWidget(app_group)

        license_group = QtWidgets.QGroupBox(tr("about.section.license"))
        license_layout = QtWidgets.QVBoxLayout(license_group)

        summary_label = QtWidgets.QLabel()
        summary_label.setWordWrap(True)
        summary_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        summary_label.setText(tr("about.license.summary"))
        license_layout.addWidget(summary_label)

        browser = QtWidgets.QTextBrowser()
        browser.setReadOnly(True)
        browser.setFrameShape(QtWidgets.QFrame.NoFrame)
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        browser.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        browser.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        browser.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        browser.document().setDocumentMargin(0)
        browser.setHtml(tr("about.license.full_link"))
        browser.anchorClicked.connect(self._on_anchor_clicked)

        self._license_browser = browser
        license_layout.addWidget(browser)

        right_layout.addWidget(license_group)
        right_layout.addStretch(1)

        layout.addWidget(left)
        layout.addWidget(right)
        layout.setStretch(0, 1)
        layout.setStretch(1, 2)

        self._update_license_browser_height()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_license_browser_height()

    def _update_license_browser_height(self) -> None:
        if not self._license_browser:
            return
        doc = self._license_browser.document()
        doc.setTextWidth(self._license_browser.viewport().width())
        height = int(doc.size().height()) + 6
        if height < 24:
            height = 24
        self._license_browser.setFixedHeight(height)

    @staticmethod
    def _resolve_logo_svg_path() -> Path:
        return getattr(Config, "IMAGES_DIR", Path("assets") / "images") / "logo.svg"

    def _build_logo_widget(self, logo_path: Path) -> QtWidgets.QWidget:
        if logo_path.exists():
            try:
                from PyQt5 import QtSvg  # type: ignore

                svg = QtSvg.QSvgWidget(str(logo_path))
                svg.setFixedSize(self._LOGO_SIZE, self._LOGO_SIZE)
                return svg
            except Exception:
                pass

        placeholder = QtWidgets.QLabel("LOGO")
        placeholder.setAlignment(QtCore.Qt.AlignCenter)
        placeholder.setFixedSize(self._LOGO_SIZE, self._LOGO_SIZE)
        placeholder.setObjectName("AboutLogoPlaceholder")
        placeholder.setStyleSheet(
            "QLabel#AboutLogoPlaceholder{border:1px dashed rgba(255,255,255,0.25); border-radius:12px;}"
        )
        return placeholder

    def _on_anchor_clicked(self, url: QtCore.QUrl) -> None:
        if url.toString().strip().lower() != "license":
            return
        self._open_local_file(Config.LICENSE_FILE)

    @staticmethod
    def _open_local_file(path: Path) -> None:
        try:
            qurl = QtCore.QUrl.fromLocalFile(str(path))
            if qurl.isValid():
                QtGui.QDesktopServices.openUrl(qurl)
        except Exception:
            pass

    def on_parent_close(self) -> None:
        pass
