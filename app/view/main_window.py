# app/view/main_window.py
from __future__ import annotations

import logging
from typing import Optional, Dict, Any

from PyQt5 import QtCore, QtGui, QtWidgets, QtNetwork

from app.controller.support.localization import tr
from app.model.config.app_config import AppConfig as Config

from app.view.ui_config import (
    UIConfig,
    app_icon,
    enable_styled_background,
    apply_windows_dark_titlebar,
    normalize_network_status,
    ui,
)

from app.view.panels.files_panel import FilesPanel
from app.view.panels.live_panel import LivePanel
from app.view.panels.downloader_panel import DownloaderPanel
from app.view.panels.settings_panel import SettingsPanel
from app.view.panels.about_panel import AboutPanel

_LOG = logging.getLogger(__name__)
BootContext = Dict[str, Any]

class MainWindow(QtWidgets.QMainWindow):
    """Main application window hosting the primary panels."""

    network_status_changed = QtCore.pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        boot_ctx: Optional[BootContext] = None,
        ui_cfg: Optional[UIConfig] = None,
    ) -> None:
        super().__init__(parent)

        self._boot_ctx: Optional[BootContext] = boot_ctx
        self._ui = ui_cfg if ui_cfg is not None else ui(self)
        self._network_status: str = "checking"
        self._network_cfg_manager: Optional[QtCore.QObject] = None
        self._network_access_manager: Optional[QtNetwork.QNetworkAccessManager] = None

        self.setObjectName("MainWindow")
        self.setWindowTitle(Config.APP_NAME)
        enable_styled_background(self)

        self._apply_window_icon()
        self.resize(self._ui.window_default_w, self._ui.window_default_h)
        self.setMinimumSize(self._ui.window_min_w, self._ui.window_min_h)

        central = QtWidgets.QWidget(self)
        central.setObjectName("MainCentral")
        enable_styled_background(central)
        central.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(self._ui.margin, self._ui.margin, self._ui.margin, self._ui.margin)
        root.setSpacing(self._ui.spacing)

        self.files_panel: Optional[QtWidgets.QWidget] = None
        self.live_panel: Optional[QtWidgets.QWidget] = None
        self.down_panel: Optional[QtWidgets.QWidget] = None
        self.settings_panel: Optional[QtWidgets.QWidget] = None
        self.about_panel: Optional[QtWidgets.QWidget] = None

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("MainTabs")
        enable_styled_background(self.tabs)
        root.addWidget(self.tabs)

        self._init_network_monitor()

        self.files_panel = FilesPanel(self, boot_ctx=self._boot_ctx)
        self.live_panel = LivePanel(self, boot_ctx=self._boot_ctx)
        self.down_panel = DownloaderPanel(self)
        self.settings_panel = SettingsPanel(self)
        self.about_panel = AboutPanel(self)

        self.tabs.addTab(self.files_panel, tr("tabs.files"))
        self.tabs.addTab(self.live_panel, tr("tabs.live"))
        self.tabs.addTab(self.down_panel, tr("tabs.downloader"))
        self.tabs.addTab(self.settings_panel, tr("tabs.settings"))
        self.tabs.addTab(self.about_panel, tr("tabs.about"))

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        _LOG.debug("Main window initialized. network_status=%s", self._network_status)

    def ui_config(self) -> UIConfig:
        """Return the UI configuration."""
        return self._ui

    def network_status(self) -> str:
        return str(self._network_status or "checking")

    # ----- App icon -----
    def _apply_window_icon(self) -> None:
        icon = QtWidgets.QApplication.windowIcon()
        if icon is None or icon.isNull():
            icon = app_icon()
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

    # ----- Network status -----
    def _set_network_status(self, value: str) -> None:
        status = normalize_network_status(value)
        if status == self._network_status:
            return
        prev = self._network_status
        self._network_status = status
        _LOG.debug("Network status changed. previous=%s current=%s", prev, status)
        self.network_status_changed.emit(status)

    def _init_network_monitor(self) -> None:
        mgr_cls = getattr(QtNetwork, "QNetworkConfigurationManager", None)
        if mgr_cls is not None:
            try:
                mgr = mgr_cls(self)
                self._network_cfg_manager = mgr
                if hasattr(mgr, "onlineStateChanged"):
                    mgr.onlineStateChanged.connect(self._on_network_online_state_changed)
                if hasattr(mgr, "configurationChanged"):
                    mgr.configurationChanged.connect(lambda *_args: self._refresh_network_status())
                if hasattr(mgr, "updateCompleted"):
                    mgr.updateCompleted.connect(self._refresh_network_status)
                self._refresh_network_status()
                try:
                    mgr.updateConfigurations()
                except Exception:
                    pass
                _LOG.debug("Network monitor initialized. backend=QNetworkConfigurationManager")
                return
            except Exception:
                self._network_cfg_manager = None

        try:
            nam = QtNetwork.QNetworkAccessManager(self)
            self._network_access_manager = nam
            if hasattr(nam, "networkAccessibleChanged"):
                nam.networkAccessibleChanged.connect(self._on_network_accessible_changed)
            self._refresh_network_status()
            _LOG.debug("Network monitor initialized. backend=QNetworkAccessManager")
        except Exception:
            self._network_access_manager = None
            self._set_network_status("online")
            _LOG.debug("Network monitor fallback applied. backend=default_online")

    @QtCore.pyqtSlot(bool)
    def _on_network_online_state_changed(self, is_online: bool) -> None:
        self._set_network_status("online" if bool(is_online) else "offline")

    @QtCore.pyqtSlot(int)
    def _on_network_accessible_changed(self, _state: int) -> None:
        self._refresh_network_status()

    def _refresh_network_status(self) -> None:
        mgr = self._network_cfg_manager
        if mgr is not None and hasattr(mgr, "isOnline"):
            try:
                self._set_network_status("online" if bool(mgr.isOnline()) else "offline")
                return
            except Exception:
                pass

        nam = self._network_access_manager
        if nam is not None:
            try:
                accessible = nam.networkAccessible()
                if accessible == QtNetwork.QNetworkAccessManager.NotAccessible:
                    self._set_network_status("offline")
                else:
                    self._set_network_status("online")
                return
            except Exception:
                pass

        self._set_network_status("online")

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.MouseButtonPress:
            self._clear_transient_editor_focus(obj)
        return super().eventFilter(obj, event)

    def _clear_transient_editor_focus(self, target_obj: QtCore.QObject) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return

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

        popup_roles = {"comboPopupHost", "comboPopup", "comboPopupList", "comboPopupViewport", "hintPopupHost", "hintPopup"}
        w = target
        while w is not None:
            if str(w.property("role") or "") in popup_roles:
                return
            if w is focus_w:
                return
            if focus_w.isAncestorOf(w):
                return
            w = w.parentWidget()

        focus_w.clearFocus()
        central = self.centralWidget()
        if central is not None:
            central.setFocus(QtCore.Qt.MouseFocusReason)

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        try:
            if hasattr(self.files_panel, "on_parent_close"):
                self.files_panel.on_parent_close()
        except Exception:
            pass

        try:
            if hasattr(self.live_panel, "on_parent_close"):
                self.live_panel.on_parent_close()
        except Exception:
            pass

        try:
            if hasattr(self.down_panel, "on_parent_close"):
                self.down_panel.on_parent_close()
        except Exception:
            pass

        super().closeEvent(e)

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        super().showEvent(e)
        if getattr(self, "_titlebar_tuned", False):
            return
        setattr(self, "_titlebar_tuned", True)
        apply_windows_dark_titlebar(self)
