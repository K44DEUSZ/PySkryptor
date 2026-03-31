# app/view/support/host_runtime.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from PyQt5 import QtCore, QtGui, QtWidgets


@runtime_checkable
class NetworkStatusHostProtocol(Protocol):
    """Host contract for views that expose normalized network state."""

    network_status_changed: Any

    def network_status(self) -> str: ...


def normalize_network_status(value: str) -> str:
    """Normalize network state values to the supported set."""
    raw = str(value or "").strip().lower()
    if raw in {"online", "offline", "checking"}:
        return raw
    return "checking"


def read_network_status(parent: QtWidgets.QWidget | None) -> str:
    """Read a normalized network status from a typed parent host."""
    if not isinstance(parent, NetworkStatusHostProtocol):
        return "checking"

    try:
        return normalize_network_status(str(parent.network_status()))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return "checking"


def connect_network_status_changed(
    parent: QtWidgets.QWidget | None,
    slot: Any,
) -> bool:
    """Connect a typed network-status host signal to a panel slot."""
    if not isinstance(parent, NetworkStatusHostProtocol):
        return False

    try:
        parent.network_status_changed.connect(slot)
        return True
    except (AttributeError, RuntimeError, TypeError):
        return False


def open_local_path(target: str | Path) -> bool:
    """Open a local filesystem path with the Windows shell."""
    try:
        path = Path(target).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return False

    try:
        os.startfile(str(path))
        return True
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def open_external_url(url: str) -> bool:
    """Open an external URL with the host desktop handler."""
    target = str(url or "").strip()
    if not target:
        return False
    return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl(target)))
