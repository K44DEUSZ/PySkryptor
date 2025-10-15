# pyskryptor/core/config/paths.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    input: Path
    output: Path
    models: Path

    @staticmethod
    def from_root(root: Path) -> "Paths":
        data = root / "data"
        return Paths(
            root=root,
            data=data,
            input=data / "input",
            output=data / "output",
            models=data / "models",
        )
