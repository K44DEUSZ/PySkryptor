# view/widgets/loading_screen.py
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from view.utils.translating import tr


class LoadingScreenWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        self.setObjectName("LoadingScreen")
        self.setMinimumSize(720, 420)

        self._title = QtWidgets.QLabel(tr("app.title"))
        f = self._title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        self._title.setFont(f)

        self._subtitle = QtWidgets.QLabel(tr("loading.subtitle"))
        self._subtitle.setWordWrap(True)

        self._status = QtWidgets.QLabel(tr("loading.stage.start"))
        self._status.setWordWrap(True)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(14)

        layout.addStretch(1)
        layout.addWidget(self._title, alignment=QtCore.Qt.AlignHCenter)
        layout.addWidget(self._subtitle, alignment=QtCore.Qt.AlignHCenter)
        layout.addSpacing(18)
        layout.addWidget(self._status)
        layout.addWidget(self._progress)
        layout.addStretch(2)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_progress(self, pct: int) -> None:
        pct = max(0, min(100, int(pct)))
        self._progress.setRange(0, 100)
        self._progress.setValue(pct)

    def set_indeterminate(self, enabled: bool) -> None:
        self._progress.setRange(0, 0 if enabled else 100)
