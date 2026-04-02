# app/model/core/utils/text_stitching.py
from __future__ import annotations

import re
from collections.abc import Iterable


def stitch_texts(parts: Iterable[str]) -> str:
    """Stitch text fragments by removing simple overlaps and duplicates."""

    def _normalize(text: str) -> str:
        value = str(text or "").replace("\r\n", "\n")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    def _words(text: str) -> list[str]:
        return [word for word in _normalize(text).split() if word]

    stitched: list[str] = []
    for part in parts:
        part_text = _normalize(str(part or ""))
        if not part_text:
            continue
        if not stitched:
            stitched.append(part_text)
            continue

        prev_text = stitched[-1]
        prev_words = _words(prev_text)
        next_words = _words(part_text)
        if not prev_words or not next_words:
            stitched.append(part_text)
            continue

        max_overlap = min(len(prev_words), len(next_words), 12)
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if prev_words[-size:] == next_words[:size]:
                overlap = size
                break

        if overlap:
            stitched[-1] = " ".join(prev_words + next_words[overlap:]).strip()
            continue

        if part_text != prev_text:
            stitched.append(part_text)

    return _normalize("\n".join(stitched))
