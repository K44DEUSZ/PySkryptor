# app/view/support/host_runtime.py
from __future__ import annotations

import os
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets


def normalize_network_status(value: str) -> str:
    """Normalize network state values to the supported set."""
    raw = str(value or "").strip().lower()
    if raw in {"online", "offline", "checking"}:
        return raw
    return "checking"


def read_network_status(parent: QtWidgets.QWidget | None) -> str:
    """Read a normalized network status from a parent view when exposed."""
    getter = getattr(parent, "network_status", None) if parent is not None else None
    if callable(getter):
        try:
            return normalize_network_status(str(getter()))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return "checking"
    return "checking"


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
