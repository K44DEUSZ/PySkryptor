# app/model/config/paths.py
from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PathCatalog:
    """Resolved runtime path layout for the application."""

    ROOT_DIR: Path
    APP_DIR: Path
    LICENSE_FILE: Path
    ASSETS_DIR: Path
    RUNTIME_DIR: Path
    AI_MODELS_DIR: Path
    LOCALES_DIR: Path
    STYLES_DIR: Path
    IMAGES_DIR: Path
    ICONS_DIR: Path
    FFMPEG_DIR: Path
    FFMPEG_BIN_DIR: Path
    DENO_DIR: Path
    DENO_BIN: Path
    TRANSCRIPTION_ENGINE_DIR: Path
    TRANSLATION_ENGINE_DIR: Path
    DATA_DIR: Path
    DOWNLOADS_DIR: Path
    TRANSCRIPTIONS_DIR: Path
    LOGS_DIR: Path
    APP_LOG_PATH: Path
    CRASH_LOG_PATH: Path
    USER_CONFIG_DIR: Path
    SETTINGS_FILE: Path
    MODEL_CONFIG_DIR: Path
    DEFAULTS_FILE: Path
    DOWNLOADS_TMP_DIR: Path
    TRANSCRIPTIONS_TMP_DIR: Path

    @classmethod
    def build(
        cls,
        root_dir: Path,
        *,
        app_log_name: str,
        crash_log_name: str,
        missing_value: str,
    ) -> "PathCatalog":
        root = Path(root_dir).resolve()
        app_dir = root / "app"
        assets_dir = root / "assets"
        runtime_dir = root / "bin"
        models_dir = root / "models"
        data_dir = root / "userdata"
        logs_dir = data_dir / "logs"
        user_config_dir = data_dir / "config"
        ffmpeg_dir = runtime_dir / "ffmpeg"
        deno_dir = runtime_dir / "deno"
        model_config_dir = app_dir / "model" / "config"
        downloads_dir = data_dir / "downloads"
        transcriptions_dir = data_dir / "transcriptions"
        return cls(
            ROOT_DIR=root,
            APP_DIR=app_dir,
            LICENSE_FILE=root / "LICENSE",
            ASSETS_DIR=assets_dir,
            RUNTIME_DIR=runtime_dir,
            AI_MODELS_DIR=models_dir,
            LOCALES_DIR=assets_dir / "locales",
            STYLES_DIR=app_dir / "view",
            IMAGES_DIR=assets_dir / "images",
            ICONS_DIR=assets_dir / "icons",
            FFMPEG_DIR=ffmpeg_dir,
            FFMPEG_BIN_DIR=ffmpeg_dir,
            DENO_DIR=deno_dir,
            DENO_BIN=deno_dir / ("deno.exe" if platform.system().lower().startswith("win") else "deno"),
            TRANSCRIPTION_ENGINE_DIR=models_dir / missing_value,
            TRANSLATION_ENGINE_DIR=models_dir / missing_value,
            DATA_DIR=data_dir,
            DOWNLOADS_DIR=downloads_dir,
            TRANSCRIPTIONS_DIR=transcriptions_dir,
            LOGS_DIR=logs_dir,
            APP_LOG_PATH=logs_dir / app_log_name,
            CRASH_LOG_PATH=logs_dir / crash_log_name,
            USER_CONFIG_DIR=user_config_dir,
            SETTINGS_FILE=user_config_dir / "settings.json",
            MODEL_CONFIG_DIR=model_config_dir,
            DEFAULTS_FILE=model_config_dir / "defaults.json",
            DOWNLOADS_TMP_DIR=downloads_dir / "._tmp",
            TRANSCRIPTIONS_TMP_DIR=transcriptions_dir / "._tmp",
        )

    @staticmethod
    def ensure_runtime_dirs(paths: "PathCatalog") -> None:
        for path in (
            paths.RUNTIME_DIR,
            paths.FFMPEG_DIR,
            paths.AI_MODELS_DIR,
            paths.LOCALES_DIR,
            paths.STYLES_DIR,
            paths.IMAGES_DIR,
            paths.ICONS_DIR,
            paths.DOWNLOADS_DIR,
            paths.TRANSCRIPTIONS_DIR,
            paths.LOGS_DIR,
            paths.USER_CONFIG_DIR,
            paths.DOWNLOADS_TMP_DIR,
            paths.TRANSCRIPTIONS_TMP_DIR,
        ):
            if isinstance(path, Path):
                path.mkdir(parents=True, exist_ok=True)
