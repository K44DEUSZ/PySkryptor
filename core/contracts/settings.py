# pyskryptor/core/contracts/settings.py
from __future__ import annotations
from typing import Protocol, Optional


class SettingsProvider(Protocol):
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]: ...
