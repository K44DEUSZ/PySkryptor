# pyskryptor/core/services/transcription_service.py
from __future__ import annotations

from typing import Callable, Optional

from core.contracts.transcriber import Transcriber as TranscriberProtocol
from core.transcription.model_loader import ModelLoader


class TranscriptionService:
    """Facade for ASR pipeline build."""

    def __init__(self, backend: Optional[TranscriberProtocol] = None) -> None:
        self._loader = backend or ModelLoader()
        self._pipe = None

    @property
    def pipeline(self):
        return self._pipe

    def build(self, log: Callable[[str], None]) -> None:
        self._loader.load(log=log)
        self._pipe = self._loader.pipeline
