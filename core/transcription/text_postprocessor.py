# pyskryptor/core/transcription/text_postprocessor.py
from __future__ import annotations


class TextPostprocessor:
    """Placeholder for text cleanup (trim, normalize whitespace)."""

    @staticmethod
    def clean(s: str) -> str:
        return " ".join(s.strip().split())
