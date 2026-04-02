# app/model/translation/errors.py
from __future__ import annotations

from typing import Any

from app.model.core.domain.errors import AppError


class TranslationError(AppError):
    """Key-based error used for i18n-friendly translation failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))
