# core/files/file_manager.py
from __future__ import annotations

from pathlib import Path
import shutil
from datetime import datetime

from core.config.app_config import AppConfig as Config
from core.io.audio_extractor import AudioExtractor
from core.utils.text import sanitize_filename


class FileManager:
    """Centralized file operations for transcription I/O and naming."""
    _session_dir: Path | None = None
    _session_created: bool = False

    # ----- Session (group output by datetime folder) -----

    @staticmethod
    def plan_session() -> Path:
        """
        Prepare a timestamped session path inside TRANSCRIPTIONS_DIR but do NOT create it yet.
        The directory will be created lazily on first write.
        """
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base = Config.TRANSCRIPTIONS_DIR / stamp
        FileManager._session_dir = base
        FileManager._session_created = False
        return base

    @staticmethod
    def ensure_session() -> Path:
        """
        Ensure the planned session directory exists (create once, lazily).
        """
        if FileManager._session_dir is None:
            FileManager.plan_session()
        assert FileManager._session_dir is not None
        if not FileManager._session_created:
            FileManager._session_dir.mkdir(parents=True, exist_ok=True)
            FileManager._session_created = True
        return FileManager._session_dir

    @staticmethod
    def rollback_session_if_empty() -> None:
        """
        Remove the session directory if it exists and is empty.
        """
        sess = FileManager._session_dir
        if not sess:
            return
        if sess.exists() and sess.is_dir():
            try:
                # Remove only if empty
                next(sess.iterdir())
            except StopIteration:
                shutil.rmtree(sess, ignore_errors=True)

    @staticmethod
    def end_session() -> None:
        """Clear current session context (does not delete any data)."""
        FileManager._session_dir = None
        FileManager._session_created = False

    @staticmethod
    def session_dir() -> Path:
        """Return planned/active session directory path (may not exist yet)."""
        return FileManager._session_dir or Config.TRANSCRIPTIONS_DIR

    # ----- Cross-session conflict lookup -----

    @staticmethod
    def find_existing_output(stem: str) -> Path | None:
        """
        Return an existing output directory for given stem if it exists
        in *any* previous session under TRANSCRIPTIONS_DIR.
        """
        safe = sanitize_filename(stem)
        root = Config.TRANSCRIPTIONS_DIR
        # Check direct child (legacy layout)
        direct = root / safe
        if direct.exists():
            return direct
        # Check any session subfolder
        for sess in root.iterdir():
            if not sess.is_dir():
                continue
            candidate = sess / safe
            if candidate.exists():
                return candidate
        return None

    # ----- Output helpers -----

    @staticmethod
    def output_dir_for(stem: str) -> Path:
        """Return target directory for a given logical item name (sanitized) inside session dir."""
        safe = sanitize_filename(stem)
        return FileManager.session_dir() / safe

    @staticmethod
    def ensure_output(stem: str) -> Path:
        """Ensure the output directory exists for given stem and return it."""
        # Ensure session exists before creating item directory
        FileManager.ensure_session()
        out_dir = FileManager.output_dir_for(stem)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    @staticmethod
    def remove_dir_if_empty(path: Path) -> None:
        """Remove directory if it exists and is empty."""
        if not path.exists() or not path.is_dir():
            return
        try:
            next(path.iterdir())
        except StopIteration:
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def ensure_tmp_wav(source: Path, log=print) -> Path:
        """
        Ensure 16 kHz mono WAV in INPUT_TMP_DIR for Whisper.
        If source is video → extract audio; if audio but wrong params → transcode.
        """
        target = Config.INPUT_TMP_DIR / (source.stem + ".wav")
        target.parent.mkdir(parents=True, exist_ok=True)
        AudioExtractor.ensure_mono_16k(source, target, log=log)
        return target

    @staticmethod
    def transcript_path(stem: str, filename: str = "transcript.txt") -> Path:
        """Return full path for transcript text file within item's output folder."""
        out_dir = FileManager.output_dir_for(stem)
        return out_dir / filename

    @staticmethod
    def copy_to_downloads(src: Path) -> Path:
        """Copy a file into downloads dir (no-op if already there)."""
        dst = Config.DOWNLOADS_DIR / src.name
        if src.resolve() == dst.resolve():
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return dst
