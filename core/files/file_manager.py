# pyskryptor/core/.files/file_manager.py
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from core.config import Config


@dataclass
class ConflictDecision:
    action: str  # 'skip' | 'overwrite' | 'new'
    stem: str


class FileManager:
    """Output directory operations and conflict resolution helpers."""

    @staticmethod
    def output_dir_for(stem: str) -> Path:
        return Config.OUTPUT_DIR / stem

    @staticmethod
    def exists(stem: str) -> bool:
        return FileManager.output_dir_for(stem).exists()

    @staticmethod
    def remove(stem: str) -> None:
        shutil.rmtree(str(FileManager.output_dir_for(stem)), ignore_errors=True)

    @staticmethod
    def next_free_stem(base_stem: str) -> str:
        candidate = base_stem
        n = 1
        while FileManager.output_dir_for(candidate).exists():
            candidate = f"{base_stem} ({n})"
            n += 1
        return candidate

    @staticmethod
    def ensure_output(stem: str) -> Path:
        out = FileManager.output_dir_for(stem)
        out.mkdir(parents=True, exist_ok=True)
        return out
