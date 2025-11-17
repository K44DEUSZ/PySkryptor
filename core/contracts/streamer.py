# core/contracts/streamer.py
from __future__ import annotations

from typing import Protocol


class StreamingController(Protocol):
    """
    Incremental transcription contract.

    push(frame) feeds audio; partial_text() returns best current hypothesis;
    finalize() returns the final transcript.
    """

    def push(self, frame: bytes) -> None: ...
    def partial_text(self) -> str: ...
    def finalize(self) -> str: ...
