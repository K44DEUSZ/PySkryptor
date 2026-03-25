# app/model/io/file_manager.py
from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.model.config.app_config import AppConfig as Config
from app.model.config.transcription_output_policy import TranscriptionOutputPolicy
from app.model.io.audio_extractor import AudioExtractor
from app.model.domain.errors import OperationCancelled
from app.model.helpers.string_utils import sanitize_filename
from app.model.io.media_probe import is_url_source

_LOG = logging.getLogger(__name__)

class FileManager:
    """Filesystem helpers for inputs, downloads, session outputs and transcripts."""

    _session_dir: Path | None = None
    _session_created: bool = False

    @staticmethod
    def project_root() -> Path:
        return Config.PATHS.ROOT_DIR

    @staticmethod
    def downloads_dir() -> Path:
        return Config.PATHS.DOWNLOADS_DIR

    @staticmethod
    def transcriptions_dir() -> Path:
        return Config.PATHS.TRANSCRIPTIONS_DIR

    @staticmethod
    def plan_session() -> Path:
        """Plan a new session folder (timestamped), create lazily on first write."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        FileManager._session_dir = Config.PATHS.TRANSCRIPTIONS_DIR / stamp
        FileManager._session_created = False
        return FileManager._session_dir

    @staticmethod
    def ensure_session() -> Path:
        session_dir = FileManager._session_dir
        if session_dir is None:
            session_dir = FileManager.plan_session()
        if not FileManager._session_created:
            session_dir.mkdir(parents=True, exist_ok=True)
            FileManager._session_created = True
        return session_dir

    @staticmethod
    def session_dir() -> Path:
        """Return current planned/active session dir or TRANSCRIPTIONS_DIR fallback."""
        return FileManager._session_dir or Config.PATHS.TRANSCRIPTIONS_DIR

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

    @staticmethod
    def output_dir_for(stem: str) -> Path:
        safe = sanitize_filename(stem) or TranscriptionOutputPolicy.OUTPUT_DEFAULT_STEM
        return FileManager.session_dir() / safe

    @staticmethod
    def ensure_output(stem: str) -> Path:
        FileManager.ensure_session()
        p = FileManager.output_dir_for(stem)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def find_existing_output(stem: str) -> Path | None:
        """Find existing output folder for `stem` across legacy layout and session layout."""
        safe = sanitize_filename(stem) or TranscriptionOutputPolicy.OUTPUT_DEFAULT_STEM
        root = Config.PATHS.TRANSCRIPTIONS_DIR

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
    def delete_output_dir(output_dir: Path) -> None:
        """Delete an item's output folder and prune the session dir if it becomes empty."""
        if output_dir is None:
            return
        try:
            p = Path(output_dir)
        except (TypeError, ValueError):
            return
        if not p.exists() or not p.is_dir():
            return

        try:
            shutil.rmtree(p, ignore_errors=True)
        except OSError as ex:
            _LOG.debug("Output directory removal skipped. path=%s detail=%s", p, ex)
            return

        parent = p.parent
        try:
            root = Config.PATHS.TRANSCRIPTIONS_DIR
            if parent == root:
                return
            if root in parent.parents and parent.is_dir():
                try:
                    next(parent.iterdir())
                except StopIteration:
                    shutil.rmtree(parent, ignore_errors=True)
        except OSError as ex:
            _LOG.debug("Parent output directory pruning skipped. path=%s detail=%s", parent, ex)

    @staticmethod
    def clear_output_dir_contents(output_dir: Path) -> None:
        """Remove all children of `output_dir` but keep the directory itself."""
        try:
            p = Path(output_dir)
        except (TypeError, ValueError):
            return
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            _LOG.debug("Output directory creation skipped. path=%s detail=%s", p, ex)
            return
        try:
            for child in p.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except OSError as ex:
                    _LOG.debug("Output child cleanup skipped. path=%s detail=%s", child, ex)
                    continue
        except OSError as ex:
            _LOG.debug("Output directory iteration failed. path=%s detail=%s", p, ex)
            return

    @staticmethod
    def transcript_filename(mode_id: str) -> str:
        """Return a deterministic transcript filename for a given output mode."""
        return TranscriptionOutputPolicy.transcript_filename(mode_id)

    @staticmethod
    def ensure_unique_path(path: Path) -> Path:
        """Return a non-conflicting path by appending " (n)" before the suffix."""
        try:
            candidate = Path(path)
        except (TypeError, ValueError):
            return Path(str(path))

        if not candidate.exists():
            return candidate

        parent = candidate.parent
        stem = str(candidate.stem or "").strip() or TranscriptionOutputPolicy.OUTPUT_DEFAULT_STEM
        suffix = str(candidate.suffix or "")

        idx = 1
        while True:
            next_path = parent / f"{stem} ({idx}){suffix}"
            if not next_path.exists():
                return next_path
            idx += 1

    @staticmethod
    def transcript_path(
        stem: str,
        filename: str | None = None,
        *,
        base_name: str | None = None,
    ) -> Path:
        """Return transcript file path inside the item's output folder."""
        out_dir = FileManager.ensure_output(stem)
        if filename:
            return out_dir / filename

        ext = Config.transcription_output_default_ext()
        name = sanitize_filename(str(base_name or "")) or TranscriptionOutputPolicy.TRANSCRIPT_DEFAULT_BASENAME
        return out_dir / f"{name}.{ext}"

    @staticmethod
    def audio_wav_path(stem: str, *, filename: str = TranscriptionOutputPolicy.AUDIO_OUTPUT_DEFAULT_FILENAME) -> Path:
        """Return a WAV asset path inside the item's output folder."""
        out_dir = FileManager.ensure_output(stem)

        name = str(filename or TranscriptionOutputPolicy.AUDIO_OUTPUT_DEFAULT_FILENAME).strip()
        base = Path(name).stem
        safe = sanitize_filename(base) or TranscriptionOutputPolicy.AUDIO_OUTPUT_DEFAULT_BASENAME
        return out_dir / f"{safe}.wav"

    @staticmethod
    def source_media_path(
        stem: str,
        *,
        src_ext: str,
        base_name: str = TranscriptionOutputPolicy.SOURCE_MEDIA_DEFAULT_BASENAME,
    ) -> Path:
        """Return a path for keeping the downloaded source media inside the item's output folder."""
        out_dir = FileManager.ensure_output(stem)

        ext = str(src_ext or "").strip().lstrip(".") or TranscriptionOutputPolicy.SOURCE_MEDIA_DEFAULT_EXT
        safe = sanitize_filename(str(base_name or "")) or TranscriptionOutputPolicy.SOURCE_MEDIA_DEFAULT_BASENAME
        return out_dir / f"{safe}.{ext}"

    @staticmethod
    def clear_temp_dir(path: Path) -> None:
        """Remove temp dir if it exists; ignore errors."""
        if not path:
            return
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def url_tmp_dir() -> Path:
        """Temp directory for media downloaded from URLs."""
        p = Config.PATHS.DOWNLOADS_TMP_DIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _tmp_wav_name_for(source: Path) -> str:
        """Return a stable temp WAV filename for a specific source file version."""
        safe_stem = sanitize_filename(source.stem) or TranscriptionOutputPolicy.TMP_AUDIO_DEFAULT_STEM
        try:
            stat = source.stat()
            sig = f"{source.resolve()}|{int(stat.st_size)}|{int(stat.st_mtime_ns)}"
        except OSError:
            sig = str(source)
        digest = hashlib.sha1(sig.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{safe_stem}_{digest}.wav"

    @staticmethod
    def ensure_tmp_wav(
        source: Path,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Path:
        """Return a mono 16k WAV path for transcription."""
        if AudioExtractor.is_wav_mono_16k(source):
            return source

        tmp_dir = Config.PATHS.TRANSCRIPTIONS_TMP_DIR
        tmp_dir.mkdir(parents=True, exist_ok=True)

        out = tmp_dir / FileManager._tmp_wav_name_for(source)
        if out.exists() and AudioExtractor.is_wav_mono_16k(out):
            return out

        try:
            if out.exists():
                out.unlink(missing_ok=True)
        except OSError as ex:
            _LOG.debug("Existing temp WAV cleanup skipped. path=%s detail=%s", out, ex)

        AudioExtractor.ensure_mono_16k(
            source,
            out,
            cancel_check=cancel_check,
        )
        return out

    @staticmethod
    def normalize_source_text(raw: str) -> str:
        """Normalize a user-provided source string."""
        return (raw or "").strip()

    @staticmethod
    def parse_source_input(raw: str, *, supported_exts: list[str]) -> dict[str, Any]:
        """Parse a single user input as either URL or local file path."""
        key = FileManager.normalize_source_text(raw)
        if not key:
            return {"ok": False, "error": "empty"}

        if is_url_source(key):
            return {"ok": True, "type": "url", "key": key}

        p = Path(key)
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": "not_found"}

        exts = {str(e).lower().lstrip(".") for e in (supported_exts or [])}
        if exts and p.suffix.lower().lstrip(".") not in exts:
            return {"ok": False, "error": "unsupported"}

        return {"ok": True, "type": "file", "key": str(p)}

    @staticmethod
    def collect_media_files(
        paths: list[str],
        *,
        supported_exts: list[str],
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[str]:
        """Collect supported media files from a drag&drop payload (files and folders)."""
        exts = {str(e).lower().lstrip(".") for e in (supported_exts or [])}
        out: list[str] = []

        def _guard_cancel() -> None:
            if cancel_check is not None and bool(cancel_check()):
                raise OperationCancelled()

        def _add_file(file_path: Path) -> None:
            _guard_cancel()
            if not file_path.exists() or not file_path.is_file():
                return
            if exts and file_path.suffix.lower().lstrip(".") not in exts:
                return
            out.append(str(file_path))

        for raw in (paths or []):
            _guard_cancel()
            p = FileManager.normalize_source_text(raw)
            if not p:
                continue
            pp = Path(p)
            if not pp.exists():
                continue
            if pp.is_dir():
                for child_path in pp.rglob("*"):
                    _guard_cancel()
                    if child_path.is_file():
                        _add_file(child_path)
            else:
                _add_file(pp)

        return [str(p) for p in dict.fromkeys(out)]
