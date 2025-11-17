# core/io/audio_extractor.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from core.config.app_config import AppConfig as Config


class AudioExtractor:
    """FFmpeg-based helpers for audio preparation and probing."""

    @staticmethod
    def _bin(name: str) -> str:
        """
        Resolve executable path from bundled ffmpeg bin dir, fallback to plain name in PATH.
        """
        exe = f"{name}.exe" if Path().anchor and (Path().anchor != "/") else name
        candidate = Config.FFMPEG_BIN_DIR / exe
        return str(candidate) if candidate.exists() else name

    @staticmethod
    def _ffmpeg_bin() -> str:
        return AudioExtractor._bin("ffmpeg")

    @staticmethod
    def _ffprobe_bin() -> str:
        return AudioExtractor._bin("ffprobe")

    @staticmethod
    def ensure_mono_16k(src: Path, dst: Path, log=print, verbose: bool = False) -> None:
        """
        Convert any media to WAV PCM 16 kHz mono suitable for Whisper. Overwrites if exists.
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
            if verbose:
                subprocess.run(cmd, check=True)
            else:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log(str(dst))
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg failed: {e}") from e

    @staticmethod
    def probe_duration(path: Path) -> Optional[float]:
        """
        Return media duration in seconds using ffprobe, or None if unavailable.
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
