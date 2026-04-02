# app/model/translation/service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.model.core.domain.errors import OperationCancelled
from app.model.core.utils.progress_utils import progress_pct_from_budget
from app.model.core.utils.text_stitching import stitch_texts
from app.model.engines.contracts import TranslationEngineProtocol
from app.model.translation.chunking import TranslationChunk, plan_chunks, stitch_chunks
from app.model.translation.errors import TranslationError
from app.model.translation.runtime_request import (
    TranslationRuntimeConfig,
    build_translation_request,
    resolve_translation_runtime_config,
)

ProgressFn = Callable[[int], None]
CancelCheckFn = Callable[[], bool]


@dataclass(frozen=True)
class SegmentTranslationResult:
    """Translated timestamped segments plus a plain-text stitched version."""

    plain_text: str
    segments: list[dict[str, Any]]


class TranslationService:
    """Chunk-aware translation orchestration shared by batch and live workflows."""

    def __init__(
        self,
        *,
        translation_engine: TranslationEngineProtocol,
        runtime: TranslationRuntimeConfig | None = None,
    ) -> None:
        self._translation_engine = translation_engine
        self._runtime = runtime

    def _runtime_config(self) -> TranslationRuntimeConfig:
        runtime = self._runtime
        if runtime is None:
            runtime = resolve_translation_runtime_config()
            self._runtime = runtime
        return runtime

    def _translate_chunk_plan(
        self,
        *,
        chunks: list[TranslationChunk],
        src_lang: str,
        tgt_lang: str,
        runtime: TranslationRuntimeConfig,
        cancel_check: CancelCheckFn,
        progress_cb: ProgressFn | None,
        completed: int,
        total: int,
    ) -> tuple[str, int]:
        translated_parts: list[str] = []
        current_completed = int(completed)

        for chunk in chunks:
            if cancel_check():
                raise OperationCancelled()

            translated = self._translation_engine.translate_text(
                build_translation_request(
                    text=chunk.text,
                    src_lang=src_lang,
                    tgt_lang=tgt_lang,
                    runtime=runtime,
                ),
                cancel_check=cancel_check,
            )
            translated = str(translated or "").strip()
            if not translated:
                raise TranslationError("error.translation.empty_result")

            translated_parts.append(translated)
            current_completed += max(1, len(chunk.text))
            if progress_cb is not None:
                progress_cb(progress_pct_from_budget(completed=current_completed, total=total))

        return stitch_chunks(chunks, translated_parts), current_completed

    def translate_text(
        self,
        *,
        text: str,
        src_lang: str,
        tgt_lang: str,
        cancel_check: CancelCheckFn,
        progress_cb: ProgressFn | None = None,
    ) -> str:
        payload = str(text or "").strip()
        if not payload:
            return ""

        runtime = self._runtime_config()
        chunks = plan_chunks(payload, max_chars=runtime.chunk_max_chars)
        total_budget = sum(max(1, len(chunk.text)) for chunk in chunks)
        translated_text, _completed = self._translate_chunk_plan(
            chunks=chunks,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            runtime=runtime,
            cancel_check=cancel_check,
            progress_cb=progress_cb,
            completed=0,
            total=total_budget,
        )
        return translated_text

    def translate_segments(
        self,
        *,
        segments: list[dict[str, Any]],
        src_lang: str,
        tgt_lang: str,
        cancel_check: CancelCheckFn,
        progress_cb: ProgressFn | None = None,
    ) -> SegmentTranslationResult:
        runtime = self._runtime_config()
        planned: list[tuple[dict[str, object], list[TranslationChunk]]] = []
        total_budget = 0

        for segment in list(segments or []):
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            chunk_plan = plan_chunks(text, max_chars=runtime.chunk_max_chars)
            planned.append((dict(segment or {}), chunk_plan))
            total_budget += sum(max(1, len(chunk.text)) for chunk in chunk_plan)

        completed = 0
        translated_segments: list[dict[str, Any]] = []
        for segment, chunk_plan in planned:
            translated_text, completed = self._translate_chunk_plan(
                chunks=chunk_plan,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                runtime=runtime,
                cancel_check=cancel_check,
                progress_cb=progress_cb,
                completed=completed,
                total=total_budget,
            )
            translated_segments.append(
                {
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "text": translated_text,
                }
            )

        plain_text = stitch_texts(str(item.get("text") or "") for item in translated_segments)
        return SegmentTranslationResult(plain_text=plain_text, segments=translated_segments)
