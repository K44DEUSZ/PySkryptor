# app/model/helpers/errors.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AppError(Exception):
    """Application error represented by an i18n key and optional params."""

    key: str
    params: Dict[str, Any]
    cause: Optional[BaseException] = None

    def __str__(self) -> str:
        return str(self.key)


class OperationCancelled(RuntimeError):
    """Raised to cooperatively stop an in-progress operation."""

    pass
