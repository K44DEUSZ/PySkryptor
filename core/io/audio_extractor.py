# core/io/audio_extractor.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from core.config.app_config import AppConfig as Config


class AudioExtractor:
    """FFmpeg-based helpers for audio preparation and probing."""

    @staticmethod
    def _ffmpeg_bin() -> str:
        # Try configured binary, fallback to PATH
        for attr in ("FFMPEG_PATH", "FFMPEG_BIN", "FFMPEG"):
            p = getattr(Config, attr, None)
            if p:
                return str(p)
        return "ffmpeg"

    @staticmethod
    def _ffprobe_bin() -> str:
        for attr in ("FFPROBE_PATH", "FFPROBE_BIN", "FFPROBE"):
            p = getattr(Config, attr, None)
            if p:
                return str(p)
        # Some bundles ship ffprobe next to ffmpeg
        ffmpeg = AudioExtractor._ffmpeg_bin()
        if "ffmpeg" in ffmpeg:
            candidate = ffmpeg.replace("ffmpeg", "ffprobe")
            return candidate
        return "ffprobe"

    @staticmethod
    def ensure_mono_16k(src: Path, dst: Path, log=print) -> None:
        """
        Convert any media to wav PCM 16kHz mono suitable for Whisper.
        Overwrite if exists.
        """
        cmd = [
            AudioExtractor._ffmpeg_bin(),
            "-y",
            "-i",
            str(src),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-vn",
            "-f",
            "wav",
            str(dst),
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log(str(dst))
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg failed: {e}") from e

    @staticmethod
    def probe_duration(path: Path) -> Optional[float]:
        """
        Return media duration in seconds (float) using ffprobe, or None if unavailable.
        """
        cmd = [
            AudioExtractor._ffprobe_bin(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            txt = (proc.stdout or "").strip()
            if not txt:
                return None
            return float(txt)
        except Exception:
            return None
