# pyskryptor/core/io/microphone.py
from __future__ import annotations

from typing import Iterable


class MicrophoneSource:
    """Stub microphone source for future live STT."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def frames(self) -> Iterable[bytes]:
        if False:
            yield b""
