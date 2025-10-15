# pyskryptor/core/contracts/translator.py
from __future__ import annotations
from typing import Protocol


class Translator(Protocol):
    def t(self, key: str, default: str) -> str: ...
