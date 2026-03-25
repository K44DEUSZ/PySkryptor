# app/model/helpers/output_resolver.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.model.helpers.string_utils import sanitize_filename
from app.model.io.file_manager import FileManager

ConflictResolverFn = Callable[[str, str], tuple[str, str, bool]]

@dataclass(frozen=True)
class OutputDirectoryResolution:
    """Resolved output-directory decision for a single item."""

    output_dir: Path | None
    stem: str
    apply_all: tuple[str, str] | None
    skipped: bool = False

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
    def existing_dir(stem: str) -> str | None:
        """Return path to existing conflicting dir, if any."""
        safe = sanitize_filename(stem)
        path = FileManager.find_existing_output(safe)
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
        existing = FileManager.find_existing_output(safe_stem)

        if existing is None:
            return OutputDirectoryResolution(
                output_dir=FileManager.ensure_output(safe_stem),
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
            return OutputDirectoryResolution(
                output_dir=None,
                stem=safe_stem,
                apply_all=resolved_apply_all,
                skipped=True,
            )

        if action == "overwrite":
            FileManager.delete_output_dir(existing)
            return OutputDirectoryResolution(
                output_dir=FileManager.ensure_output(safe_stem),
                stem=safe_stem,
                apply_all=resolved_apply_all,
                skipped=False,
            )

        if action == "new":
            candidate = sanitize_filename(new_stem or f"{safe_stem}-copy")
            candidate = OutputResolver.next_free(candidate)
            return OutputDirectoryResolution(
                output_dir=FileManager.ensure_output(candidate),
                stem=candidate,
                apply_all=resolved_apply_all,
                skipped=False,
            )

        return OutputDirectoryResolution(
            output_dir=None,
            stem=safe_stem,
            apply_all=resolved_apply_all,
            skipped=True,
        )

