# app/view/support/popup_host.py
from __future__ import annotations

import weakref
from typing import Callable, Protocol, runtime_checkable

from PyQt5 import QtCore, QtGui, QtWidgets

from app.view.support.widget_effects import (
    bind_tracked_window,
    contains_widget_chain,
    install_app_event_filter,
    overlay_edge_gap,
)
from app.view.ui_config import ui


@runtime_checkable
class PopupHostProtocol(Protocol):
    """Interactive popup host that can be dismissed by the shared registry."""

    def hide_popup(self) -> None: ...


def hide_popup_widget(widget: QtWidgets.QWidget | None) -> bool:
    """Hide a popup widget when it is currently visible."""
    if not isinstance(widget, QtWidgets.QWidget):
        return False
    try:
        if not widget.isVisible():
            return False
        widget.hide()
        return True
    except (AttributeError, RuntimeError, TypeError):
        return False


def popup_screen_at(
    point: QtCore.QPoint,
    *,
    fallback_widget: QtWidgets.QWidget | None = None,
) -> QtGui.QScreen | None:
    """Resolve the best screen for a popup anchored near the given global point."""
    return (
        QtWidgets.QApplication.screenAt(point)
        or (fallback_widget.screen() if isinstance(fallback_widget, QtWidgets.QWidget) else None)
        or QtWidgets.QApplication.primaryScreen()
    )


def clamp_popup_geometry(
    rect: QtCore.QRect,
    *,
    point: QtCore.QPoint,
    fallback_widget: QtWidgets.QWidget | None = None,
) -> QtCore.QRect:
    """Clamp a popup rectangle to the available geometry of the active screen."""
    geom = QtCore.QRect(rect)
    screen = popup_screen_at(point, fallback_widget=fallback_widget)
    if screen is None:
        return geom

    cfg = ui(fallback_widget)
    edge = overlay_edge_gap(cfg)
    avail = screen.availableGeometry()

    if geom.right() > avail.right() - edge:
        geom.moveLeft(max(avail.left() + edge, avail.right() - geom.width() - edge))
    if geom.bottom() > avail.bottom() - edge:
        geom.moveTop(max(avail.top() + edge, avail.bottom() - geom.height() - edge))
    if geom.left() < avail.left() + edge:
        geom.moveLeft(avail.left() + edge)
    if geom.top() < avail.top() + edge:
        geom.moveTop(avail.top() + edge)
    return geom


