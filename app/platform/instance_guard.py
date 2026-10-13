# app/platform/instance_guard.py
from __future__ import annotations

import hashlib
from pathlib import Path

from PyQt6 import QtCore, QtNetwork, QtWidgets

from app.platform.installer_guard import InstallerGuard


class InstanceGuard(QtCore.QObject):
  """Keep one GUI process per user profile and relay activation requests."""

  activation_requested = QtCore.pyqtSignal()

  def __init__(self, profile_dir: Path, parent: QtCore.QObject | None = None) -> None:
    super().__init__(parent)
    profile_dir.mkdir(parents=True, exist_ok=True)
    identity = str(profile_dir.resolve()).casefold().encode('utf-8')
    self._server_name = 'pyskryptor-' + hashlib.sha256(identity).hexdigest()[:24]
    self._lock = QtCore.QLockFile(str(profile_dir / 'application.lock'))
    self._lock.setStaleLockTime(0)
    self._server = QtNetwork.QLocalServer(self)
    self._server.setSocketOptions(QtNetwork.QLocalServer.SocketOption.UserAccessOption)
    self._server.newConnection.connect(self._activate)
    self._window: QtWidgets.QWidget | None = None
    self._activation_pending = False
    self._installer_guard: InstallerGuard | None = None
    self.activation_requested.connect(self.restore_window)

  def acquire(self) -> bool:
    """Acquire ownership or ask the current process to show its window."""
    owned = self._lock.tryLock(0)
    if not owned:
      socket = QtNetwork.QLocalSocket(self)
      for _attempt in range(6):
        socket.connectToServer(self._server_name)
        if socket.waitForConnected(500):
          if socket.waitForReadyRead(500) and bytes(socket.readAll()).strip() == b'activated':
            socket.disconnectFromServer()
            return False
        socket.abort()
        if self._lock.tryLock(0):
          owned = True
          break
        QtCore.QThread.msleep(50)
      if not owned:
        raise RuntimeError('The running application did not respond to the activation request.')
    QtNetwork.QLocalServer.removeServer(self._server_name)
    if not self._server.listen(self._server_name):
      self._lock.unlock()
      raise RuntimeError(self._server.errorString())
    try:
      self._installer_guard = InstallerGuard()
    except OSError:
      self.release()
      raise
    return True

  def _activate(self) -> None:
    while self._server.hasPendingConnections():
      socket = self._server.nextPendingConnection()
      if socket is not None:
        closing = self._window is not None and self._window.property('application_closing')
        socket.write(b'closing\n' if closing else b'activated\n')
        socket.flush()
        socket.disconnectFromServer()
        socket.deleteLater()
        if not closing:
          self.activation_requested.emit()

  def set_window(self, window: QtWidgets.QWidget) -> None:
    self._window = window
    if self._activation_pending:
      self.restore_window()

  def restore_window(self) -> None:
    if self._window is None:
      self._activation_pending = True
      return
    self._activation_pending = False
    self._window.setWindowState(self._window.windowState() & ~QtCore.Qt.WindowState.WindowMinimized)
    self._window.show()
    self._window.raise_()
    self._window.activateWindow()

  def release(self) -> None:
    self._server.close()
    self._lock.unlock()
    if self._installer_guard is not None:
      self._installer_guard.release()
      self._installer_guard = None
