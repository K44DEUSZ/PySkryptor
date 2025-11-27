# app/entrypoint.py
from __future__ import annotations

import sys
from pathlib import Path
from PyQt5 import QtWidgets

from core.config.app_config import AppConfig as Config
from core.services.settings_service import SettingsService, SettingsError
from ui.utils.translating import Translator
from ui.views.dialogs import (
    critical_defaults_missing_and_exit,
    critical_locales_missing_and_exit,
    info_settings_restored,
)


def _resolve(root: Path, p: str) -> Path:
    """Resolve 'p' against project root if it's relative."""
    path = Path(p)
    return path if path.is_absolute() else (root / path)


def run() -> int:
    app = QtWidgets.QApplication(sys.argv)

    # Resolve project root & prepare services
    project_root = Path(__file__).resolve().parent.parent
    Config.ROOT_DIR = project_root
    svc = SettingsService(project_root)

    # Load or restore settings snapshot (but DO NOT localize errors yet)
    try:
        snap, restored, reason = svc.load_or_restore()
    except SettingsError as ex:
        # If defaults.json missing — hard stop (EN).
        if getattr(ex, "key", "") == "error.defaults_missing":
            critical_defaults_missing_and_exit(None)
            return 1

        title = "Application Error"
        body = f"Cannot load configuration.\n\nDetails: {getattr(ex, 'key', str(ex))}"
        QtWidgets.QMessageBox.critical(None, title, body)
        return 1

    # Load i18n (after we know paths + language preference)
    locales_dir = _resolve(project_root, snap.paths["locales_dir"])
    lang_pref = str(snap.app.get("language", "auto"))

    try:
        if lang_pref.lower() == "auto":
            # Pick best match based on system locale, fallback to English.
            Translator.load_best(locales_dir, system_first=True, fallback="en")
        else:
            Translator.load(locales_dir, lang_pref)
    except Exception:
        critical_locales_missing_and_exit(None)
        return 1

    # If settings.json was missing and auto-restored: inform user (localized) and continue.
    if restored and reason == "error.settings_missing":
        info_settings_restored(None)

    # Apply runtime config (paths, device/dtype, etc.)
    Config.initialize(svc)

    # Create & show main window
    from ui.views.main_window import MainWindow
    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())
