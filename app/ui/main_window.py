# app/ui/main_window.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from PyQt6 import QtCore, QtGui, QtNetwork, QtWidgets

from app.application.panel_protocols import (
  DownloaderPanelViewProtocol,
  FilesPanelViewProtocol,
  LivePanelViewProtocol,
  SettingsPanelViewProtocol,
)
from app.core.config.meta import AppMeta
from app.core.runtime.localization import tr
from app.ui.components.action_button import ActionButton
from app.ui.components.animated_tabs import AnimatedTabs
from app.ui.components.desktop_canvas import DesktopCanvas
from app.ui.components.section_group import SectionGroup
from app.ui.components.system_status import SystemStatusBar
from app.ui.components.window_title_bar import WindowTitleBar
from app.ui.panels.about_panel import AboutPanel
from app.ui.panels.downloader_panel import DownloaderPanel
from app.ui.panels.files_panel import FilesPanel
from app.ui.panels.live_panel import LivePanel
from app.ui.panels.settings_panel import SettingsPanel
from app.ui.support.action_icons import action_icon, refresh_action_icons
from app.ui.support.host_runtime import normalize_network_status
from app.ui.support.theme_runtime import (
  LogoSvgLabel,
  app_icon,
  apply_application_theme,
  apply_windows_dark_titlebar,
  logo_svg_path,
)
from app.ui.support.widget_effects import enable_styled_background, repolish_widget
from app.ui.support.widget_setup import set_passive_cursor, setup_button
from app.ui.support.window_frame import configure_native_frame, native_frame_event
from app.ui.ui_config import UIConfig, ui

_LOG = logging.getLogger(__name__)


class _PanelFactoryProtocol(Protocol):
  """Callable factory used by MainWindow to build one panel instance."""

  def __call__(self, parent: QtWidgets.QWidget | None) -> QtWidgets.QWidget: ...


@runtime_checkable
class _ParentCloseAwareProtocol(Protocol):
  """Panel contract for optional parent-close cleanup hooks."""

  def on_parent_close(self) -> None: ...


@dataclass(frozen=True)
class _PanelTabSpec:
  """Immutable tab descriptor used to build the main window panels."""

  key: str
  title_key: str
  factory: _PanelFactoryProtocol

  def title(self) -> str:
    return tr(self.title_key)


def _build_main_tab_specs() -> tuple[_PanelTabSpec, ...]:
  """Return the ordered tab specifications for the main window."""

  return (
    _PanelTabSpec(key='files', title_key='tabs.files', factory=FilesPanel),
    _PanelTabSpec(key='live', title_key='tabs.live', factory=LivePanel),
    _PanelTabSpec(key='downloader', title_key='tabs.downloader', factory=DownloaderPanel),
    _PanelTabSpec(key='settings', title_key='tabs.settings', factory=SettingsPanel),
    _PanelTabSpec(key='about', title_key='tabs.about', factory=AboutPanel),
  )


