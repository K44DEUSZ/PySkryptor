# core/services/transcription_service.py
from __future__ import annotations

from typing import Callable, Optional, Any

from core.transcription.model_loader import ModelLoader


class TranscriptionService:
    """Facade for ASR pipeline build."""

    def __init__(self, backend: Optional[ModelLoader] = None) -> None:
        self._loader: ModelLoader = backend or ModelLoader()
        self._pipe: Optional[Any] = None

    @property
    def pipeline(self) -> Any:
        return self._pipe

    def build(self, log: Callable[[str], None]) -> None:
        self._loader.load(log=log)
        self._pipe = self._loader.pipeline
