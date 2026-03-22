# app/model/domain/errors.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass
class AppError(Exception):
    """Application-layer error represented by an i18n ``error.*`` key and semantic params."""

    key: str
    params: dict[str, Any] = field(default_factory=dict)
    cause: BaseException | None = None

    def __str__(self) -> str:
        return str(self.key)

class OperationCancelled(RuntimeError):
    """Raised to cooperatively stop an in-progress operation."""

    pass
