# app/model/helpers/errors.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass
class AppError(Exception):
    """Application error represented by an i18n key and optional params."""

    key: str
    params: dict[str, Any]
    cause: BaseException | None = None

    def __str__(self) -> str:
        return str(self.key)


class OperationCancelled(RuntimeError):
    """Raised to cooperatively stop an in-progress operation."""

    pass
