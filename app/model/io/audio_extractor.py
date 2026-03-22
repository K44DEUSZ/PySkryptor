# app/model/io/audio_extractor.py
from __future__ import annotations

import json
import logging
import os
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Iterable

from app.model.config.app_config import AppConfig as Config
from app.model.domain.errors import AppError, OperationCancelled
from app.model.infrastructure.external_tools import CommandRunner

_LOG = logging.getLogger(__name__)

class AudioError(AppError):
    """Audio error carrying a translation key + params (UI will localize)."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))

class AudioExtractor:
    """FFmpeg-based helpers for audio preparation and probing."""

    @staticmethod
    def _tool_exe(name: str) -> str:
        """Return absolute tool executable path or fallback to the PATH name."""
        base = Config.FFMPEG_BIN_DIR
        tool = str(name or "").strip()
        if not tool:
            return ""
        exe = f"{tool}.exe" if os.name == "nt" else tool
        cand = base / exe
        return str(cand) if cand.exists() else exe

    @staticmethod
    def _raise_ffmpeg_error(ex: AppError, *, src: Path, dst: Path) -> None:
        if str(getattr(ex, "key", "")) == "error.cancelled":
            raise OperationCancelled()

        params = dict(getattr(ex, "params", {}) or {})
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
            AudioExtractor._tool_exe("ffmpeg"),
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
    def convert_audio(
        src: Path,
        dst: Path,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        """Convert audio to a target container/codec based on dst suffix."""
        AudioExtractor._run_ffmpeg(
            src,
            dst,
            args=("-vn",),
            cancel_check=cancel_check,
            log_label="convert",
        )

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
                str(Config.ASR_SAMPLE_RATE),
                "-ac",
                str(Config.ASR_CHANNELS),
                "-vn",
                "-f",
                str(Config.ASR_WAV_FORMAT_TOKEN),
            ),
            cancel_check=cancel_check,
            log_label="mono 16k",
        )

    @staticmethod
    def probe_audio_stream(path: Path) -> dict[str, Any]:
        """Return basic audio-stream metadata from ffprobe; empty dict on failure."""
        cmd = [
            AudioExtractor._tool_exe("ffprobe"),
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
            res = CommandRunner.run(cmd, timeout_s=Config.AUDIO_PROBE_TIMEOUT_S, error_key="error.audio.ffprobe_failed")
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

        if Config.ASR_WAV_FORMAT_TOKEN not in format_name:
            return False
        if not codec_name.startswith(Config.ASR_WAV_CODEC_PREFIX):
            return False
        if sample_rate != Config.ASR_SAMPLE_RATE:
            return False
        if channels != Config.ASR_CHANNELS:
            return False
        return True

    @staticmethod
    def probe_duration(path: Path) -> float | None:
        """Return media duration in seconds using ffprobe."""
        cmd = [
            AudioExtractor._tool_exe("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]

        try:
            res = CommandRunner.run(cmd, timeout_s=Config.AUDIO_PROBE_TIMEOUT_S, error_key="error.audio.ffprobe_failed")
            out = str(res.stdout or "").strip()
            return float(out) if out else None
        except (AppError, TypeError, ValueError):
            return None
