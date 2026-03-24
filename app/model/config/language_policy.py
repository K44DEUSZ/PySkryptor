# app/model/config/language_policy.py
from __future__ import annotations

from typing import Any

from app.model.helpers.string_utils import normalize_lang_code


class LanguagePolicy:
    """Helpers for language-policy values, panel selectors, and normalization."""

    AUTO = "auto"
    DEFAULT_UI = "default_ui"
    LAST_USED = "last_used"
    PREFERRED = "preferred"

    @classmethod
    def normalize_policy_value(cls, value: Any) -> str:
        return str(value or "").strip().lower()

    @classmethod
    def normalize_choice_value(cls, value: Any) -> str:
        return cls.normalize_policy_value(value)

    @classmethod
    def normalize_code(cls, value: str | None, *, drop_region: bool = False) -> str:
        return normalize_lang_code(value, drop_region=drop_region)

    @classmethod
    def is_auto(cls, value: Any) -> bool:
        return cls.normalize_choice_value(value) == cls.AUTO

    @classmethod
    def is_last_used(cls, value: Any) -> bool:
        return cls.normalize_choice_value(value) == cls.LAST_USED

    @classmethod
    def is_default_ui(cls, value: Any) -> bool:
        return cls.normalize_choice_value(value) == cls.DEFAULT_UI

    @classmethod
    def is_preferred(cls, value: Any) -> bool:
        return cls.normalize_choice_value(value) == cls.PREFERRED
