# app/entrypoint.py
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5 import QtGui, QtWidgets

from core.config.app_config import AppConfig as Config
from core.services.settings_service import SettingsService, SettingsError
from ui.utils.translating import Translator
from ui.views.dialogs import (
    critical_defaults_missing_and_exit,
    critical_locales_missing_and_exit,
    critical_config_load_failed_and_exit,
    info_settings_restored,
)


def _guess_theme(app: QtWidgets.QApplication) -> str:
    try:
        col = app.palette().color(QtGui.QPalette.Window)
        return "dark" if col.value() < 128 else "light"
    except Exception:
        return "light"


def _apply_stylesheet(app: QtWidgets.QApplication, styles_dir: Path, theme_pref: str) -> str:
    """Load and apply QSS from resources/styles (if present)."""
    pref = (theme_pref or "auto").strip().lower()
    theme = pref if pref in ("light", "dark") else _guess_theme(app)

    parts: list[str] = []
    for name in ("base.qss", f"{theme}.qss"):
        f = styles_dir / name
        if f.exists() and f.is_file():
            try:
                parts.append(f.read_text(encoding="utf-8"))
            except Exception:
                pass

    qss = "\n\n".join([p.strip() for p in parts if p.strip()])
    if qss:
        app.setStyleSheet(qss)

    try:
        app.setProperty("theme", theme)
    except Exception:
        pass

    return theme


def run() -> int:
    app = QtWidgets.QApplication(sys.argv)

    # Resolve project root & prepare services
    project_root = Path(__file__).resolve().parent.parent
    Config.set_root_dir(project_root)

    svc = SettingsService(Config.ROOT_DIR)

    # Load or restore settings snapshot (do not localize errors yet)
    try:
        snap, restored, reason = svc.load_or_restore()
    except SettingsError as ex:
        # defaults.json missing -> hard stop (EN)
        if getattr(ex, "key", "") == "error.settings.defaults_missing":
            critical_defaults_missing_and_exit(None)
            return 1

        critical_config_load_failed_and_exit(None, str(getattr(ex, "key", str(ex))))
        return 1

    # Load i18n (after we know language preference)
    locales_dir = Config.LOCALES_DIR
    lang_pref = str(snap.app.get("language", "auto"))

    try:
        if lang_pref.lower() == "auto":
            Translator.load_best(locales_dir, system_first=True, fallback="en")
        else:
            Translator.load(locales_dir, lang_pref)
    except Exception:
        critical_locales_missing_and_exit(None)
        return 1

    # If settings.json was missing or invalid and auto-restored -> inform user (localized)
    if restored and reason in ("error.settings.settings_missing", "error.settings.settings_invalid"):
        info_settings_restored(None)

    # Apply runtime config (paths, device/dtype, etc.)
    Config.initialize_from_snapshot(snap)

    _apply_stylesheet(app, Config.STYLES_DIR, str(snap.app.get("theme", "auto")))

    # Create & show main window
    from ui.views.main_window import MainWindow

    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())