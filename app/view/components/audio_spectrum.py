# app/view/components/audio_spectrum.py
from __future__ import annotations

from typing import Any

from PyQt5 import QtCore, QtGui, QtWidgets

from app.view.support.theme_runtime import spectrum_palette
from app.view.ui_config import ui


def _app_instance() -> QtWidgets.QApplication | None:
    app = QtWidgets.QApplication.instance()
    return app if isinstance(app, QtWidgets.QApplication) else None


class AudioSpectrumWidget(QtWidgets.QWidget):
    """Lightweight input-audio spectrum meter."""

    STATE_IDLE = "idle"
    STATE_ACTIVE = "active"
    STATE_PAUSED = "paused"
    STATE_DISABLED = "disabled"
    STATE_ERROR = "error"

    _VALID_STATES = {STATE_IDLE, STATE_ACTIVE, STATE_PAUSED, STATE_DISABLED, STATE_ERROR}

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        cfg = ui(self)
        self._bars = max(8, int(cfg.spectrum_bar_count))
        self._target_values: list[float] = [0.0] * self._bars
        self._display_values: list[float] = [0.0] * self._bars
        self._visual_state = self.STATE_IDLE

        self._anim_timer = QtCore.QTimer(self)
        self._anim_timer.setInterval(int(cfg.spectrum_anim_interval_ms))
        self._anim_timer.timeout.connect(self._tick_animation)

        self.setMinimumHeight(cfg.spectrum_min_h)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    @QtCore.pyqtSlot(object)
    def set_spectrum(self, values: object) -> None:
        norm = self._normalize_values(values)
        target = self._resample_values(norm) if norm else [0.0] * self._bars
        if self._max_delta(target, self._target_values) < 0.02:
            return

        self._target_values = target
        if self._has_pending_animation() and not self._anim_timer.isActive():
            self._anim_timer.start()

    def clear(self) -> None:
        zero = [0.0] * self._bars
        self._target_values = zero.copy()
        self._display_values = zero.copy()
        self._anim_timer.stop()
        self.update()

    def set_visual_state(self, state: str) -> None:
        value = str(state or "").strip().lower()
        if value not in self._VALID_STATES:
            value = self.STATE_IDLE
        if value == self._visual_state:
            return
        self._visual_state = value
        self.update()

    def changeEvent(self, ev: QtCore.QEvent) -> None:
        super().changeEvent(ev)
        if ev.type() in {
            QtCore.QEvent.Type.EnabledChange,
            QtCore.QEvent.Type.PaletteChange,
            QtCore.QEvent.Type.StyleChange,
        }:
            self.update()

    def _normalize_values(self, values: Any) -> list[float]:
        try:
            items = list(values)
        except TypeError:
            return []

        out: list[float] = []
        for item in items:
            try:
                value = float(item)
            except (TypeError, ValueError):
                value = 0.0
            out.append(self._clamp_value(value))
        return out

    def _resample_values(self, values: list[float]) -> list[float]:
        if not values:
            return [0.0] * self._bars
        if len(values) == self._bars:
            return values.copy()

        out: list[float] = []
        count = len(values)
        for idx in range(self._bars):
            src_idx = min(count - 1, int(idx * count / self._bars))
            out.append(self._clamp_value(values[src_idx]))
        return out

    @staticmethod
    def _clamp_value(value: float) -> float:
        if value <= 0.0:
            return 0.0
        if value >= 1.0:
            return 1.0
        return float(value)

    @staticmethod
    def _max_delta(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            return 1.0
        try:
            return max(abs(float(a) - float(b)) for a, b in zip(left, right))
        except (TypeError, ValueError):
            return 1.0

    def _has_pending_animation(self) -> bool:
        return self._max_delta(self._target_values, self._display_values) >= 0.01

    def _tick_animation(self) -> None:
        changed = False

        for idx, target in enumerate(self._target_values):
            current = float(self._display_values[idx])
            delta = float(target) - current
            if abs(delta) < 0.01:
                value = float(target)
            elif delta > 0.0:
                value = current + min(delta, 0.20 + delta * 0.45)
            else:
                value = current + delta * 0.24

            if value < 0.001:
                value = 0.0

            value = self._clamp_value(value)
            if abs(value - current) >= 0.002:
                changed = True
            self._display_values[idx] = value

        if changed:
            self.update()

        if not self._has_pending_animation():
            self._anim_timer.stop()

    @staticmethod
    def _draw_background(
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        *,
        radius: float,
        colors: dict[str, QtGui.QColor],
    ) -> None:
        pen = QtGui.QPen(colors["border"])
        pen.setWidthF(0.8)
        painter.setPen(pen)
        painter.setBrush(colors["background"])
        painter.drawRoundedRect(rect, radius, radius)

    def _draw_bars(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        *,
        radius: float,
        colors: dict[str, QtGui.QColor],
    ) -> None:
        values = self._display_values
        if not values:
            return

        cfg = ui(self)
        pad = max(2, int(cfg.space_s // 2))
        gap = max(1, int(cfg.space_s) - 2)
        inner = rect.adjusted(pad, pad, -pad, -pad)
        if inner.width() <= 1 or inner.height() <= 1:
            return

        count = len(values)
        total_gap = gap * max(0, count - 1)
        bar_w = max(2.0, (inner.width() - total_gap) / max(1, count))
        used_w = bar_w * count + total_gap
        start_x = inner.left() + max(0.0, (inner.width() - used_w) / 2.0)
        bar_radius = min(float(radius), bar_w / 2.0)
        min_h = max(2.0, float(cfg.space_s // 2 + 1))

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        for idx, value in enumerate(values):
            x = start_x + idx * (bar_w + gap)
            track_rect = QtCore.QRectF(x, inner.top(), bar_w, inner.height())
            painter.setBrush(colors["track"])
            painter.drawRoundedRect(track_rect, bar_radius, bar_radius)

            if value <= 0.0:
                continue

            bar_h = max(min_h, inner.height() * float(value))
            bar_h = min(bar_h, inner.height())
            bar_rect = QtCore.QRectF(x, inner.bottom() - bar_h, bar_w, bar_h)
            painter.setBrush(colors["bar"])
            painter.drawRoundedRect(bar_rect, bar_radius, bar_radius)

    def paintEvent(self, ev: QtGui.QPaintEvent) -> None:
        super().paintEvent(ev)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        outer = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if outer.width() <= 2.0 or outer.height() <= 2.0:
            return

        cfg = ui(self)
        radius = max(4.0, float(cfg.radius_m))
        palette = spectrum_palette(self._visual_state, app=_app_instance())
        colors = {
            "background": palette.background,
            "border": palette.border,
            "track": palette.track,
            "bar": palette.bar,
        }

        self._draw_background(painter, outer, radius=radius, colors=colors)
        self._draw_bars(painter, outer, radius=radius - 2.0, colors=colors)
