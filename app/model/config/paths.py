# app/model/config/paths.py
from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

class PathCatalog:
    """Builds and applies the runtime path layout for the application."""

    @staticmethod
    def build(root_dir: Path, *, app_log_name: str, crash_log_name: str, missing_value: str) -> dict[str, Any]:
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
        return {
            'ROOT_DIR': root,
            'APP_DIR': app_dir,
            'LICENSE_FILE': root / 'LICENSE',
            'ASSETS_DIR': assets_dir,
            'RUNTIME_DIR': runtime_dir,
            'AI_MODELS_DIR': models_dir,
            'LOCALES_DIR': assets_dir / 'locales',
            'STYLES_DIR': app_dir / 'view',
            'IMAGES_DIR': assets_dir / 'images',
            'ICONS_DIR': assets_dir / 'icons',
            'FFMPEG_DIR': ffmpeg_dir,
            'FFMPEG_BIN_DIR': ffmpeg_dir,
            'DENO_DIR': deno_dir,
            'DENO_BIN': deno_dir / ('deno.exe' if platform.system().lower().startswith('win') else 'deno'),
            'TRANSCRIPTION_ENGINE_DIR': models_dir / missing_value,
            'TRANSLATION_ENGINE_DIR': models_dir / missing_value,
            'DATA_DIR': data_dir,
            'DOWNLOADS_DIR': data_dir / 'downloads',
            'TRANSCRIPTIONS_DIR': data_dir / 'transcriptions',
            'LOGS_DIR': logs_dir,
            'APP_LOG_PATH': logs_dir / app_log_name,
            'CRASH_LOG_PATH': logs_dir / crash_log_name,
            'USER_CONFIG_DIR': user_config_dir,
            'SETTINGS_FILE': user_config_dir / 'settings.json',
            'MODEL_CONFIG_DIR': app_dir / 'model' / 'config',
            'DEFAULTS_FILE': app_dir / 'model' / 'config' / 'defaults.json',
            'DOWNLOADS_TMP_DIR': data_dir / 'downloads' / '._tmp',
            'TRANSCRIPTIONS_TMP_DIR': data_dir / 'transcriptions' / '._tmp',
        }

    @classmethod
    def apply_to_config(cls, config_cls: type, root_dir: Path) -> None:
        values = cls.build(
            root_dir,
            app_log_name=getattr(config_cls, 'APP_LOG_NAME', 'app.log'),
            crash_log_name=getattr(config_cls, 'CRASH_LOG_NAME', 'crash.log'),
            missing_value=getattr(config_cls, 'MISSING_VALUE', '__missing__'),
        )
        for key, value in values.items():
            setattr(config_cls, key, value)

    @classmethod
    def ensure_runtime_dirs(cls, config_cls: type) -> None:
        for key in (
            'RUNTIME_DIR',
            'FFMPEG_DIR',
            'AI_MODELS_DIR',
            'LOCALES_DIR',
            'STYLES_DIR',
            'IMAGES_DIR',
            'ICONS_DIR',
            'DOWNLOADS_DIR',
            'TRANSCRIPTIONS_DIR',
            'LOGS_DIR',
            'USER_CONFIG_DIR',
            'DOWNLOADS_TMP_DIR',
            'TRANSCRIPTIONS_TMP_DIR',
        ):
            path = getattr(config_cls, key, None)
            if isinstance(path, Path):
                path.mkdir(parents=True, exist_ok=True)
