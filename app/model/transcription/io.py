# app/model/transcription/io.py
from __future__ import annotations

import json
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Iterable

from app.model.core.config.config import AppConfig
from app.model.core.domain.errors import AppError, OperationCancelled
from app.model.core.infrastructure.command_runner import CommandRunner
from app.model.core.runtime.ffmpeg import resolve_ffmpeg_tool

_LOG = logging.getLogger(__name__)


class AudioError(AppError):
    """Audio error carrying a translation key + params (UI will localize)."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


class AudioExtractor:
    """FFmpeg-based helpers for audio preparation and probing."""

    @staticmethod
    def _raise_ffmpeg_error(ex: AppError, *, src: Path, dst: Path) -> None:
        if str(ex.key) == "error.cancelled":
            raise OperationCancelled()

        params = dict(ex.params or {})
        params.setdefault("src", str(src))
        params.setdefault("dst", str(dst))
        if "detail" not in params:
            stderr = str(params.get("stderr") or "").strip()
            code = params.get("code")
            if stderr:
                params["detail"] = stderr
            elif code is not None:
                params["detail"] = f"ffmpeg exit={code}"
            else:
                params["detail"] = "ffmpeg failed"
        raise AudioError("error.audio.ffmpeg_failed", **params)

    @staticmethod
    def _run_ffmpeg(
        src: Path,
        dst: Path,
        *,
        args: Iterable[str],
        cancel_check: Callable[[], bool] | None = None,
        log_label: str,
    ) -> None:
        cmd = [
            resolve_ffmpeg_tool(AppConfig, "ffmpeg"),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            *[str(arg) for arg in args],
            str(dst),
        ]

        _LOG.debug("Running FFmpeg %s command. cmd=%s", log_label, " ".join(cmd))

        try:
            CommandRunner.run(
                cmd,
                cancel_check=cancel_check,
                error_key="error.audio.ffmpeg_failed",
            )
        except AppError as ex:
            AudioExtractor._raise_ffmpeg_error(ex, src=src, dst=dst)


    @staticmethod
    def ensure_mono_16k(
        src: Path,
        dst: Path,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        """Convert any media to WAV PCM 16kHz mono (overwrite if exists)."""
        AudioExtractor._run_ffmpeg(
            src,
            dst,
            args=(
                "-ar",
                str(AppConfig.ASR_SAMPLE_RATE),
                "-ac",
                str(AppConfig.ASR_CHANNELS),
                "-vn",
                "-f",
                str(AppConfig.ASR_WAV_FORMAT_TOKEN),
            ),
            cancel_check=cancel_check,
            log_label="mono 16k",
        )

    @staticmethod
    def probe_audio_stream(path: Path) -> dict[str, Any]:
        """Return basic audio-stream metadata from ffprobe; empty dict on failure."""
        cmd = [
            resolve_ffmpeg_tool(AppConfig, "ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-show_entries",
            "format=format_name",
            "-of",
            "json",
            str(path),
        ]

        try:
            res = CommandRunner.run(
                cmd,
                timeout_s=AppConfig.AUDIO_PROBE_TIMEOUT_S,
                error_key="error.audio.ffprobe_failed",
            )
            payload = json.loads(str(res.stdout or "{}") or "{}")
        except (AppError, JSONDecodeError, TypeError, ValueError):
            return {}

        streams = payload.get("streams") or []
        stream = streams[0] if isinstance(streams, list) and streams else {}
        fmt = payload.get("format") or {}

        try:
            sample_rate = int(stream.get("sample_rate") or 0)
        except (TypeError, ValueError):
            sample_rate = 0
        try:
            channels = int(stream.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0

        return {
            "codec_name": str(stream.get("codec_name") or "").strip().lower(),
            "sample_rate": sample_rate,
            "channels": channels,
            "format_name": str(fmt.get("format_name") or "").strip().lower(),
        }

    @staticmethod
    def is_wav_mono_16k(path: Path) -> bool:
        """Return True when media is already a mono 16k WAV suitable for ASR input."""
        meta = AudioExtractor.probe_audio_stream(path)
        if not meta:
            return False

        format_name = str(meta.get("format_name") or "")
        codec_name = str(meta.get("codec_name") or "")
        sample_rate = int(meta.get("sample_rate") or 0)
        channels = int(meta.get("channels") or 0)

        if AppConfig.ASR_WAV_FORMAT_TOKEN not in format_name:
            return False
        if not codec_name.startswith(AppConfig.ASR_WAV_CODEC_PREFIX):
            return False
        if sample_rate != AppConfig.ASR_SAMPLE_RATE:
            return False
        if channels != AppConfig.ASR_CHANNELS:
            return False
        return True

    @staticmethod
    def probe_duration(path: Path) -> float | None:
        """Return media duration in seconds using ffprobe."""
        cmd = [
            resolve_ffmpeg_tool(AppConfig, "ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]

        try:
            res = CommandRunner.run(
                cmd,
                timeout_s=AppConfig.AUDIO_PROBE_TIMEOUT_S,
                error_key="error.audio.ffprobe_failed",
            )
            out = str(res.stdout or "").strip()
            return float(out) if out else None
        except (AppError, TypeError, ValueError):
            return None
