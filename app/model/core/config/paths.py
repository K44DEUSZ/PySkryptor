# app/model/core/config/paths.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PathCatalog:
    """Resolved runtime path layout for the application."""

    BUNDLE_ROOT_DIR: Path
    INSTALL_ROOT_DIR: Path
    ROOT_DIR: Path
    APP_DIR: Path
    LICENSE_FILE: Path
    ENGINE_HOST_EXE: Path
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
        bundle_root_dir: Path,
        *,
        install_root_dir: Path | None = None,
        app_log_name: str,
        crash_log_name: str,
        missing_value: str,
    ) -> "PathCatalog":
        bundle_root = Path(bundle_root_dir).resolve()
        install_root = Path(install_root_dir or bundle_root_dir).resolve()

        app_dir = bundle_root / 'app'
        assets_dir = install_root / 'assets'
        runtime_dir = install_root / 'bin'
        models_dir = install_root / 'models'
        data_dir = install_root / 'userdata'
        logs_dir = data_dir / 'logs'
        user_config_dir = data_dir / 'config'
        ffmpeg_dir = runtime_dir / 'ffmpeg'
        deno_dir = runtime_dir / 'deno'
        model_config_dir = app_dir / 'model' / 'settings'
        downloads_dir = data_dir / 'downloads'
        transcriptions_dir = data_dir / 'transcriptions'

        return cls(
            BUNDLE_ROOT_DIR=bundle_root,
            INSTALL_ROOT_DIR=install_root,
            ROOT_DIR=install_root,
            APP_DIR=app_dir,
            LICENSE_FILE=install_root / 'LICENSE',
            ENGINE_HOST_EXE=install_root / 'AIModelHost.exe',
            ASSETS_DIR=assets_dir,
            RUNTIME_DIR=runtime_dir,
            AI_MODELS_DIR=models_dir,
            LOCALES_DIR=assets_dir / 'locales',
            STYLES_DIR=app_dir / 'view',
            IMAGES_DIR=assets_dir / 'images',
            ICONS_DIR=assets_dir / 'icons',
            FFMPEG_DIR=ffmpeg_dir,
            FFMPEG_BIN_DIR=ffmpeg_dir,
            DENO_DIR=deno_dir,
            DENO_BIN=deno_dir / 'deno.exe',
            TRANSCRIPTION_ENGINE_DIR=models_dir / missing_value,
            TRANSLATION_ENGINE_DIR=models_dir / missing_value,
            DATA_DIR=data_dir,
            DOWNLOADS_DIR=downloads_dir,
            TRANSCRIPTIONS_DIR=transcriptions_dir,
            LOGS_DIR=logs_dir,
            APP_LOG_PATH=logs_dir / app_log_name,
            CRASH_LOG_PATH=logs_dir / crash_log_name,
            USER_CONFIG_DIR=user_config_dir,
            SETTINGS_FILE=user_config_dir / 'settings.json',
            MODEL_CONFIG_DIR=model_config_dir,
            DEFAULTS_FILE=model_config_dir / 'defaults.json',
            DOWNLOADS_TMP_DIR=downloads_dir / '._tmp',
            TRANSCRIPTIONS_TMP_DIR=transcriptions_dir / '._tmp',
        )

    @staticmethod
    def ensure_runtime_dirs(paths: "PathCatalog") -> None:
        for path in (
            paths.RUNTIME_DIR,
            paths.FFMPEG_DIR,
            paths.AI_MODELS_DIR,
            paths.DATA_DIR,
            paths.DOWNLOADS_DIR,
            paths.TRANSCRIPTIONS_DIR,
            paths.LOGS_DIR,
            paths.USER_CONFIG_DIR,
            paths.DOWNLOADS_TMP_DIR,
            paths.TRANSCRIPTIONS_TMP_DIR,
        ):
            if isinstance(path, Path):
                path.mkdir(parents=True, exist_ok=True)
