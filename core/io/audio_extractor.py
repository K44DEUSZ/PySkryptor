# core/io/audio_extractor.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Callable

from core.config.app_config import AppConfig as Config


class AudioError(RuntimeError):
    """Audio error carrying a translation key + params (UI will localize)."""

    def __init__(self, key: str, **params) -> None:
        self.key = key
        self.params = params
        super().__init__(key)


class AudioExtractor:
    """FFmpeg-based helpers for audio preparation and probing."""

    @staticmethod
    def _ffmpeg_exe() -> str:
        """Return absolute ffmpeg executable path or name on PATH."""
        base = Config.FFMPEG_BIN_DIR
        exe = "ffmpeg.exe" if Path().anchor or (hasattr(Path, "home") and Path.home().drive) else "ffmpeg"
        cand = base / exe
        return str(cand) if cand.exists() else "ffmpeg"


    @staticmethod
    def _ffprobe_exe() -> str:
        """Return absolute ffprobe executable path or name on PATH."""
        base = Config.FFMPEG_BIN_DIR
        exe = "ffprobe.exe" if Path().anchor or (hasattr(Path, "home") and Path.home().drive) else "ffprobe"
        cand = base / exe
        return str(cand) if cand.exists() else "ffprobe"


    @staticmethod
    def ensure_mono_16k(src: Path, dst: Path, log: Optional[Callable[[str], None]]) -> None:
        """
        Convert any media to WAV PCM 16kHz mono (overwrite if exists).
        Raises AudioError with i18n key on failure.
        """
        cmd = [
            AudioExtractor._ffmpeg_exe(),
            "-y",
            "-i",
            str(src),
            "-ar", "16000",
            "-ac", "1",
            "-vn",
            "-f", "wav",
            str(dst),
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            raise AudioError("error.audio.ffmpeg_failed", detail=str(e), src=str(src))


    @staticmethod
    def probe_duration(path: Path) -> Optional[float]:
        """
        Return media duration in seconds using ffprobe.
        Returns None if probing fails (non-fatal).
        """
        cmd = [
            AudioExtractor._ffprobe_exe(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            txt = (proc.stdout or "").strip()
            return float(txt) if txt else None
        except Exception:
            return None
