# core/services/conflict_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.files.file_manager import FileManager
from core.utils.text import sanitize_filename


@dataclass
class ConflictResult:
    action: str  # "skip" | "overwrite" | "new"
    stem: str


class ConflictService:
    """Helpers for output directory name conflicts across all sessions."""

    @staticmethod
    def exists(stem: str) -> bool:
        safe = sanitize_filename(stem)
        return FileManager.find_existing_output(safe) is not None

    @staticmethod
    def next_free(stem: str) -> str:
        """Return a non-colliding stem by appending (n)."""
        base = sanitize_filename(stem)
        if not ConflictService.exists(base):
            return base
        i = 1
        while True:
            candidate = f"{base} ({i})"
            if not ConflictService.exists(candidate):
                return candidate
            i += 1

    @staticmethod
    def ensure(stem: str):
        """Create (if needed) and return output directory for stem in the current session."""
        safe = sanitize_filename(stem)
        return FileManager.ensure_output(safe)

    @staticmethod
    def existing_dir(stem: str) -> Optional[str]:
        """Return path to existing conflicting dir, if any."""
        safe = sanitize_filename(stem)
        path = FileManager.find_existing_output(safe)
        return str(path) if path else None
