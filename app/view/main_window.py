# app/view/main_window.py
from __future__ import annotations

import logging

from PyQt5 import QtCore, QtGui, QtWidgets, QtNetwork

from app.controller.contracts import (
    DownloaderPanelViewProtocol,
    FilesPanelViewProtocol,
    LivePanelViewProtocol,
    SettingsPanelViewProtocol,
)
from app.model.config.app_meta import AppMeta
from app.view.panels.registry import PanelTabSpec, build_main_tab_specs
from app.view.support.theme_runtime import app_icon, apply_windows_dark_titlebar
from app.view.support.view_runtime import normalize_network_status
from app.view.support.widget_effects import enable_styled_background
from app.view.ui_config import UIConfig, ui

_LOG = logging.getLogger(__name__)

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
        self._ui = ui_cfg if ui_cfg is not None else ui(self)
        self._network_status: str = 'checking'
        self._network_cfg_manager: QtCore.QObject | None = None
        self._network_access_manager: QtNetwork.QNetworkAccessManager | None = None
        self._panels: dict[str, QtWidgets.QWidget] = {}

        self.setObjectName('MainWindow')
        self.setWindowTitle(AppMeta.NAME)
        enable_styled_background(self)

        self._apply_window_icon()
        self.resize(self._ui.window_default_w, self._ui.window_default_h)
        self.setMinimumSize(self._ui.window_min_w, self._ui.window_min_h)

        central = QtWidgets.QWidget(self)
        central.setObjectName('MainCentral')
        enable_styled_background(central)
        central.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(self._ui.margin, self._ui.margin, self._ui.margin, self._ui.margin)
        root.setSpacing(self._ui.spacing)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName('MainTabs')
        enable_styled_background(self.tabs)
        root.addWidget(self.tabs)

        self.files_panel: FilesPanelViewProtocol | None = None
        self.live_panel: LivePanelViewProtocol | None = None
        self.downloader_panel: DownloaderPanelViewProtocol | None = None
        self.settings_panel: SettingsPanelViewProtocol | None = None
        self.about_panel: QtWidgets.QWidget | None = None

        self._init_network_monitor()
        self._build_tabs()

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        _LOG.debug('Main window initialized. network_status=%s', self._network_status)

    def ui_config(self) -> UIConfig:
        """Return the UI configuration."""
        return self._ui

    def network_status(self) -> str:
        return str(self._network_status or 'checking')

    def _build_tabs(self) -> None:
        for spec in build_main_tab_specs():
            panel = self._create_panel(spec)
            self._panels[spec.key] = panel
            setattr(self, f'{spec.key}_panel', panel)
            self.tabs.addTab(panel, spec.title())

    def _create_panel(self, spec: PanelTabSpec) -> QtWidgets.QWidget:
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
        mgr_cls = getattr(QtNetwork, 'QNetworkConfigurationManager', None)
        if mgr_cls is not None:
            try:
                mgr = mgr_cls(self)
                self._network_cfg_manager = mgr
                if hasattr(mgr, 'onlineStateChanged'):
                    mgr.onlineStateChanged.connect(self._on_network_online_state_changed)
                if hasattr(mgr, 'configurationChanged'):
                    mgr.configurationChanged.connect(lambda *_args: self._refresh_network_status())
                if hasattr(mgr, 'updateCompleted'):
                    mgr.updateCompleted.connect(self._refresh_network_status)
                self._refresh_network_status()
                try:
                    mgr.updateConfigurations()
                except (AttributeError, RuntimeError, TypeError) as ex:
                    _LOG.debug('Network configuration refresh skipped. detail=%s', ex)
                _LOG.debug('Network monitor initialized. backend=QNetworkConfigurationManager')
                return
            except (AttributeError, RuntimeError, TypeError) as ex:
                self._network_cfg_manager = None
                _LOG.debug('Network configuration manager unavailable. detail=%s', ex)

        try:
            nam = QtNetwork.QNetworkAccessManager(self)
            self._network_access_manager = nam
            if hasattr(nam, 'networkAccessibleChanged'):
                nam.networkAccessibleChanged.connect(self._on_network_accessible_changed)
            self._refresh_network_status()
            _LOG.debug('Network monitor initialized. backend=QNetworkAccessManager')
        except (AttributeError, RuntimeError, TypeError) as ex:
            self._network_access_manager = None
            self._set_network_status('online')
            _LOG.debug('Network monitor fallback applied. backend=default_online detail=%s', ex)

    @QtCore.pyqtSlot(bool)
    def _on_network_online_state_changed(self, is_online: bool) -> None:
        self._set_network_status('online' if bool(is_online) else 'offline')

    @QtCore.pyqtSlot(int)
    def _on_network_accessible_changed(self, _state: int) -> None:
        self._refresh_network_status()

    def _refresh_network_status(self) -> None:
        mgr = self._network_cfg_manager
        if mgr is not None and hasattr(mgr, 'isOnline'):
            try:
                self._set_network_status('online' if bool(mgr.isOnline()) else 'offline')
                return
            except (AttributeError, RuntimeError, TypeError):
                self._set_network_status('online')
                return

        nam = self._network_access_manager
        if nam is not None:
            try:
                accessible = nam.networkAccessible()
                net_accessibility = getattr(QtNetwork.QNetworkAccessManager, 'NetworkAccessibility', None)
                not_accessible = getattr(
                    net_accessibility,
                    'NotAccessible',
                    getattr(QtNetwork.QNetworkAccessManager, 'NotAccessible', None),
                )
                if accessible == not_accessible:
                    self._set_network_status('offline')
                else:
                    self._set_network_status('online')
                return
            except (AttributeError, RuntimeError, TypeError):
                self._set_network_status('online')
                return

        self._set_network_status('online')

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

        popup_roles = {'comboPopupHost', 'comboPopup', 'comboPopupList', 'comboPopupViewport', 'hintPopupHost', 'hintPopup'}
        w = target
        while w is not None:
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
            apply_windows_dark_titlebar(self)
            self._dark_titlebar_applied = True
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug('Dark titlebar application skipped. detail=%s', ex)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        for panel in self._panels.values():
            on_parent_close = getattr(panel, 'on_parent_close', None)
            if not callable(on_parent_close):
                continue
            try:
                on_parent_close()
            except (AttributeError, RuntimeError, TypeError) as ex:
                _LOG.debug('Panel close hook skipped. panel=%s detail=%s', type(panel).__name__, ex)
        super().closeEvent(event)
