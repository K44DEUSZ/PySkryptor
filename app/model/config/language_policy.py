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

    @classmethod
    def normalize_default_source_language_policy(cls, value: Any) -> str:
        token = cls.normalize_choice_value(value)
        if cls.is_last_used(token):
            return cls.LAST_USED
        if cls.is_auto(token):
            return cls.AUTO
        norm = cls.normalize_code(token, drop_region=False)
        return norm or cls.AUTO

    @classmethod
    def normalize_default_target_language_policy(cls, value: Any) -> str:
        token = cls.normalize_choice_value(value)
        if cls.is_last_used(token):
            return cls.LAST_USED
        if cls.is_default_ui(token) or not token:
            return cls.DEFAULT_UI
        norm = cls.normalize_code(token, drop_region=False)
        return norm or cls.DEFAULT_UI

    @classmethod
    def normalize_last_used_source_language(cls, value: Any) -> str:
        token = cls.normalize_choice_value(value)
        if cls.is_auto(token) or not token:
            return cls.AUTO
        norm = cls.normalize_code(token, drop_region=False)
        return norm or cls.AUTO

    @classmethod
    def normalize_last_used_target_language(cls, value: Any) -> str:
        token = cls.normalize_choice_value(value)
        if cls.is_default_ui(token) or not token:
            return cls.DEFAULT_UI
        norm = cls.normalize_code(token, drop_region=False)
        return norm or cls.DEFAULT_UI

    @classmethod
    def normalize_panel_source_language_selection(cls, value: Any) -> str:
        token = cls.normalize_choice_value(value)
        if cls.is_preferred(token):
            return cls.PREFERRED
        if cls.is_auto(token):
            return cls.AUTO
        norm = cls.normalize_code(token, drop_region=False)
        return norm or cls.PREFERRED

    @classmethod
    def normalize_panel_target_language_selection(cls, value: Any) -> str:
        token = cls.normalize_choice_value(value)
        if cls.is_preferred(token):
            return cls.PREFERRED
        if cls.is_default_ui(token):
            return cls.DEFAULT_UI
        norm = cls.normalize_code(token, drop_region=False)
        return norm or cls.PREFERRED
