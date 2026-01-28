# core/io/file_manager.py
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.config.app_config import AppConfig as Config
from core.io.audio_extractor import AudioExtractor
from core.io.text import sanitize_filename


class FileManager:
    """Filesystem helpers for inputs, downloads, session outputs and transcripts."""

    _session_dir: Path | None = None
    _session_created: bool = False

    # ----- Base dirs -----

    @staticmethod
    def project_root() -> Path:
        return Config.ROOT_DIR

    @staticmethod
    def downloads_dir() -> Path:
        return Config.DOWNLOADS_DIR

    @staticmethod
    def transcriptions_dir() -> Path:
        return Config.TRANSCRIPTIONS_DIR

    # ----- Session management -----

    @staticmethod
    def plan_session() -> Path:
        """Plan a new session folder (timestamped), create lazily on first write."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        FileManager._session_dir = Config.TRANSCRIPTIONS_DIR / stamp
        FileManager._session_created = False
        return FileManager._session_dir

    @staticmethod
    def ensure_session() -> Path:
        if FileManager._session_dir is None:
            FileManager.plan_session()
        assert FileManager._session_dir is not None
        if not FileManager._session_created:
            FileManager._session_dir.mkdir(parents=True, exist_ok=True)
            FileManager._session_created = True
        return FileManager._session_dir

    @staticmethod
    def session_dir() -> Path:
        """Return current planned/active session dir or TRANSCRIPTIONS_DIR fallback."""
        return FileManager._session_dir or Config.TRANSCRIPTIONS_DIR

    @staticmethod
    def end_session() -> None:
        FileManager._session_dir = None
        FileManager._session_created = False

    @staticmethod
    def rollback_session_if_empty() -> None:
        sess = FileManager._session_dir
        if not sess or not sess.exists() or not sess.is_dir():
            return
        try:
            next(sess.iterdir())
        except StopIteration:
            shutil.rmtree(sess, ignore_errors=True)

    # ----- Outputs -----

    @staticmethod
    def output_dir_for(stem: str) -> Path:
        safe = sanitize_filename(stem) or "item"
        return FileManager.session_dir() / safe

    @staticmethod
    def ensure_output(stem: str) -> Path:
        FileManager.ensure_session()
        p = FileManager.output_dir_for(stem)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def find_existing_output(stem: str) -> Optional[Path]:
        """
        Find existing output folder for `stem` across legacy layout and session layout.

        Legacy (older builds):
          TRANSCRIPTIONS_DIR/<stem>

        Session layout:
          TRANSCRIPTIONS_DIR/<session_stamp>/<stem>
        """
        safe = sanitize_filename(stem) or "item"
        root = Config.TRANSCRIPTIONS_DIR

        direct = root / safe
        if direct.exists():
            return direct

        if root.exists():
            for sess in root.iterdir():
                if not sess.is_dir():
                    continue
                cand = sess / safe
                if cand.exists():
                    return cand
        return None

    @staticmethod
    def transcript_path(
        stem: str,
        filename: str | None = None,
        *,
        base_name: str | None = None,
    ) -> Path:
        """
        Return transcript file path inside the item's output folder.

        - If `filename` is provided, it's used as-is in the output folder.
        - Otherwise use `base_name` (or "transcript") + default ext from config.
        """
        out_dir = FileManager.ensure_output(stem)
        if filename:
            return out_dir / filename

        ext = str(Config.transcript_default_ext() or "txt").lower().strip().lstrip(".") or "txt"
        name = sanitize_filename(str(base_name or "")) or "transcript"
        return out_dir / f"{name}.{ext}"

    # ----- Temp & downloads -----

    @staticmethod
    def clear_temp_dir(path: Path) -> None:
        """Remove temp dir if it exists; ignore errors."""
        if not path:
            return
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def url_tmp_dir() -> Path:
        """Temp directory for media downloaded from URLs."""
        p = Config.INPUT_TMP_DIR / "url"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def move_to_downloads(source: Path, *, desired_stem: str | None = None) -> Path:
        """Move a file into DOWNLOADS_DIR with a non-colliding name."""
        src = Path(source)
        downloads = Config.DOWNLOADS_DIR
        downloads.mkdir(parents=True, exist_ok=True)

        try:
            if src.parent.resolve() == downloads.resolve():
                return src
        except Exception:
            pass

        stem = sanitize_filename(desired_stem or src.stem) or "download"
        ext = src.suffix or ""
        candidate = downloads / f"{stem}{ext}"

        if candidate.exists():
            i = 2
            while True:
                cand = downloads / f"{stem} ({i}){ext}"
                if not cand.exists():
                    candidate = cand
                    break
                i += 1

        candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(candidate))
        return candidate

    @staticmethod
    def ensure_tmp_wav(
        source: Path,
        log=print,
        *,
        cancel_check=None,
    ) -> Path:
        """
        Return a WAV path for transcription.

        If source is already wav, return it.
        Otherwise, extract/convert to 16kHz mono PCM wav in INPUT_TMP_DIR.
        """
        ext = source.suffix.lower().strip()
        if ext == ".wav":
            return source

        tmp_dir = Config.INPUT_TMP_DIR
        tmp_dir.mkdir(parents=True, exist_ok=True)

        out = tmp_dir / f"{source.stem}.wav"
        if out.exists():
            return out

        AudioExtractor.ensure_mono_16k(
            source,
            out,
            log=log,
            cancel_check=cancel_check,
        )
        return out

    # ----- Misc -----

    @staticmethod
    def plan_output_files(
        *,
        output_dir: Path,
        base_stem: str,
        formats: List[str],
    ) -> Dict[str, Path]:
        """Build a mapping: format -> output path."""
        out: Dict[str, Path] = {}
        safe = sanitize_filename(base_stem) or "transcript"
        for fmt in formats:
            fmt_clean = (fmt or "").lower().strip().lstrip(".")
            if not fmt_clean:
                continue
            out[fmt_clean] = output_dir / f"{safe}.{fmt_clean}"
        return out

    @staticmethod
    def list_existing_transcripts(output_dir: Path) -> List[Path]:
        """Return existing transcript files in output_dir."""
        if not output_dir.exists():
            return []
        items = []
        for p in output_dir.iterdir():
            if p.is_file():
                items.append(p)
        return sorted(items)

    @staticmethod
    def snapshot_metadata(
        *,
        source: Path,
        title: str,
        language: str,
        extras: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a metadata dict saved next to transcripts."""
        data: Dict[str, Any] = {
            "source": str(source),
            "title": title,
            "language": language,
        }
        if extras:
            data.update(dict(extras))
        return data
