# app/model/config/language_policy.py
from __future__ import annotations

from typing import Any

from app.model.helpers.string_utils import normalize_lang_code

class LanguagePolicy:
    """Helpers for policy-style language values and normalization."""

    AUTO = "auto"
    DEFAULT = "default"
    UI = "ui"
    APP = "app"
    DEFAULT_UI = "default_ui"

    DOWNLOAD_AUDIO_AUTO_VALUES: tuple[str, ...] = (DEFAULT, AUTO, "-")
    TRANSLATION_SOURCE_DEFERRED_VALUES: tuple[str, ...] = (
        AUTO,
        UI,
        APP,
        DEFAULT,
        DEFAULT_UI,
    )
    TRANSLATION_TARGET_DEFERRED_VALUES: tuple[str, ...] = (
        AUTO,
        DEFAULT,
        UI,
        APP,
        DEFAULT_UI,
    )

    @classmethod
    def normalize_policy_value(cls, value: Any) -> str:
        return str(value or "").strip().lower()

    @classmethod
    def normalize_choice_value(cls, value: Any) -> str:
        token = cls.normalize_policy_value(value)
        if token == "default-ui":
            return cls.DEFAULT_UI
        return token

    @classmethod
    def normalize_code(cls, value: str | None, *, drop_region: bool = False) -> str:
        return normalize_lang_code(value, drop_region=drop_region)

    @classmethod
    def is_auto(cls, value: Any) -> bool:
        return cls.normalize_choice_value(value) == cls.AUTO

    @classmethod
    def is_download_audio_auto(cls, value: Any) -> bool:
        token = cls.normalize_policy_value(value)
        return token in set(cls.DOWNLOAD_AUDIO_AUTO_VALUES)

    @classmethod
    def is_translation_source_deferred(cls, value: Any) -> bool:
        token = cls.normalize_choice_value(value)
        return (not token) or token in set(cls.TRANSLATION_SOURCE_DEFERRED_VALUES)

    @classmethod
    def is_translation_target_deferred(cls, value: Any) -> bool:
        token = cls.normalize_choice_value(value)
        return (not token) or token in set(cls.TRANSLATION_TARGET_DEFERRED_VALUES)
