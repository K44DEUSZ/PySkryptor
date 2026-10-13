# app/ui/support/window_frame.py
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PyQt6 import QtCore, QtWidgets


class _MonitorInfo(ctypes.Structure):
  _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT), ('work', wintypes.RECT), ('flags', wintypes.DWORD)]


def _native_windows() -> bool:
  app = QtWidgets.QApplication.instance()
  return sys.platform == 'win32' and app is not None and app.platformName() == 'windows'


def configure_native_frame(window: QtWidgets.QWidget) -> None:
  """Keep native resize, taskbar and window-management styles with a custom caption."""
  if not _native_windows():
    return
  user = ctypes.windll.user32
  pointer_type = ctypes.c_ssize_t
  getter = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
  setter = user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.SetWindowLongW
  getter.argtypes = [wintypes.HWND, ctypes.c_int]
  getter.restype = pointer_type
  setter.argtypes = [wintypes.HWND, ctypes.c_int, pointer_type]
  setter.restype = pointer_type
  hwnd = int(window.winId())
  style = getter(hwnd, -16) | 0x00040000 | 0x00080000 | 0x00010000 | 0x00020000
  setter(hwnd, -16, style)
  user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
  user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)


def native_frame_event(window: QtWidgets.QWidget, title_bar, message) -> tuple[bool, int]:
  """Handle native client bounds and resize hit testing in physical screen coordinates."""
  if not _native_windows():
    return False, 0
  msg = wintypes.MSG.from_address(int(message))
  user = ctypes.windll.user32
  user.IsZoomed.argtypes = [wintypes.HWND]
  if msg.message == 0x0083 and msg.wParam:
    if user.IsZoomed(msg.hWnd):
      user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
      user.MonitorFromWindow.restype = wintypes.HANDLE
      info = _MonitorInfo()
      info.size = ctypes.sizeof(info)
      user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
      monitor = user.MonitorFromWindow(msg.hWnd, 2)
      if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        rect = wintypes.RECT.from_address(msg.lParam)
        rect.left, rect.top, rect.right, rect.bottom = info.work.left, info.work.top, info.work.right, info.work.bottom
    return True, 0
  if msg.message != 0x0084:
    return False, 0
  rect = wintypes.RECT()
  user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
  if not user.GetWindowRect(msg.hWnd, ctypes.byref(rect)):
    return False, 0
  x = ctypes.c_short(msg.lParam & 0xFFFF).value
  y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
  dpr = window.devicePixelRatioF()
  if not user.IsZoomed(msg.hWnd):
    border = round(6 * dpr)
    left, right = x < rect.left + border, x >= rect.right - border
    top, bottom = y < rect.top + border, y >= rect.bottom - border
    if top and left:
      return True, 13
    if top and right:
      return True, 14
    if bottom and left:
      return True, 16
    if bottom and right:
      return True, 17
    if left or right or top or bottom:
      return True, 10 if left else 11 if right else 12 if top else 15
  if title_bar is not None:
    point = QtCore.QPoint(round((x - rect.left) / dpr), round((y - rect.top) / dpr))
    point = title_bar.mapFrom(window, point)
    if title_bar.is_caption_point(point):
      return True, 2
  return False, 0