class MainWindow(QtWidgets.QMainWindow):
  """Main application window hosting the primary panels."""

  network_status_changed = QtCore.pyqtSignal(str)

  def __init__(
    self,
    parent: QtWidgets.QWidget | None = None,
    ui_cfg: UIConfig | None = None,
  ) -> None:
    super().__init__(parent)

    self._dark_titlebar_applied = False
    self._close_prepared = False
    self._ui = ui_cfg if ui_cfg is not None else ui(self)
    self._network_status: str = 'checking'
    self._panels: dict[str, QtWidgets.QWidget] = {}
    self.title_bar = None
    self._sidebar_override: bool | None = None

    self.setObjectName('MainWindow')
    self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
    self.setWindowTitle(AppMeta.NAME)
    enable_styled_background(self)

    self._apply_window_icon()
    self.resize(self._ui.window_default_w, self._ui.window_default_h)
    self.setMinimumSize(self._ui.window_min_w, self._ui.window_min_h)

    central = DesktopCanvas(self)
    central.setObjectName('MainCentral')
    enable_styled_background(central)
    central.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
    self.setCentralWidget(central)

    outer = QtWidgets.QVBoxLayout(central)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    outer.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
    self.title_bar = WindowTitleBar(self, central, navigation_callback=self.toggle_sidebar)
    outer.addWidget(self.title_bar)
    body = QtWidgets.QWidget(central)
    self._workspace_body = body
    outer.addWidget(body, 1)
    root = QtWidgets.QHBoxLayout(body)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    root.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
    self._navigation_buttons: list[QtWidgets.QPushButton] = []
    self._sidebar = QtWidgets.QFrame(central)
    self._sidebar.setObjectName('AppSidebar')
    self._sidebar.setFixedWidth(self._ui.sidebar_width)
    enable_styled_background(self._sidebar)
    self._sidebar_layout = QtWidgets.QVBoxLayout(self._sidebar)
    self._sidebar_layout.setContentsMargins(16, 24, 16, 18)
    self._sidebar_layout.setSpacing(0)
    path = logo_svg_path(wordmark=True)
    if path is not None:
      self._brand_logo = LogoSvgLabel(path, object_name='SidebarLogo')
      self._brand_logo.update_for_bounds(self._ui.sidebar_width - 64, 30)
      self._sidebar_layout.addWidget(self._brand_logo, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
    self._sidebar_layout.addSpacing(24)
    navigation = QtWidgets.QWidget(self._sidebar)
    self._navigation_layout = QtWidgets.QVBoxLayout(navigation)
    self._navigation_layout.setContentsMargins(0, 0, 0, 0)
    self._navigation_layout.setSpacing(6)
    self._sidebar_layout.addWidget(navigation)
    root.addWidget(self._sidebar)

    content = QtWidgets.QWidget(central)
    content_layout = QtWidgets.QVBoxLayout(content)
    self._content_layout = content_layout
    content_layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
    content_layout.setContentsMargins(28, 24, 24, 14)
    content_layout.setSpacing(16)
    self.lbl_page_title = QtWidgets.QLabel()
    self.lbl_page_title.setObjectName('PageTitle')
    self.lbl_page_description = QtWidgets.QLabel()
    self.lbl_page_description.setObjectName('PageDescription')
    self.lbl_page_description.setWordWrap(True)
    heading = QtWidgets.QWidget(content)
    heading_layout = QtWidgets.QVBoxLayout(heading)
    heading_layout.setContentsMargins(0, 0, 0, 0)
    heading_layout.setSpacing(4)
    heading_layout.addWidget(self.lbl_page_title)
    heading_layout.addWidget(self.lbl_page_description)
    content_layout.addWidget(heading)
    self.tabs = AnimatedTabs()
    self.tabs.setObjectName('MainTabs')
    self.tabs.tabBar().hide()
    enable_styled_background(self.tabs)
    set_passive_cursor(central)
    set_passive_cursor(self.tabs)
    content_layout.addWidget(self.tabs, 1)
    self.tabs.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Expanding)
    self.system_status = SystemStatusBar(content)
    self.system_status.setObjectName('SystemStatus')
    content_layout.addWidget(self.system_status)
    root.addWidget(content, 1)

    self.files_panel: FilesPanelViewProtocol | None = None
    self.live_panel: LivePanelViewProtocol | None = None
    self.downloader_panel: DownloaderPanelViewProtocol | None = None
    self.settings_panel: SettingsPanelViewProtocol | None = None
    self.about_panel: QtWidgets.QWidget | None = None

    self._init_network_monitor()
    self._build_tabs()
    divider = QtWidgets.QFrame(self._sidebar)
    divider.setObjectName('NavigationDivider')
    divider.setFixedHeight(20)
    self._navigation_layout.insertWidget(4, divider)
    self.tabs.currentChanged.connect(self._sync_navigation)
    self._sync_navigation(self.tabs.currentIndex())
    if isinstance(self.files_panel, FilesPanel):
      self.system_status.bind_badge(self.files_panel.model_info)
    self._sidebar_layout.addStretch(1)
    footer = QtWidgets.QFrame(self._sidebar)
    footer.setObjectName('SidebarFooter')
    footer_layout = QtWidgets.QVBoxLayout(footer)
    footer_layout.setContentsMargins(0, 12, 0, 0)
    footer_layout.setSpacing(2)
    self.lbl_version = QtWidgets.QLabel(AppMeta.VERSION)
    self.lbl_version.setProperty('role', 'overline')
    self.lbl_version.setObjectName('SidebarVersion')
    footer_layout.addWidget(self.lbl_version)
    self._sidebar_layout.addWidget(footer)
    self._sidebar_shortcut = QtGui.QShortcut(QtGui.QKeySequence('Ctrl+B'), self)
    self._sidebar_shortcut.activated.connect(self.toggle_sidebar)
    refresh_action_icons(self)

    app = QtWidgets.QApplication.instance()
    if app is not None:
      app.installEventFilter(self)
      app.styleHints().colorSchemeChanged.connect(self._on_system_appearance_changed)

    _LOG.debug('Main window initialized. network_status=%s', self._network_status)

  def ui_config(self) -> UIConfig:
    """Return the UI configuration."""
    return self._ui

  def toggle_sidebar(self) -> None:
    self._sidebar_override = not self._sidebar.isVisible()
    self._update_responsive_layout()

  def _update_responsive_layout(self) -> None:
    if not hasattr(self, 'lbl_version'):
      return
    width = self.width()
    visible = self._sidebar_override if self._sidebar_override is not None else width >= 1040
    rail = self._sidebar_override is None and 1040 <= width < 1280
    self._sidebar.setVisible(visible)
    self._sidebar.setFixedWidth(72 if rail else self._ui.sidebar_width)
    if hasattr(self, '_brand_logo'):
      self._brand_logo.setVisible(not rail)
    for button in self._navigation_buttons:
      name = tr('tabs.' + str(button.property('panelKey')))
      button.setText('' if rail else name)
      button.setToolTip(name)
      button.setAccessibleName(name)
    self.lbl_version.setVisible(not rail)
    margin = 16 if width < 1040 else 28
    self._content_layout.setContentsMargins(margin, 16 if width < 1040 else 24, 16 if width < 1040 else 24, 14)

  def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
    super().resizeEvent(event)
    self._update_responsive_layout()

  def set_runtime_busy(self, busy: bool) -> None:
    self._workspace_body.setEnabled(not busy)
    self.statusBar().setVisible(busy)
    if not busy:
      self.statusBar().clearMessage()

  def set_runtime_status(self, text: str) -> None:
    self.statusBar().showMessage(text)

  def network_status(self) -> str:
    return str(self._network_status or 'checking')

  def _build_tabs(self) -> None:
    for spec in _build_main_tab_specs():
      panel = self._create_panel(spec)
      self._panels[spec.key] = panel
      self._bind_panel(spec.key, panel)
      self.tabs.addTab(panel, action_icon(spec.title_key), spec.title())
      self._add_navigation_button(spec.key, spec.title_key)
      if spec.key == 'settings' and isinstance(panel, SettingsPanel):
        self.models_panel = panel.models_page
        self._panels['models'] = self.models_panel
        self.tabs.insertTab(3, self.models_panel, action_icon('tabs.models'), tr('tabs.models'))
        self._add_navigation_button('models', 'tabs.models', position=3)

  def _add_navigation_button(self, key: str, title_key: str, *, position: int | None = None) -> None:
    button = ActionButton(action_icon(title_key), tr(title_key), self._sidebar)
    setup_button(button, min_w=0, min_h=42)
    button.setIconSize(QtCore.QSize(18, 18))
    button.setProperty('role', 'navigation')
    button.setProperty('panelKey', key)
    button.setCheckable(True)
    button.clicked.connect(lambda _checked=False, name=key: self.tabs.setCurrentWidget(self._panels[name]))
    if position is None:
      self._navigation_buttons.append(button)
    else:
      self._navigation_buttons.insert(position, button)
    self._navigation_layout.insertWidget(self._navigation_buttons.index(button), button)

  def _sync_navigation(self, index: int) -> None:
    panel = self.tabs.widget(index)
    key = next((name for name, widget in self._panels.items() if widget is panel), 'files')
    for button in self._navigation_buttons:
      active = button.property('panelKey') == key
      button.setChecked(active)
      button.setProperty('navigationActive', active)
      repolish_widget(button)
    self.lbl_page_title.setText(tr(f'tabs.{key}'))
    self.lbl_page_description.setText(tr(f'desktop.page.{key}'))
    refresh_action_icons(self)

  def _on_system_appearance_changed(self, _scheme: QtCore.Qt.ColorScheme) -> None:
    app = QtWidgets.QApplication.instance()
    if app is not None and str(app.property('themePreference') or 'auto') == 'auto':
      apply_application_theme('auto')

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if event.type() == QtCore.QEvent.Type.WindowStateChange and self.title_bar is not None:
      self.title_bar.refresh_icons()
    if event.type() in {QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange}:
      if hasattr(self, '_navigation_buttons'):
        refresh_action_icons(self)
      if hasattr(self, 'tabs'):
        for index in range(self.tabs.count()):
          key = next((name for name, panel in self._panels.items() if panel is self.tabs.widget(index)), '')
          if key:
            self.tabs.setTabIcon(index, action_icon(f'tabs.{key}'))

  def _bind_panel(self, key: str, panel: QtWidgets.QWidget) -> None:
    if key == 'files':
      self.files_panel = panel
      return
    if key == 'live':
      self.live_panel = panel
      return
    if key == 'downloader':
      self.downloader_panel = panel
      return
    if key == 'settings':
      self.settings_panel = panel
      return
    if key == 'about':
      self.about_panel = panel
      return
    raise ValueError(f'Unsupported panel key: {key}')

  def _create_panel(self, spec: _PanelTabSpec) -> QtWidgets.QWidget:
    return spec.factory(self)

  def _apply_window_icon(self) -> None:
    icon = QtWidgets.QApplication.windowIcon()
    if icon is None or icon.isNull():
      icon = app_icon()
    if icon is not None and not icon.isNull():
      self.setWindowIcon(icon)

  def _set_network_status(self, value: str) -> None:
    status = normalize_network_status(value)
    if status == self._network_status:
      return
    prev = self._network_status
    self._network_status = status
    _LOG.debug('Network status changed. previous=%s current=%s', prev, status)
    self.network_status_changed.emit(status)

  def _init_network_monitor(self) -> None:
    QtNetwork.QNetworkInformation.loadDefaultBackend()
    self._network_info = QtNetwork.QNetworkInformation.instance()
    if self._network_info is not None:
      self._network_info.reachabilityChanged.connect(self._refresh_network_status)
    self._refresh_network_status()

  def _refresh_network_status(self, *_args: object) -> None:
    info = self._network_info
    if info is None:
      self._set_network_status('checking')
      return
    reachability = QtNetwork.QNetworkInformation.Reachability
    state = info.reachability()
    self._set_network_status(
      'online' if state == reachability.Online else ('checking' if state == reachability.Unknown else 'offline')
    )

  def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
    if event.type() == QtCore.QEvent.Type.MouseButtonPress:
      self._clear_transient_editor_focus(obj)
    return super().eventFilter(obj, event)

  def _clear_transient_editor_focus(self, target_obj: QtCore.QObject) -> None:
    app_obj = QtWidgets.QApplication.instance()
    if not isinstance(app_obj, QtWidgets.QApplication):
      return
    app = app_obj

    focus_w = app.focusWidget()
    if not isinstance(focus_w, QtWidgets.QWidget):
      return
    if not isinstance(focus_w, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit)):
      return

    target = target_obj if isinstance(target_obj, QtWidgets.QWidget) else None
    if target is None:
      return

    if target is focus_w or focus_w.isAncestorOf(target):
      return

    popup_roles = {'comboPopupList', 'comboPopupViewport'}
    popup_surfaces = {'floatingHost', 'floatingBody'}
    w = target
    while w is not None:
      if str(w.property('uiSurface') or '') in popup_surfaces:
        return
      if str(w.property('role') or '') in popup_roles:
        return
      if w is focus_w:
        return
      if focus_w.isAncestorOf(w):
        return
      w = w.parentWidget()

    focus_w.clearFocus()
    central = self.centralWidget()
    if central is not None:
      central.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)

  def showEvent(self, event: QtGui.QShowEvent) -> None:
    super().showEvent(event)
    if self._dark_titlebar_applied:
      return
    try:
      configure_native_frame(self)
      apply_windows_dark_titlebar(self)
      self._dark_titlebar_applied = True
    except (AttributeError, RuntimeError, TypeError) as ex:
      _LOG.debug('Dark titlebar application skipped. detail=%s', ex)

  def nativeEvent(self, event_type, message) -> tuple[bool, int]:
    return native_frame_event(self, getattr(self, 'title_bar', None), message)

  def prepare_shutdown(self) -> None:
    if self._close_prepared:
      return
    self._close_prepared = True
    self.system_status.set_expanded(False)
    for group in self.findChildren(SectionGroup):
      group.release_elevation()
    for panel in self._panels.values():
      if not isinstance(panel, _ParentCloseAwareProtocol):
        continue
      try:
        panel.on_parent_close()
      except (AttributeError, RuntimeError, TypeError) as ex:
        _LOG.debug('Panel close hook skipped. panel=%s detail=%s', type(panel).__name__, ex)

  def closeEvent(self, event: QtGui.QCloseEvent) -> None:
    self.prepare_shutdown()
    super().closeEvent(event)
