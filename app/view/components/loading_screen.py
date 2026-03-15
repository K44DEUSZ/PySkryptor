# app/view/components/loading_screen.py
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets
from app.view.ui_config import (
    LogoSvgLabel,
    apply_floating_shadow,
    enable_styled_background,
    floating_shadow_margins,
    logo_svg_path,
    sync_progress_text_role,
    ui,
)

from app.controller.support.localization import tr
from app.model.config.app_config import AppConfig as Config

class LoadingScreenWidget(QtWidgets.QWidget):
    """Splash-like loading screen shown during app startup."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        cfg = ui(self)
        enable_styled_background(self)

        self.setObjectName("LoadingScreen")
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setFixedSize(cfg.loading_min_w, cfg.loading_min_h)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(QtCore.Qt.WindowMinimizeButtonHint, False)
        self.setWindowFlag(QtCore.Qt.WindowMaximizeButtonHint, False)
        self.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, False)
        self._allow_close = False

        path = logo_svg_path()
        if path is not None:
            brand: QtWidgets.QWidget = LogoSvgLabel(path, object_name="LoadingLogo")
            brand.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        else:
            title = QtWidgets.QLabel(Config.APP_NAME)
            title.setObjectName("LoadingTitle")
            title.setAlignment(QtCore.Qt.AlignHCenter)
            brand = title

        self._brand = brand

        self._status = QtWidgets.QLabel(self._normalize_status_text(tr("loading.stage.start")))
        self._status.setObjectName("LoadingStatus")
        self._status.setWordWrap(False)
        self._status.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._status.setFixedHeight(max(self._status.fontMetrics().lineSpacing() + cfg.spacing + 6, 30))

        self._progress = QtWidgets.QProgressBar()
        self._progress.setObjectName("LoadingProgress")
        self._progress.setRange(0, 0)
        self._sync_progress_text_role()

        self._card = QtWidgets.QFrame()
        self._card.setObjectName("LoadingScreenCard")
        self._card.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        enable_styled_background(self._card)

        apply_floating_shadow(self._card)

        card_layout = QtWidgets.QVBoxLayout(self._card)
        card_layout.setContentsMargins(cfg.margin * 3, cfg.margin * 2, cfg.margin * 3, cfg.margin * 2)
        card_layout.setSpacing(cfg.spacing * 2)
        card_layout.addStretch(1)
        card_layout.addWidget(self._brand, 0, QtCore.Qt.AlignHCenter)
        card_layout.addSpacing(cfg.spacing)
        card_layout.addWidget(self._progress)
        card_layout.addWidget(self._status)
        card_layout.addStretch(2)

        outer_layout = QtWidgets.QVBoxLayout(self)
        left, top, right, bottom = floating_shadow_margins(self, extra=cfg.margin)
        outer_layout.setContentsMargins(left + cfg.margin, top + cfg.margin, right + cfg.margin, bottom + cfg.margin)
        outer_layout.setSpacing(0)
        outer_layout.addWidget(self._card)

        self._update_brand_geometry()

    @staticmethod
    def _normalize_status_text(text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        while value.endswith(".") or value.endswith("\u2026"):
            value = value[:-1].rstrip()
        return f"{value}..."

    def set_status(self, text: str) -> None:
        """Update the status label."""
        self._status.setText(self._normalize_status_text(text))

    def set_progress(self, pct: int) -> None:
        """Switch to determinate mode and set progress percent."""
        pct = max(0, min(100, int(pct)))
        self._progress.setRange(0, 100)
        self._progress.setValue(pct)
        self._sync_progress_text_role()

    def set_indeterminate(self, enabled: bool) -> None:
        """Enable/disable indeterminate progress."""
        self._progress.setRange(0, 0 if enabled else 100)
        self._sync_progress_text_role()

    def _sync_progress_text_role(self) -> None:
        sync_progress_text_role(self._progress)

    def finish(self) -> None:
        self._allow_close = True
        self.close()

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if not bool(getattr(self, "_allow_close", False)):
            e.ignore()
            return
        super().closeEvent(e)

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(e)
        self._update_brand_geometry()

    def showEvent(self, e: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(e)
        try:
            self.setWindowIcon(QtWidgets.QApplication.windowIcon())
        except Exception:
            pass

    def _update_brand_geometry(self) -> None:
        brand = self._brand
        if not isinstance(brand, LogoSvgLabel):
            return
        max_w = max(min(int(self.width() * 0.74), 660), 360)
        max_h = max(min(int(self.height() * 0.34), 220), 128)
        brand.update_for_bounds(max_w, max_h)
