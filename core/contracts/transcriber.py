# pyskryptor/core/contracts/transcriber.py
from __future__ import annotations
from typing import Any, Protocol


class Transcriber(Protocol):
    @property
    def pipeline(self) -> Any: ...
    def build(self, log) -> None: ...
