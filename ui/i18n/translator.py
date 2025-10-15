# pyskryptor/ui/i18n/translator.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class JsonTranslator:
    """Minimal JSON-backed translator."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._data = {}
        if path and Path(path).exists():
            try:
                self._data = json.loads(Path(path).read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def t(self, key: str, default: str) -> str:
        val = self._data.get(key)
        return val if isinstance(val, str) else default
