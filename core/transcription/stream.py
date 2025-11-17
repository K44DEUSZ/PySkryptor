#core/transcription/stream.py
from __future__ import annotations

from typing import Optional

from core.contracts.streamer import StreamingController


class WhisperStreamingController(StreamingController):
    """
    Minimal controller stub for future live STT.

    Responsibility:
    - accept audio chunks (push)
    - expose partial text (partial_text)
    - finalize and return full text (finalize)

    Notes:
    - Implementation is intentionally empty for now; it only defines
      a clean surface compatible with `core/contracts/streamer.py`.
    - When live STT is implemented, connect this to a streaming-capable
      ASR (chunk buffering, VAD, incremental decode, etc.).
    """

    def __init__(self) -> None:
        self._partial: str = ""
        self._final: Optional[str] = None
        self._closed: bool = False

    def push(self, frame: bytes) -> None:
        if self._closed:
            return
        # TO DO: buffer frame / run incremental decoding
        # For now just keep a placeholder marker to signal activity.
        if not self._partial:
            self._partial = "…"
        # self._partial = <incremental result>

    def partial_text(self) -> str:
        return self._partial

    def finalize(self) -> str:
        # TO DO: flush decoder, produce final text
        self._closed = True
        self._final = self._final or (self._partial or "")
        return self._final
