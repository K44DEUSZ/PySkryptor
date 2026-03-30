# app/model/transcription/workspace.py
from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.model.core.config.config import AppConfig
from app.model.core.utils.string_utils import sanitize_filename
from app.model.transcription.io import AudioExtractor
from app.model.transcription.policy import TranscriptionOutputPolicy

_LOG = logging.getLogger(__name__)

_session_dir: Path | None = None
_session_created: bool = False

ConflictResolverFn = Callable[[str, str], tuple[str, str, bool]]


@dataclass(frozen=True)
class OutputDirectoryResolution:
    """Resolved output-directory decision for a single item."""

    output_dir: Path | None
    stem: str
    apply_all: tuple[str, str] | None
    skipped: bool = False


class OutputResolver:
    """Helpers for output directory and name resolution across sessions."""

    @staticmethod
    def exists(stem: str) -> bool:
        safe = sanitize_filename(stem)
        return find_existing_output(safe) is not None

    @staticmethod
    def next_free(stem: str) -> str:
        """Return a non-colliding stem by appending (n)."""
        base = sanitize_filename(stem)
        if not OutputResolver.exists(base):
            return base
        index = 1
        while True:
            candidate = f"{base} ({index})"
            if not OutputResolver.exists(candidate):
                return candidate
            index += 1

    @staticmethod
    def existing_dir(stem: str) -> str | None:
        """Return path to an existing conflicting output dir, if any."""
        safe = sanitize_filename(stem)
        path = find_existing_output(safe)
        return str(path) if path else None

    @staticmethod
    def resolve_directory(
        *,
        stem: str,
        conflict_resolver: ConflictResolverFn,
        apply_all: tuple[str, str] | None,
    ) -> OutputDirectoryResolution:
        """Resolve and prepare the item's output directory, including conflicts."""
        safe_stem = sanitize_filename(stem)
        existing = find_existing_output(safe_stem)
        if existing is None:
            return OutputDirectoryResolution(
                output_dir=ensure_output(safe_stem),
                stem=safe_stem,
                apply_all=apply_all,
                skipped=False,
            )

        resolved_apply_all = apply_all
        if resolved_apply_all is None:
            action, new_stem, set_all = conflict_resolver(safe_stem, str(existing))
            action = str(action or "skip").strip().lower()
            new_stem = sanitize_filename(new_stem or "")
            if set_all:
                resolved_apply_all = (action, new_stem)
        else:
            action, new_stem = resolved_apply_all
            action = str(action or "skip").strip().lower()
            new_stem = sanitize_filename(new_stem or "")

        if action == "skip":
            return OutputDirectoryResolution(None, safe_stem, resolved_apply_all, skipped=True)
        if action == "overwrite":
            delete_output_dir(existing)
            return OutputDirectoryResolution(ensure_output(safe_stem), safe_stem, resolved_apply_all)
        if action == "new":
            candidate = sanitize_filename(new_stem or f"{safe_stem}-copy")
            candidate = OutputResolver.next_free(candidate)
            return OutputDirectoryResolution(ensure_output(candidate), candidate, resolved_apply_all)
        return OutputDirectoryResolution(None, safe_stem, resolved_apply_all, skipped=True)


def downloads_dir() -> Path:
    return AppConfig.PATHS.DOWNLOADS_DIR


def plan_session() -> Path:
    """Plan a new session folder, created lazily on first write."""
    global _session_dir, _session_created
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    _session_dir = AppConfig.PATHS.TRANSCRIPTIONS_DIR / stamp
    _session_created = False
    return _session_dir


def _ensure_session() -> Path:
    global _session_created
    session = _session_dir or plan_session()
    if not _session_created:
        session.mkdir(parents=True, exist_ok=True)
        _session_created = True
    return session


def _session_dir_path() -> Path:
    """Return the current planned or active session dir."""
    return _session_dir or AppConfig.PATHS.TRANSCRIPTIONS_DIR


def end_session() -> None:
    global _session_dir, _session_created
    _session_dir = None
    _session_created = False


def rollback_session_if_empty() -> None:
    session = _session_dir
    if not session or not session.exists() or not session.is_dir():
        return
    try:
        next(session.iterdir())
    except StopIteration:
        shutil.rmtree(session, ignore_errors=True)


def _output_dir_for(stem: str) -> Path:
    safe = sanitize_filename(stem) or TranscriptionOutputPolicy.OUTPUT_DEFAULT_STEM
    return _session_dir_path() / safe


def ensure_output(stem: str) -> Path:
    _ensure_session()
    path = _output_dir_for(stem)
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_existing_output(stem: str) -> Path | None:
    """Find an existing output folder within session layouts."""
    safe = sanitize_filename(stem) or TranscriptionOutputPolicy.OUTPUT_DEFAULT_STEM
    root = AppConfig.PATHS.TRANSCRIPTIONS_DIR
    if root.exists():
        for session in root.iterdir():
            if not session.is_dir():
                continue
            candidate = session / safe
            if candidate.exists():
                return candidate
    return None


def delete_output_dir(output_dir: Path) -> None:
    """Delete an item's output folder and prune the parent session when empty."""
    if output_dir is None:
        return
    try:
        path = Path(output_dir)
    except (TypeError, ValueError):
        return
    if not path.exists() or not path.is_dir():
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as ex:
        _LOG.debug("Output directory removal skipped. path=%s detail=%s", path, ex)
        return

    parent = path.parent
    try:
        root = AppConfig.PATHS.TRANSCRIPTIONS_DIR
        if parent == root:
            return
        if root in parent.parents and parent.is_dir():
            try:
                next(parent.iterdir())
            except StopIteration:
                shutil.rmtree(parent, ignore_errors=True)
    except OSError as ex:
        _LOG.debug("Parent output directory pruning skipped. path=%s detail=%s", parent, ex)


def url_tmp_dir() -> Path:
    """Temp directory for media downloaded from URLs."""
    path = AppConfig.PATHS.DOWNLOADS_TMP_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def ensure_tmp_wav(source: Path, *, cancel_check: Callable[[], bool] | None = None) -> Path:
    """Return a mono 16k WAV path for transcription."""
    if AudioExtractor.is_wav_mono_16k(source):
        return source
    tmp_dir = AppConfig.PATHS.TRANSCRIPTIONS_TMP_DIR
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / _tmp_wav_name_for(source)
    if out.exists() and AudioExtractor.is_wav_mono_16k(out):
        return out
    try:
        if out.exists():
            out.unlink(missing_ok=True)
    except OSError as ex:
        _LOG.debug("Existing temp WAV cleanup skipped. path=%s detail=%s", out, ex)
    AudioExtractor.ensure_mono_16k(source, out, cancel_check=cancel_check)
    return out
