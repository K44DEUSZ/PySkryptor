from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config.app_config import AppConfig as Config


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    downloads: Path
    input_tmp: Path
    transcriptions: Path
    resources: Path
    ffmpeg: Path
    models: Path


def current() -> Paths:
    Config.initialize()
    return Paths(
        root=Config.ROOT_DIR,
        data=Config.DATA_DIR,
        downloads=Config.DOWNLOADS_DIR,
        input_tmp=Config.INPUT_TMP_DIR,
        transcriptions=Config.TRANSCRIPTIONS_DIR,
        resources=Config.RESOURCES_DIR,
        ffmpeg=Config.FFMPEG_BIN_DIR,
        models=Config.AI_ENGINE_DIR,
    )
