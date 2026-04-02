# app/model/translation/chunking.py
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationChunk:
    """Single translation chunk with explicit join semantics."""

    text: str
    joiner: str


_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def _normalize_limit(max_chars: int) -> int:
    try:
        return max(1, int(max_chars))
    except (TypeError, ValueError):
        return 1


def _last_sentence_cut(window: str) -> int | None:
    cut: int | None = None
    for match in _SENTENCE_BOUNDARY_RE.finditer(window):
        if match.start() > 0:
            cut = match.start()
    return cut


def _last_whitespace_cut(window: str) -> int | None:
    for idx in range(len(window) - 1, -1, -1):
        if window[idx].isspace():
            return idx
    return None


def _split_paragraph(paragraph: str, *, max_chars: int, final_joiner: str) -> list[TranslationChunk]:
    limit = _normalize_limit(max_chars)
    text = str(paragraph or "").strip()
    if not text:
        return []

    chunks: list[TranslationChunk] = []
    start = 0
    while start < len(text):
        remaining = text[start:]
        if len(remaining) <= limit:
            chunks.append(TranslationChunk(remaining, final_joiner))
            break

        window = text[start : start + limit]
        cut = _last_sentence_cut(window)
        joiner = " "
        if cut is None:
            cut = _last_whitespace_cut(window)
        if cut is None or cut <= 0:
            cut = len(window)
            joiner = ""

        end = start + cut
        chunk_text = text[start:end].strip()
        if not chunk_text:
            end = min(len(text), start + limit)
            chunk_text = text[start:end]
            joiner = ""

        chunks.append(TranslationChunk(chunk_text, joiner))
        start = end
        while start < len(text) and text[start].isspace():
            start += 1

    if chunks:
        last = chunks[-1]
        chunks[-1] = TranslationChunk(last.text, final_joiner)
    return chunks


def plan_chunks(text: str, *, max_chars: int) -> list[TranslationChunk]:
    """Split translation input into hard-bounded chunks while preserving paragraph joins."""

    payload = str(text or "").strip()
    if not payload:
        return []

    parts: list[TranslationChunk] = []
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", payload) if str(part or "").strip()]
    for index, para in enumerate(paragraphs):
        para = para.strip()
        if not para:
            continue
        joiner = "\n\n" if index < len(paragraphs) - 1 else ""
        parts.extend(_split_paragraph(para, max_chars=max_chars, final_joiner=joiner))

    return parts


def stitch_chunks(chunks: list[TranslationChunk], translated_parts: list[str]) -> str:
    """Reassemble translated chunk outputs with preserved paragraph semantics."""

    if not chunks or not translated_parts:
        return ""

    pieces: list[str] = []
    for index, chunk in enumerate(chunks):
        translated = str(translated_parts[index] if index < len(translated_parts) else "").strip()
        if not translated:
            continue
        if pieces:
            pieces.append(str(chunks[index - 1].joiner or ""))
        pieces.append(translated)
    return "".join(pieces).strip()
