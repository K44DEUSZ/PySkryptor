# core/io/audio_extractor.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from core.config.app_config import AppConfig as Config


def _ffbin(name: str) -> Path:
    exe = f"{name}.exe" if name in ("ffmpeg", "ffprobe") and (Path().anchor and Config.FFMPEG_BIN_DIR) and (str(Config.FFMPEG_BIN_DIR).lower().endswith("\\bin") or True) and (Path().anchor) else name
    p = Config.FFMPEG_BIN_DIR / exe
    return p if p.exists() else Path(name)


class AudioExtractor:
    """FFmpeg audio utilities."""

    @staticmethod
    def has_audio(path: Path) -> bool:
        ffprobe = _ffbin("ffprobe")
        try:
            result = subprocess.run(
                [str(ffprobe), "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True,
            )
            return "audio" in result.stdout
        except Exception:
            return False

    @staticmethod
    def ensure_mono_16k(src: Path, dst: Path, log: Callable[[str], None] = print) -> None:
        """Transcode to 16kHz mono WAV if needed."""
        ffmpeg = _ffbin("ffmpeg")
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(ffmpeg),
            "-y",
            "-i",
            str(src),
            "-ac", "1",
            "-ar", "16000",
            "-vn",
            "-c:a", "pcm_s16le",
            str(dst),
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            log(f"🎛️ Przygotowano audio: {dst.name}")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg error: {e}")
