# core/io/file_manager.py
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.config.app_config import AppConfig as Config
from core.io.audio_extractor import AudioExtractor
from core.io.text import sanitize_filename


class FileManager:
    """Filesystem helpers for inputs, downloads, session outputs and transcripts."""

    @staticmethod
    def project_root() -> Path:
        return Config.ROOT_DIR

    @staticmethod
    def downloads_dir() -> Path:
        return Config.DOWNLOADS_DIR

    @staticmethod
    def transcriptions_dir() -> Path:
        return Config.TRANSCRIPTIONS_DIR

    @staticmethod
    def output_dir_for(stem: str) -> Path:
        safe = sanitize_filename(stem) or "item"
        return Config.TRANSCRIPTIONS_DIR / safe

    @staticmethod
    def ensure_output(stem: str) -> Path:
        p = FileManager.output_dir_for(stem)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def find_existing_output(stem: str) -> Optional[Path]:
        p = FileManager.output_dir_for(stem)
        return p if p.exists() else None

    @staticmethod
    def plan_session() -> Path:
        """
        Plan a new session output folder name.

        We don't create it immediately; it's created on first actual write.
        """
        base = Config.TRANSCRIPTIONS_DIR / "session"
        if not base.exists():
            return base
        i = 1
        while True:
            cand = Config.TRANSCRIPTIONS_DIR / f"session ({i})"
            if not cand.exists():
                return cand
            i += 1

    @staticmethod
    def clear_temp_dir(path: Path) -> None:
        """Remove temp dir if exists; ignore errors."""
        shutil.rmtree(path, ignore_errors=True)

        try:
            next(path.iterdir())
        except StopIteration:
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def ensure_tmp_wav(
        source: Path,
        log=print,
        *,
        cancel_check=None,
    ) -> Path:
        """
        Return a path suitable for chunked transcription.

        - If 'source' is a WAV file → return it as-is (wave module can read it).
        - Otherwise create a 16 kHz mono WAV copy in INPUT_TMP_DIR and return that path.
        """
        if source.suffix.lower() == ".wav":
            return source

        target = Config.INPUT_TMP_DIR / (source.stem + ".wav")
        target.parent.mkdir(parents=True, exist_ok=True)
        AudioExtractor.ensure_mono_16k(source, target, log=log, cancel_check=cancel_check)
        return target

    @staticmethod
    def transcript_path(
        stem: str,
        filename: str | None = None,
        *,
        base_name: str | None = None,
    ) -> Path:
        """
        Return full path for transcript file within item's output folder.

        Precedence:
          1) If filename is provided → use it as-is inside the item's output folder.
          2) Otherwise:
               - take default transcript extension from AppConfig (settings),
               - use provided base_name if given (typically localized from i18n),
               - fall back to "transcript" if base_name is empty or not provided.
        """
        out_dir = FileManager.output_dir_for(stem)

        if filename is not None:
            return out_dir / filename

        ext = Config.transcript_default_ext()
        raw_base = (base_name or "").strip() or "transcript"
        safe_base = sanitize_filename(raw_base) or "transcript"
        filename_auto = f"{safe_base}.{ext.lstrip('.')}"

        return out_dir / filename_auto

    @staticmethod
    def _unique_path(dst: Path) -> Path:
        """Return a unique path by appending (n) if needed."""
        if not dst.exists():
            return dst
        stem = dst.stem
        suffix = dst.suffix
        parent = dst.parent
        i = 1
        while True:
            cand = parent / f"{stem} ({i}){suffix}"
            if not cand.exists():
                return cand
            i += 1

    @staticmethod
    def copy_to_downloads(src: Path) -> Path:
        """
        Copy a file into downloads dir.
        If a file with the same name exists, create a '(n)' suffixed copy.
        """
        dst = Config.DOWNLOADS_DIR / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst = FileManager._unique_path(dst)
        if src.resolve() == dst.resolve():
            return dst
        shutil.copy2(src, dst)
        return dst