def anchored_popup_geometry(
    anchor: QtWidgets.QWidget,
    *,
    width: int,
    height: int,
    host_margins: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> QtCore.QRect:
    """Build anchored popup geometry clamped to the anchor screen bounds."""
    cfg = ui(anchor)
    popup_gap_y = max(0, max(1, int(cfg.space_s) // 2) - 1)
    left_margin, top_margin, right_margin, bottom_margin = (int(v) for v in host_margins)

    anchor_top_left = anchor.mapToGlobal(QtCore.QPoint(0, 0))
    x = anchor_top_left.x() - left_margin
    y_below = anchor.mapToGlobal(QtCore.QPoint(0, anchor.height() + popup_gap_y)).y() - top_margin
    y_above = anchor.mapToGlobal(QtCore.QPoint(0, -popup_gap_y)).y() - (int(height) - bottom_margin)

    avail = popup_screen_at(
        anchor.mapToGlobal(QtCore.QPoint(0, anchor.height())),
        fallback_widget=anchor,
    )
    avail_rect = avail.availableGeometry() if avail is not None else QtCore.QRect(0, 0, 1920, 1080)
    y = y_below if y_below + int(height) <= avail_rect.bottom() + 1 or y_above < avail_rect.top() else y_above

    rect = QtCore.QRect(int(x), int(y), int(width), int(height))
    return clamp_popup_geometry(rect, point=anchor_top_left, fallback_widget=anchor)


class PopupHostRegistry:
    """Tracks popup hosts so a new popup can close older ones."""

    _OPEN_HOSTS: weakref.WeakSet[PopupHostProtocol] = weakref.WeakSet()

    @classmethod
    def register(cls, host: PopupHostProtocol) -> None:
        cls._OPEN_HOSTS.add(host)

    @classmethod
    def close_others(cls, current: PopupHostProtocol) -> None:
        for host in tuple(cls._OPEN_HOSTS):
            if host is current:
                continue
            try:
                host.hide_popup()
            except (AttributeError, RuntimeError, TypeError):
                continue


class _PopupHostRuntime:
    """Shares popup lifecycle rules between floating host widgets."""

    def __init__(self, owner: QtCore.QObject) -> None:
        self._owner = owner

    @staticmethod
    def is_visible(widget: QtWidgets.QWidget | None) -> bool:
        """Return True when the popup widget exists and is currently visible."""
        if not isinstance(widget, QtWidgets.QWidget):
            return False
        try:
            return bool(widget.isVisible())
        except (AttributeError, RuntimeError, TypeError):
            return False

    def install_app_filter(self, *, installed: bool) -> bool:
        """Install an application-wide event filter once for the host."""
        return install_app_event_filter(self._owner, installed=installed)

    def bind_window(
        self,
        tracked_window: QtWidgets.QWidget | None,
        widget: QtWidgets.QWidget | None,
    ) -> QtWidgets.QWidget | None:
        """Bind the popup host to the current top-level window of the target widget."""
        return bind_tracked_window(self._owner, tracked_window, widget)

    def cleanup(
        self,
        *,
        tracked_window: QtWidgets.QWidget | None,
        app_filter_installed: bool,
    ) -> tuple[QtWidgets.QWidget | None, bool]:
        """Remove tracked event filters during widget teardown."""
        if tracked_window is not None:
            try:
                tracked_window.removeEventFilter(self._owner)
            except (AttributeError, RuntimeError, TypeError):
                pass

        if app_filter_installed:
            try:
                app = QtWidgets.QApplication.instance()
                if app is not None:
                    app.removeEventFilter(self._owner)
            except (AttributeError, RuntimeError, TypeError):
                pass
        return None, False

    @staticmethod
    def contains_widget(widget: QtWidgets.QWidget | None, *roots: QtWidgets.QWidget | None) -> bool:
        """Return True when the widget belongs to any tracked popup root."""
        return contains_widget_chain(widget, *roots)

    @staticmethod
    def refresh_geometry(popup_widget: QtWidgets.QWidget | None) -> None:
        """Refresh popup geometry when the popup implementation supports it."""
        if popup_widget is None:
            return
        refresh = getattr(popup_widget, "refresh_geometry", None)
        if not callable(refresh):
            return
        try:
            refresh()
        except (AttributeError, RuntimeError, TypeError):
            return

    def handle_dismiss_event(
        self,
        *,
        obj: QtCore.QObject,
        event: QtCore.QEvent,
        popup_widget: QtWidgets.QWidget | None,
        hide_popup: Callable[[], None],
        contains_widget: Callable[[QtWidgets.QWidget | None], bool],
        tracked_window: QtWidgets.QWidget | None = None,
        popup_focus_widget: QtWidgets.QWidget | None = None,
        on_state_change: Callable[[], None] | None = None,
        handle_escape: bool = False,
    ) -> bool:
        """Dismiss a popup when global/window events indicate it should close."""
        popup_visible = self.is_visible(popup_widget)
        event_type = event.type()

        def _request_hide() -> None:
            hide_popup()
            if on_state_change is not None:
                on_state_change()

        if popup_visible:
            if event_type in {QtCore.QEvent.Type.ApplicationDeactivate, QtCore.QEvent.Type.WindowDeactivate}:
                _request_hide()
            elif handle_escape and event_type == QtCore.QEvent.Type.KeyPress and isinstance(event, QtGui.QKeyEvent):
                if event.key() == QtCore.Qt.Key.Key_Escape:
                    _request_hide()
                    event.accept()
                    return True
            elif event_type in {QtCore.QEvent.Type.MouseButtonPress, QtCore.QEvent.Type.Wheel} and isinstance(
                event,
                (QtGui.QMouseEvent, QtGui.QWheelEvent),
            ):
                target = (
                    obj
                    if isinstance(obj, QtWidgets.QWidget)
                    else QtWidgets.QApplication.widgetAt(event.globalPos())
                )
                if not contains_widget(target):
                    _request_hide()

        if obj is tracked_window and event_type in {
            QtCore.QEvent.Type.Move,
            QtCore.QEvent.Type.Resize,
            QtCore.QEvent.Type.Hide,
            QtCore.QEvent.Type.WindowDeactivate,
        }:
            if popup_visible:
                _request_hide()
            elif on_state_change is not None:
                on_state_change()
        elif obj is popup_focus_widget and event_type in {
            QtCore.QEvent.Type.FocusIn,
            QtCore.QEvent.Type.FocusOut,
            QtCore.QEvent.Type.Hide,
            QtCore.QEvent.Type.EnabledChange,
        }:
            if on_state_change is not None:
                on_state_change()
        return False


class PopupHostBinding:
    """Stateful popup-host binding that manages window and app event filters."""

    def __init__(self, owner: QtCore.QObject) -> None:
        self._runtime = _PopupHostRuntime(owner)
        self._tracked_window: QtWidgets.QWidget | None = None
        self._app_filter_installed = False

    def install_app_filter(self) -> None:
        self._app_filter_installed = self._runtime.install_app_filter(installed=self._app_filter_installed)

    def bind_window(self, widget: QtWidgets.QWidget | None) -> QtWidgets.QWidget | None:
        self._tracked_window = self._runtime.bind_window(self._tracked_window, widget)
        return self._tracked_window

    def cleanup(self) -> None:
        self._tracked_window, self._app_filter_installed = self._runtime.cleanup(
            tracked_window=self._tracked_window,
            app_filter_installed=self._app_filter_installed,
        )

    def contains_widget(self, widget: QtWidgets.QWidget | None, *roots: QtWidgets.QWidget | None) -> bool:
        return self._runtime.contains_widget(widget, *roots)

    def refresh_geometry(self, popup_widget: QtWidgets.QWidget | None) -> None:
        self._runtime.refresh_geometry(popup_widget)

    def handle_dismiss_event(
        self,
        *,
        obj: QtCore.QObject,
        event: QtCore.QEvent,
        popup_widget: QtWidgets.QWidget | None,
        hide_popup: Callable[[], None],
        contains_widget: Callable[[QtWidgets.QWidget | None], bool],
        popup_focus_widget: QtWidgets.QWidget | None = None,
        on_state_change: Callable[[], None] | None = None,
        handle_escape: bool = False,
    ) -> bool:
        return self._runtime.handle_dismiss_event(
            obj=obj,
            event=event,
            popup_widget=popup_widget,
            hide_popup=hide_popup,
            contains_widget=contains_widget,
            tracked_window=self._tracked_window,
            popup_focus_widget=popup_focus_widget,
            on_state_change=on_state_change,
            handle_escape=handle_escape,
        )
