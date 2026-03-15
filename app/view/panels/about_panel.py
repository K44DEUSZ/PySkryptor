# app/view/panels/about_panel.py
from __future__ import annotations

import html
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets
from app.view.ui_config import LogoSvgLabel, enable_styled_background, logo_svg_path, open_local_path, setup_layout, ui
from app.view import dialogs
from app.view.components.section_group import SectionGroup

from app.model.config.app_config import AppConfig as Config
from app.controller.support.localization import tr

_LOG = logging.getLogger(__name__)

class AboutPanel(QtWidgets.QWidget):
    """About view with app metadata, scalable logo and local license link."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("AboutPanel")
        self.setProperty("uiRole", "page")
        enable_styled_background(self)
        self._ui = ui(self)
        self._license_browser: Optional[QtWidgets.QTextBrowser] = None
        self._logo: Optional[LogoSvgLabel] = None
        self._left: Optional[QtWidgets.QWidget] = None
        self._build_ui()
        self._wire_signals()
        self._restore_initial_state()

    # ----- Build -----

    def _build_ui(self) -> None:
        cfg = self._ui

        layout = QtWidgets.QHBoxLayout(self)
        setup_layout(layout, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)

        left = QtWidgets.QWidget()
        left.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        left_layout = QtWidgets.QVBoxLayout(left)
        setup_layout(left_layout, cfg=cfg, margins=(cfg.margin * 2, cfg.margin * 2, cfg.margin, cfg.margin), spacing=cfg.spacing)

        path = logo_svg_path()
        if path is not None:
            logo = LogoSvgLabel(path, object_name="AboutLogo")
            self._logo = logo
            left_layout.addWidget(logo, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
            left_layout.addStretch(1)
        else:
            placeholder = QtWidgets.QLabel(tr("about.logo.placeholder"))
            placeholder.setObjectName("AboutLogoPlaceholder")
            placeholder.setAlignment(QtCore.Qt.AlignCenter)
            placeholder.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            left_layout.addWidget(placeholder)
            left_layout.addStretch(1)

        right = QtWidgets.QWidget()
        right.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        right.setMinimumWidth(int(cfg.control_min_w * 3))
        right_layout = QtWidgets.QVBoxLayout(right)
        setup_layout(right_layout, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)

        app_group = SectionGroup(self, object_name="AboutAppGroup")
        app_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        app_layout = app_group.root
        setup_layout(app_layout, cfg=cfg, margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin), spacing=cfg.spacing)

        app_label = QtWidgets.QLabel()
        app_label.setTextFormat(QtCore.Qt.RichText)
        app_label.setWordWrap(True)
        app_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        app_html = tr("about.app.description").format(
            name=Config.APP_NAME,
            version=Config.APP_VERSION,
            author=Config.APP_AUTHOR,
            years=Config.APP_DEVELOPMENT_YEARS,
        )
        app_label.setText(f'<div style="line-height:1.35">{app_html}</div>')
        app_layout.addWidget(app_label)
        right_layout.addWidget(app_group)

        license_group = SectionGroup(self, object_name="AboutLicenseGroup")
        license_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        license_layout = license_group.root
        setup_layout(license_layout, cfg=cfg, margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin), spacing=cfg.spacing)

        summary = html.escape(tr("about.license.summary")).replace("\n", "<br>")
        summary_label = QtWidgets.QLabel(f'<div style="line-height:1.35">{summary}</div>')
        summary_label.setTextFormat(QtCore.Qt.RichText)
        summary_label.setWordWrap(True)
        summary_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        license_layout.addWidget(summary_label)

        browser = QtWidgets.QTextBrowser()
        browser.setReadOnly(True)
        browser.setFrameShape(QtWidgets.QFrame.NoFrame)
        browser.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        browser.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        browser.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        browser.setObjectName("AboutLicenseLink")
        browser.document().setDocumentMargin(0)
        browser.setHtml(tr("about.license.full_link"))

        self._license_browser = browser
        license_layout.addWidget(browser)
        right_layout.addWidget(license_group)
        right_layout.addStretch(1)

        layout.addWidget(left)
        layout.addWidget(right)
        layout.setStretch(0, 0)
        layout.setStretch(1, 1)

        self._left = left

    # ----- Wiring -----

    def _wire_signals(self) -> None:
        if self._license_browser is not None:
            self._license_browser.anchorClicked.connect(self._on_anchor_clicked)

    # ----- Restore / bootstrap -----

    def _restore_initial_state(self) -> None:
        QtCore.QTimer.singleShot(0, self._tune_license_link_height)
        self._update_logo_geometry()

    # ----- Lifecycle -----

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        self._update_logo_geometry()
        self._tune_license_link_height()

    def _tune_license_link_height(self) -> None:
        b = self._license_browser
        if b is None:
            return
        try:
            b.document().setTextWidth(float(b.viewport().width()))
        except Exception:
            pass

        try:
            h = float(b.document().size().height())
        except Exception:
            h = 0.0

        fm = b.fontMetrics()
        pad = int(self._ui.about_text_browser_height_pad)
        need = int(max(float(fm.height()), h) + float(pad))
        b.setFixedHeight(max(1, need))

    def _update_logo_geometry(self) -> None:
        if self._logo is None or self._left is None:
            return

        cfg = self._ui
        max_w = max(min(int(self.width() * cfg.about_logo_max_w_ratio), int(cfg.about_logo_max_w_cap)), int(cfg.about_logo_max_w_floor))
        max_h = max(min(int(self.height() * cfg.about_logo_max_h_ratio), int(cfg.about_logo_max_h_cap)), int(cfg.about_logo_max_h_floor))

        self._logo.update_for_bounds(max_w, max_h)
        self._left.setMaximumWidth(min(int(self.width() * cfg.about_left_panel_max_w_ratio), self._logo.width() + cfg.margin * 4))

    def _on_anchor_clicked(self, url: QtCore.QUrl) -> None:
        del url
        self._open_license_file()

    @staticmethod
    def _resolve_license_path() -> Optional[Path]:
        path = Path(Config.LICENSE_FILE)
        if path.exists():
            return path
        return None

    def _open_license_file(self) -> None:
        path = self._resolve_license_path()
        if path is None:
            dialogs.show_error(
                self,
                title=tr("dialog.error.title"),
                header=tr("about.section.license"),
                message=tr("about.license.missing"),
            )
            return

        try:
            if self._open_local_file(path):
                return
            _LOG.error("Opening the license file with the system handler failed. path=%s", path)
        except Exception:
            _LOG.exception("Opening the license file failed. path=%s", path)

        dialogs.show_error(
            self,
            title=tr("dialog.error.title"),
            header=tr("about.section.license"),
            message=tr("about.license.open_failed"),
        )

    @staticmethod
    def _open_local_file(path: Optional[Path]) -> bool:
        if path is None:
            return False

        target = Path(path).resolve()
        if os.name != "nt" or target.suffix:
            return open_local_path(target)
        return open_local_path(target) or AboutPanel._open_extensionless_text_file(target)

    @staticmethod
    def _open_extensionless_text_file(path: Path) -> bool:
        try:
            subprocess.Popen(["notepad.exe", str(path)])
            return True
        except Exception:
            _LOG.exception("Opening the extensionless text file in Notepad failed. path=%s", path)
            return False

    def on_parent_close(self) -> None:
        pass
