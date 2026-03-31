# app/model/translation/chunking.py
from __future__ import annotations

import re


def chunk_text(text: str, *, max_chars: int) -> list[str]:
    """Split translation input into paragraph and sentence-sized chunks."""

    payload = str(text or "").strip()
    if not payload:
        return []

    parts: list[str] = []
    for para in re.split(r"\n{2,}", payload):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            parts.append(para)
            continue

        buf = ""
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(buf) + 1 + len(sentence) <= max_chars:
                buf = (buf + " " + sentence).strip()
            else:
                if buf:
                    parts.append(buf)
                buf = sentence
        if buf:
            parts.append(buf)

    return parts
