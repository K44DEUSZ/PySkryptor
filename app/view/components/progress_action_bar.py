# app/view/components/progress_action_bar.py
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from app.view.support.widget_effects import sync_progress_text_role
from app.view.support.widget_setup import build_layout_host, setup_button, setup_layout
from app.view.ui_config import ui

# ----- Progress action bar -----
class ProgressActionBar(QtWidgets.QWidget):
    """Reusable row: progress bar + two action buttons."""

    primary_clicked = QtCore.pyqtSignal()
    secondary_clicked = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        primary_text: str,
        secondary_text: str,
        height: int | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        cfg = ui(self)
        h = int(height or cfg.control_min_h)

        row = QtWidgets.QHBoxLayout(self)
        setup_layout(row, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMinimumHeight(h)

        self.btn_primary = QtWidgets.QPushButton(primary_text)
        self.btn_secondary = QtWidgets.QPushButton(secondary_text)
        setup_button(self.btn_primary, min_h=h, min_w=cfg.control_min_w)
        setup_button(self.btn_secondary, min_h=h, min_w=cfg.control_min_w)

        for b in (self.btn_primary, self.btn_secondary):
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        for w in (self.btn_primary, self.btn_secondary, self.progress):
            w.ensurePolished()
        h_eff = max(
            h,
            int(self.btn_primary.sizeHint().height()),
            int(self.btn_secondary.sizeHint().height()),
            int(self.progress.sizeHint().height()),
        )
        self.progress.setFixedHeight(h_eff)
        self.btn_primary.setFixedHeight(h_eff)
        self.btn_secondary.setFixedHeight(h_eff)

        btn_box_host, btn_box = build_layout_host(
            layout="hbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.spacing,
        )
        btn_box.addWidget(self.btn_primary, 1)
        btn_box.addWidget(self.btn_secondary, 1)

        row.addWidget(self.progress, 3)
        row.addWidget(btn_box_host, 1)

        self.btn_primary.clicked.connect(self.primary_clicked)
        self.btn_secondary.clicked.connect(self.secondary_clicked)

        self._target_value = 0
        self._anim_timer = QtCore.QTimer(self)
        self._anim_timer.setInterval(int(cfg.progress_anim_interval_ms))
        self._anim_timer.timeout.connect(self._tick_progress)
        self._sync_progress_text_role()

    # ----- Progress -----

    def _tick_progress(self) -> None:
        cur = int(self.progress.value())
        target = int(self._target_value)

        if cur == target:
            self._anim_timer.stop()
            return

        if cur > target:
            self.progress.setValue(target)
            self._anim_timer.stop()
            return

        delta = target - cur
        cfg = ui(self)
        if delta < int(cfg.progress_anim_small_delta_threshold):
            step = 1
        else:
            step = max(1, int(delta / max(1, int(cfg.progress_anim_divisor))))
        self.progress.setValue(min(target, cur + step))
        self._sync_progress_text_role()

    def _sync_progress_text_role(self) -> None:
        sync_progress_text_role(self.progress)

    def set_progress(self, value: int) -> None:
        try:
            v = max(0, min(100, int(value)))
        except Exception:
            v = 0

        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        self._target_value = v

        cur = int(self.progress.value())
        if v <= cur:
            self.progress.setValue(v)
            self._anim_timer.stop()
            self._sync_progress_text_role()
            return

        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def reset(self) -> None:
        self._target_value = 0
        self.progress.setValue(0)
        self._anim_timer.stop()
        self._sync_progress_text_role()

    def set_busy(self, busy: bool) -> None:
        if busy:
            self._anim_timer.stop()
            self._target_value = 0
            self.progress.setRange(0, 0)
            self.progress.setValue(0)
            self._sync_progress_text_role()
            return

        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(self._target_value))
        self._sync_progress_text_role()

# ----- Buttons -----

    def set_primary_enabled(self, enabled: bool) -> None:
        self.btn_primary.setEnabled(bool(enabled))

    def set_secondary_enabled(self, enabled: bool) -> None:
        self.btn_secondary.setEnabled(bool(enabled))
