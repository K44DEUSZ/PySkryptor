# view/widgets/choice_toggle.py
from __future__ import annotations

from typing import Callable, Optional

from PyQt5 import QtCore, QtGui, QtWidgets


class ChoiceToggle(QtWidgets.QWidget):
    """A two-option segmented toggle.

    UI-only component used for binary choices (e.g. Yes/No, Mode selection).
    It stores no domain logic; it only manages selection state and emits a
    signal when the user changes it.
    """

    changed = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        first_text: str,
        second_text: str,
        height: int = 26,
        first_checked: bool = True,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)

        self._btn_first = QtWidgets.QPushButton(first_text)
        self._btn_second = QtWidgets.QPushButton(second_text)

        for b in (self._btn_first, self._btn_second):
            b.setCheckable(True)
            b.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.setMinimumHeight(int(height))

        self._group.addButton(self._btn_first, 0)
        self._group.addButton(self._btn_second, 1)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._btn_first)
        lay.addWidget(self._btn_second)

        self._btn_first.setChecked(bool(first_checked))
        self._btn_second.setChecked(not bool(first_checked))

        self._group.buttonToggled.connect(self._on_toggled)
        self._apply_style()

    def _apply_style(self) -> None:
        self._btn_first.setObjectName("ChoiceToggleFirst")
        self._btn_second.setObjectName("ChoiceToggleSecond")

        self.setStyleSheet(
            """
            QPushButton#ChoiceToggleFirst,
            QPushButton#ChoiceToggleSecond {
                border: 1px solid palette(mid);
                padding: 4px 10px;
            }

            QPushButton#ChoiceToggleFirst {
                border-top-left-radius: 7px;
                border-bottom-left-radius: 7px;
                border-right: none;
            }

            QPushButton#ChoiceToggleSecond {
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
            }

            QPushButton#ChoiceToggleFirst:checked,
            QPushButton#ChoiceToggleSecond:checked {
                background: palette(highlight);
                color: palette(highlighted-text);
            }
            """
        )

    def _on_toggled(self, _btn: QtWidgets.QAbstractButton, checked: bool) -> None:
        if checked:
            self.changed.emit()

    def toggled(self, callback: Callable[[], None]) -> None:
        """Compatibility helper used in SettingsPanel."""

        self.changed.connect(callback)

    def set_checked(self, checked: bool) -> None:
        self.set_first_checked(bool(checked))

    def is_checked(self) -> bool:
        return self.is_first_checked()

    def clear_selection(self) -> None:
        self._group.setExclusive(False)
        try:
            self._btn_first.setChecked(False)
            self._btn_second.setChecked(False)
        finally:
            self._group.setExclusive(True)

    def set_first_checked(self, checked: bool) -> None:
        self._btn_first.setChecked(bool(checked))

    def set_second_checked(self, checked: bool) -> None:
        self._btn_second.setChecked(bool(checked))

    def is_first_checked(self) -> bool:
        return bool(self._btn_first.isChecked())

    def is_second_checked(self) -> bool:
        return bool(self._btn_second.isChecked())

    def set_first_enabled(self, enabled: bool) -> None:
        self._btn_first.setEnabled(bool(enabled))

    def set_second_enabled(self, enabled: bool) -> None:
        self._btn_second.setEnabled(bool(enabled))
