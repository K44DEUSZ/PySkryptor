# app/model/engines/contracts.py
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from app.model.engines.types import (
    EngineHealth,
    RecognizeAudioRequest,
    RecognizeAudioResult,
    TranscribeWavRequest,
    TranscribeWavResult,
    TranslateTextRequest,
)


@runtime_checkable
class EngineHandleProtocol(Protocol):
    """Shared lifecycle contract exposed by all engine handles."""

    @property
    def role(self) -> str: ...

    def warmup(self) -> None: ...

    def health(self) -> EngineHealth: ...

    def shutdown(self) -> None: ...


@runtime_checkable
class TranscriptionEngineProtocol(EngineHandleProtocol, Protocol):
    """Batch and live ASR contract exposed to the application layer."""

    def transcribe_wav(
        self,
        request: TranscribeWavRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
        progress_cb: Callable[[int], None] | None = None,
    ) -> TranscribeWavResult: ...

    def recognize_audio(
        self,
        request: RecognizeAudioRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> RecognizeAudioResult: ...


@runtime_checkable
class TranslationEngineProtocol(EngineHandleProtocol, Protocol):
    """Text translation contract exposed to the application layer."""

    def translate_text(
        self,
        request: TranslateTextRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str: ...
