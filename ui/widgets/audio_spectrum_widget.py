# ui/widgets/audio_spectrum_widget.py
from __future__ import annotations

from typing import List

from PyQt5 import QtWidgets, QtCore, QtGui


class AudioSpectrumWidget(QtWidgets.QWidget):
    """
    Lightweight input-audio spectrum meter (bar graph).
    Accepts a list of floats in [0..1].
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None, *, bars: int = 24) -> None:
        super().__init__(parent)
        self._bars = max(8, int(bars))
        self._values: List[float] = [0.0] * self._bars

        self.setMinimumHeight(46)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    @QtCore.pyqtSlot(object)
    def set_spectrum(self, values: object) -> None:
        try:
            arr = list(values)  # type: ignore[arg-type]
        except Exception:
            return

        if not arr:
            self._values = [0.0] * self._bars
            self.update()
            return

        # Normalize length to _bars
        out: List[float] = []
        n = len(arr)
        for i in range(self._bars):
            src_i = int(i * n / self._bars)
            v = float(arr[src_i])
            if v < 0.0:
                v = 0.0
            if v > 1.0:
                v = 1.0
            out.append(v)

        self._values = out
        self.update()

    def paintEvent(self, ev: QtGui.QPaintEvent) -> None:
        super().paintEvent(ev)

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        r = self.rect().adjusted(2, 2, -2, -2)

        # background
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(20, 20, 20, 35))
        p.drawRoundedRect(r, 8, 8)

        # bars
        if not self._values:
            return

        bar_count = len(self._values)
        gap = 3
        total_gap = gap * (bar_count - 1)
        w = max(1, (r.width() - total_gap) // bar_count)
        max_h = max(1, r.height() - 6)

        x = r.left()
        base_y = r.bottom() - 3

        for v in self._values:
            h = int(max_h * float(v))
            if h < 2:
                h = 2

            bar_rect = QtCore.QRect(x, base_y - h, w, h)
            p.setBrush(QtGui.QColor(255, 255, 255, 160))
            p.drawRoundedRect(bar_rect, 2, 2)

            x += w + gap