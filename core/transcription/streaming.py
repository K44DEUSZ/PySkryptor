# pyskryptor/core/transcription/streaming.py
from __future__ import annotations


class StreamingTranscription:
    """Stub for future streaming transcription controller."""

    def __init__(self) -> None:
        self._partial = ""

    def push(self, frame: bytes) -> None:
        pass

    def partial_text(self) -> str:
        return self._partial

    def finalize(self) -> str:
        return ""
