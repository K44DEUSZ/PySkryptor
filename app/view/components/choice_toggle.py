# app/view/components/choice_toggle.py
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from app.view.support.widget_effects import repolish_widget
from app.view.support.widget_setup import set_interactive_cursor, set_widget_style_role
from app.view.ui_config import ui

class ChoiceToggle(QtWidgets.QWidget):
    """Two-option segmented toggle."""

    changed = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        first_text: str,
        second_text: str,
        height: int | None = None,
        first_checked: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        cfg = ui(self)
        h = int(height or cfg.control_min_h)

        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)

        self._btn_first = QtWidgets.QPushButton(first_text)
        self._btn_second = QtWidgets.QPushButton(second_text)

        for b in (self._btn_first, self._btn_second):
            b.setCheckable(True)
            set_interactive_cursor(b)
            b.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            b.setFixedHeight(h)
            b.setMinimumWidth(cfg.control_min_w)

        self._btn_first.setObjectName("ChoiceToggleFirst")
        self._btn_second.setObjectName("ChoiceToggleSecond")
        set_widget_style_role(self._btn_first, chrome="field")
        set_widget_style_role(self._btn_second, chrome="field")
        self._btn_first.setProperty("role", "toggle")
        self._btn_second.setProperty("role", "toggle")
        self._btn_first.setProperty("segment", "left")
        self._btn_second.setProperty("segment", "right")
        self._repolish_segment(self._btn_first)
        self._repolish_segment(self._btn_second)

        self._group.addButton(self._btn_first, 0)
        self._group.addButton(self._btn_second, 1)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._btn_first)
        lay.addWidget(self._btn_second)

        self._btn_first.setChecked(bool(first_checked))
        self._btn_second.setChecked(not bool(first_checked))
        self._dirty_value = False
        self._sync_dirty_value_state()

        self._group.buttonToggled.connect(self._on_toggled)

    @staticmethod
    def _repolish_segment(button: QtWidgets.QPushButton) -> None:
        repolish_widget(button)

    def _on_toggled(self, _btn: QtWidgets.QAbstractButton, checked: bool) -> None:
        if checked:
            self._sync_dirty_value_state()
            self.changed.emit()

    def _set_segment_dirty(self, button: QtWidgets.QPushButton, dirty: bool) -> None:
        if button.property("dirtyValue") != bool(dirty):
            button.setProperty("dirtyValue", bool(dirty))
            self._repolish_segment(button)

    def _sync_dirty_value_state(self) -> None:
        dirty = bool(getattr(self, "_dirty_value", False))
        self._set_segment_dirty(self._btn_first, dirty and self._btn_first.isChecked())
        self._set_segment_dirty(self._btn_second, dirty and self._btn_second.isChecked())

    def set_first_checked(self, checked: bool) -> None:
        if bool(checked):
            self._btn_first.setChecked(True)
        else:
            self._btn_second.setChecked(True)

    def set_second_checked(self, checked: bool) -> None:
        if bool(checked):
            self._btn_second.setChecked(True)
        else:
            self._btn_first.setChecked(True)

    def is_first_checked(self) -> bool:
        return bool(self._btn_first.isChecked())

    def is_second_checked(self) -> bool:
        return bool(self._btn_second.isChecked())

    def set_second_enabled(self, enabled: bool) -> None:
        self._btn_second.setEnabled(bool(enabled))

    def set_dirty_value(self, enabled: bool) -> None:
        self._dirty_value = bool(enabled)
        self._sync_dirty_value_state()
