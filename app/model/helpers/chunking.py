# app/model/helpers/chunking.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Tuple

import numpy as np
import wave


def normalize_chunk_params(chunk_len_s: int, stride_len_s: int) -> Tuple[int, int, int]:
    chunk = max(1, int(chunk_len_s))
    stride = max(0, int(stride_len_s))
    if stride >= chunk:
        stride = max(0, chunk - 1)
    step = max(1, chunk - stride)
    return chunk, stride, step


def seconds_to_frames(sr: int, chunk_len_s: int, stride_len_s: int) -> Tuple[int, int, int]:
    chunk_s, stride_s, step_s = normalize_chunk_params(chunk_len_s, stride_len_s)
    sr_i = max(1, int(sr))
    chunk_f = max(1, int(chunk_s * sr_i))
    stride_f = max(0, int(stride_s * sr_i))
    if stride_f >= chunk_f:
        stride_f = max(0, chunk_f - 1)
    step_f = max(1, chunk_f - stride_f)
    return chunk_f, stride_f, step_f


def pcm16le_bytes_to_float32(data: bytes) -> np.ndarray:
    """Convert little-endian PCM16 mono bytes into float32 [-1, 1]."""
    if not data:
        return np.zeros((0,), dtype=np.float32)
    arr = np.frombuffer(data, dtype="<i2").astype(np.float32)
    return arr / 32768.0


def estimate_chunks(total_dur_s: float, chunk_len_s: int, stride_len_s: int) -> int:
    try:
        dur = float(total_dur_s)
    except Exception:
        dur = 0.0
    if dur <= 0:
        return 1

    chunk_s, stride_s, step_s = normalize_chunk_params(chunk_len_s, stride_len_s)
    step = float(step_s)
    n = int(np.ceil(dur / step))
    return max(1, n)


def _pcm_bytes_to_float32(frames: bytes, sampwidth: int) -> np.ndarray:
    if not frames:
        return np.zeros((0,), dtype=np.float32)

    sw = int(sampwidth)

    if sw == 1:
        # 8-bit PCM is unsigned [0..255] -> centered [-128..127]
        a = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        return (a - 128.0) / 128.0

    if sw == 2:
        a = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
        return a / 32768.0

    if sw == 3:
        # 24-bit little-endian signed PCM: 3 bytes per sample.
        b = np.frombuffer(frames, dtype=np.uint8)
        if b.size % 3 != 0:
            b = b[: b.size - (b.size % 3)]
        if b.size == 0:
            return np.zeros((0,), dtype=np.float32)

        b = b.reshape(-1, 3)
        x = (b[:, 0].astype(np.int32) |
             (b[:, 1].astype(np.int32) << 8) |
             (b[:, 2].astype(np.int32) << 16))
        # Sign-extend from 24-bit
        x = (x << 8) >> 8
        return x.astype(np.float32) / 8388608.0  # 2^23

    if sw == 4:
        a = np.frombuffer(frames, dtype=np.int32).astype(np.float32)
        return a / 2147483648.0  # 2^31

    raise ValueError(f"unsupported-sample-width; got {sw}")


@dataclass(frozen=True)
class WavChunk:
    idx: int
    n_chunks: int
    offset_s: float
    audio: np.ndarray
    sr: int


def iter_wav_mono_chunks(
    wav_path: Path,
    *,
    chunk_len_s: int,
    stride_len_s: int,
) -> Iterator[WavChunk]:
    chunk_s, stride_s, step_s = normalize_chunk_params(chunk_len_s, stride_len_s)

    with wave.open(str(wav_path), "rb") as wf:
        n_channels = int(wf.getnchannels() or 0)
        if n_channels != 1:
            raise ValueError(f"expected-mono-wav; channels={n_channels}")

        sr = int(wf.getframerate() or 0)
        if sr <= 0:
            raise ValueError(f"invalid-sample-rate; sr={sr}")

        sampwidth = int(wf.getsampwidth() or 0)
        if sampwidth <= 0:
            raise ValueError(f"invalid-sample-width; sampwidth={sampwidth}")

        n_frames = int(wf.getnframes() or 0)
        duration_s = float(n_frames) / float(sr) if n_frames > 0 else 0.0

        n_chunks = estimate_chunks(duration_s, chunk_s, stride_s)
        chunk_frames, stride_frames, step_frames = seconds_to_frames(sr, chunk_s, stride_s)

        for i in range(n_chunks):
            start = int(i * step_frames)
            if start >= n_frames and n_frames > 0:
                break

            wf.setpos(min(max(0, start), max(0, n_frames)))
            raw = wf.readframes(chunk_frames)

            audio = _pcm_bytes_to_float32(raw, sampwidth)
            offset_s = float(start) / float(sr)

            yield WavChunk(
                idx=i,
                n_chunks=n_chunks,
                offset_s=offset_s,
                audio=audio,
                sr=sr,
            )
