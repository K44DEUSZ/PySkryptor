# app/model/engines/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TranscribeWavRequest:
    """Batch ASR request executed against the transcription engine host."""

    wav_path: str
    key: str
    chunk_length_s: int
    stride_length_s: int
    want_timestamps: bool
    ignore_warning: bool
    require_language: bool
    source_language: str = ""
    runtime_profile: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "wav_path": str(self.wav_path),
            "key": str(self.key),
            "chunk_length_s": int(self.chunk_length_s),
            "stride_length_s": int(self.stride_length_s),
            "want_timestamps": bool(self.want_timestamps),
            "ignore_warning": bool(self.ignore_warning),
            "require_language": bool(self.require_language),
            "source_language": str(self.source_language or ""),
            "runtime_profile": dict(self.runtime_profile or {}),
        }


@dataclass(frozen=True)
class RecognizeAudioRequest:
    """Live ASR request executed against the transcription engine host."""

    audio_b64: str
    sample_rate: int
    source_language: str
    ignore_warning: bool
    signal_kind: str
    previous_text: str = ""
    require_language: bool = False
    runtime_profile: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "audio_b64": str(self.audio_b64 or ""),
            "sample_rate": int(self.sample_rate),
            "source_language": str(self.source_language or ""),
            "ignore_warning": bool(self.ignore_warning),
            "signal_kind": str(self.signal_kind or ""),
            "previous_text": str(self.previous_text or ""),
            "require_language": bool(self.require_language),
            "runtime_profile": dict(self.runtime_profile or {}),
        }


@dataclass(frozen=True)
class TranslateTextRequest:
    """Translation request executed against the translation engine host."""

    text: str
    src_lang: str
    tgt_lang: str
    model_ref: str
    device: str
    dtype: str
    low_cpu_mem_usage: bool
    max_new_tokens: int
    num_beams: int
    no_repeat_ngram_size: int

    def payload(self) -> dict[str, Any]:
        return {
            "text": str(self.text or ""),
            "src_lang": str(self.src_lang or ""),
            "tgt_lang": str(self.tgt_lang or ""),
            "model_ref": str(self.model_ref or ""),
            "device": str(self.device or ""),
            "dtype": str(self.dtype or ""),
            "low_cpu_mem_usage": bool(self.low_cpu_mem_usage),
            "max_new_tokens": int(self.max_new_tokens),
            "num_beams": int(self.num_beams),
            "no_repeat_ngram_size": int(self.no_repeat_ngram_size),
        }


@dataclass(frozen=True)
class EngineHealth:
    """Normalized health snapshot returned by an engine host."""

    role: str
    ready: bool
    pid: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscribeWavResult:
    """Normalized batch ASR response."""

    merged_text: str
    segments: list[dict[str, Any]]
    detected_language: str


@dataclass(frozen=True)
class RecognizeAudioResult:
    """Normalized live ASR response."""

    text: str
    detected_language: str


@dataclass(frozen=True)
class EngineRuntimeState:
    """Runtime readiness state for a single engine role."""

    ready: bool = False
    error_key: str | None = None
    error_params: dict[str, Any] = field(default_factory=dict)
