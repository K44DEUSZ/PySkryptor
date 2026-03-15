# app/model/helpers/output_resolver.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.model.io.file_manager import FileManager
from app.model.helpers.string_utils import sanitize_filename


class OutputResolver:
    """Helpers for output directory/file name resolution across all sessions."""

    @staticmethod
    def exists(stem: str) -> bool:
        safe = sanitize_filename(stem)
        return FileManager.find_existing_output(safe) is not None

    @staticmethod
    def next_free(stem: str) -> str:
        """Return a non-colliding stem by appending (n)."""
        base = sanitize_filename(stem)
        if not OutputResolver.exists(base):
            return base
        i = 1
        while True:
            candidate = f"{base} ({i})"
            if not OutputResolver.exists(candidate):
                return candidate
            i += 1

    @staticmethod
    def ensure(stem: str) -> Path:
        """Create (if needed) and return output directory for stem in current session."""
        safe = sanitize_filename(stem)
        return FileManager.ensure_output(safe)

    @staticmethod
    def existing_dir(stem: str) -> Optional[str]:
        """Return path to existing conflicting dir, if any."""
        safe = sanitize_filename(stem)
        path = FileManager.find_existing_output(safe)
        return str(path) if path else None

    @staticmethod
    def next_free_file_stem(out_dir: Path, stem: str, ext: str) -> str:
        """Return a non-colliding file stem inside `out_dir` for `.<ext>` using the same (n) scheme."""
        base = sanitize_filename(stem) or "item"
        ext_l = str(ext or "").strip().lower().lstrip(".") or "bin"
        out_dir = Path(out_dir)

        candidate = out_dir / f"{base}.{ext_l}"
        if not candidate.exists():
            return base

        i = 1
        while True:
            alt = f"{base} ({i})"
            if not (out_dir / f"{alt}.{ext_l}").exists():
                return alt
            i += 1
