# app/ui/components/collapsible_section.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.core.config.config import AppConfig
from app.ui.support.theme_runtime import _render_svg_icon, active_theme_key
from app.ui.support.widget_setup import setup_layout
from app.ui.ui_config import ui


class _DisclosureButton(QtWidgets.QToolButton):
  """Disclosure header with a trailing animated chevron and an optional count."""

  def __init__(self, parent: QtWidgets.QWidget) -> None:
    super().__init__(parent)
    self._indicator_angle = 0.0
    self._badge = ''
    self.setIconSize(QtCore.QSize(18, 18))

  def set_indicator_angle(self, value: float) -> None:
    self._indicator_angle = float(value)
    self.update()

  def set_badge(self, text: str) -> None:
    self._badge = str(text)
    self.update()

  def paintEvent(self, event: QtGui.QPaintEvent) -> None:
    option = QtWidgets.QStyleOptionToolButton()
    self.initStyleOption(option)
    option.text = ''
    option.icon = QtGui.QIcon()
    painter = QtWidgets.QStylePainter(self)
    painter.drawComplexControl(QtWidgets.QStyle.ComplexControl.CC_ToolButton, option)
    rect = self.rect().adjusted(12, 0, -12, 0)
    mode = QtGui.QIcon.Mode.Normal if self.isEnabled() else QtGui.QIcon.Mode.Disabled
    painter.save()
    painter.translate(rect.right() - 9, rect.center().y())
    painter.rotate(self._indicator_angle)
    self.icon().paint(painter, QtCore.QRect(-9, -9, 18, 18), QtCore.Qt.AlignmentFlag.AlignCenter, mode)
    painter.restore()
    right_gap = 30
    if self._badge:
      font = self.font()
      font.setPixelSize(11)
      painter.setFont(font)
      badge_width = max(24, painter.fontMetrics().horizontalAdvance(self._badge) + 12)
      badge_rect = QtCore.QRect(rect.right() - 30 - badge_width, rect.center().y() - 10, badge_width, 20)
      color = option.palette.color(QtGui.QPalette.ColorRole.ButtonText)
      color.setAlpha(18)
      painter.fillRect(badge_rect, color)
      painter.setPen(option.palette.color(QtGui.QPalette.ColorRole.ButtonText))
      painter.drawText(badge_rect, QtCore.Qt.AlignmentFlag.AlignCenter, self._badge)
      right_gap += badge_width + 10
    painter.setFont(self.font())
    painter.setPen(option.palette.color(QtGui.QPalette.ColorRole.ButtonText))
    text_rect = rect.adjusted(0, 0, -right_gap, 0)
    text = self.fontMetrics().elidedText(self.text(), QtCore.Qt.TextElideMode.ElideRight, text_rect.width())
    painter.drawText(text_rect, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter, text)


class CollapsibleSection(QtWidgets.QWidget):
  """Accessible disclosure with an animated body and themed SVG indicator."""

  expanded_changed = QtCore.pyqtSignal(bool)

  def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    cfg = ui(self)
    root = QtWidgets.QVBoxLayout(self)
    setup_layout(root, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_s)
    self.btn_toggle = _DisclosureButton(self)
    self.btn_toggle.setText(title)
    self.btn_toggle.setAccessibleName(title)
    self.btn_toggle.setCheckable(True)
    self.btn_toggle.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
    self.btn_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    self.btn_toggle.setProperty('role', 'disclosureHeader')
    self.btn_toggle.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    self.btn_toggle.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
    self.btn_toggle.setMinimumHeight(cfg.control_min_h)
    self.body = QtWidgets.QWidget(self)
    self.body.setProperty('role', 'disclosureBody')
    self.body.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    self.body_layout = QtWidgets.QVBoxLayout(self.body)
    setup_layout(self.body_layout, cfg=cfg, margins=(12, 8, 12, 12), spacing=cfg.space_s)
    root.addWidget(self.btn_toggle)
    root.addWidget(self.body)
    self.body.setMaximumHeight(0)
    self.body.hide()
    self._reveal_body = False
    self._animation = QtCore.QPropertyAnimation(self.body, b'maximumHeight', self)
    self._animation.setDuration(cfg.disclosure_animation_ms)
    self._animation.setEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)
    self._animation.finished.connect(self._finish_animation)
    self._indicator_animation = QtCore.QVariantAnimation(self)
    self._indicator_animation.setDuration(cfg.disclosure_animation_ms)
    self._indicator_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
    self._indicator_animation.valueChanged.connect(self.btn_toggle.set_indicator_angle)
    self.btn_toggle.toggled.connect(self.set_expanded)
    self._update_icon()

  def is_expanded(self) -> bool:
    return self.btn_toggle.isChecked()

  def set_badge(self, text: str) -> None:
    self.btn_toggle.set_badge(text)

  def set_expanded(self, expanded: bool, *, animated: bool = True) -> None:
    expanded = bool(expanded)
    self._reveal_body = expanded and animated and self.isVisible()
    self._animation.stop()
    self._indicator_animation.stop()
    blocker = QtCore.QSignalBlocker(self.btn_toggle)
    self.btn_toggle.setChecked(expanded)
    del blocker
    self._update_icon()
    current = self.body.height() if self.body.isVisible() else 0
    target = self.body_layout.sizeHint().height() if expanded else 0
    self.body.show()
    if animated and self.isVisible():
      self._indicator_animation.setStartValue(self.btn_toggle._indicator_angle)
      self._indicator_animation.setEndValue(180.0 if expanded else 0.0)
      self._indicator_animation.start()
      self._animation.setStartValue(current)
      self._animation.setEndValue(max(0, target))
      self._animation.start()
    else:
      self.btn_toggle.set_indicator_angle(180.0 if expanded else 0.0)
      self._finish_animation()
    self.expanded_changed.emit(expanded)

  def _finish_animation(self) -> None:
    expanded = self.is_expanded()
    self.body.setMaximumHeight(16777215 if expanded else 0)
    self.body.setVisible(expanded)
    self.updateGeometry()
    if self._reveal_body and expanded:
      self._reveal_body = False
      QtCore.QTimer.singleShot(0, self._scroll_to_body)

  def _scroll_to_body(self) -> None:
    parent = self.parentWidget()
    while parent is not None:
      if isinstance(parent, QtWidgets.QScrollArea):
        parent.ensureWidgetVisible(self.body, 0, 12)
        return
      parent = parent.parentWidget()

  def _update_icon(self) -> None:
    path = AppConfig.PATHS.ICONS_DIR / f'arrow_down_{active_theme_key()}.svg'
    self.btn_toggle.setIcon(_render_svg_icon(path))

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if event.type() in {QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange}:
      if hasattr(self, 'btn_toggle'):
        self._update_icon()
