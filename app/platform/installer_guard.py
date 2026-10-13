# app/platform/installer_guard.py
from __future__ import annotations

import sys


class InstallerGuard:
  """Expose application lifetime to the Windows installer and uninstaller."""

  def __init__(self) -> None:
    self._handle = None
    if sys.platform != 'win32':
      return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    self._kernel = kernel
    self._handle = kernel.CreateMutexW(None, False, 'Local\\PySkryptorDesktop')
    if not self._handle:
      raise ctypes.WinError(ctypes.get_last_error())

  def release(self) -> None:
    if self._handle is not None:
      self._kernel.CloseHandle(self._handle)
      self._handle = None
