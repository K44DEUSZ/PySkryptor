# core/contracts/audio_source.py
from __future__ import annotations

from typing import Iterable, Protocol


class AudioSource(Protocol):
    """
    Live audio input contract.

    start() / stop() control capture; frames() yields raw PCM chunks (bytes).
    """

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def frames(self) -> Iterable[bytes]: ...
