# pyskryptor/core/services/conflict_service.py
from __future__ import annotations

from dataclasses import dataclass

from core.files.file_manager import FileManager


@dataclass
class ConflictResult:
    action: str
    stem: str


class ConflictService:
    """Helpers for output directory conflicts."""

    @staticmethod
    def exists(stem: str) -> bool:
        return FileManager.exists(stem)

    @staticmethod
    def next_free(stem: str) -> str:
        return FileManager.next_free_stem(stem)

    @staticmethod
    def remove(stem: str) -> None:
        FileManager.remove(stem)

    @staticmethod
    def ensure(stem: str):
        return FileManager.ensure_output(stem)
