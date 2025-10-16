# core/transcription/text_postprocessor.py
from __future__ import annotations

import re


class TextPostprocessor:
    """Simple text cleanup used by transcription output."""

    @staticmethod
    def clean(text: str) -> str:
        t = text.replace("\r\n", "\n")
        t = re.sub(r"[ \t]+\n", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()
