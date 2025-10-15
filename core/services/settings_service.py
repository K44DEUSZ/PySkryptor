# pyskryptor/core/services/settings_service.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class SettingsService:
    """Simple JSON settings accessor."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._data = {}
        if path and Path(path).exists():
            try:
                self._data = json.loads(Path(path).read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)
