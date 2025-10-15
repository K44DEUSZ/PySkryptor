# pyskryptor/core/transcription/model_loader.py
from __future__ import annotations

from typing import Callable, Optional

from core.transcription.pipeline import WhisperPipeline


class ModelLoader:
    """Builds ASR pipeline using WhisperPipeline."""

    def __init__(self, model_dir: Optional[str] = None) -> None:
        self._pipe = None
        self._model_dir = model_dir

    @property
    def pipeline(self):
        return self._pipe

    def load(self, log: Callable[[str], None]) -> None:
        wp = WhisperPipeline(model_dir=self._model_dir)
        wp.build(log=log)
        self._pipe = wp.pipeline
