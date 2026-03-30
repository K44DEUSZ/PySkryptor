# app/model/core/runtime/platform.py
from __future__ import annotations

import sys

from app.model.core.domain.errors import AppError


def is_windows_platform() -> bool:
    return sys.platform == "win32"


def ensure_windows_platform() -> None:
    if is_windows_platform():
        return
    raise AppError("error.runtime.unsupported_platform", {"platform": str(sys.platform or "").strip() or "unknown"})
