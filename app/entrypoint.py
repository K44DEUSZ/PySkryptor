# app/entrypoint.py
from __future__ import annotations
import sys
from pathlib import Path
from PyQt5 import QtWidgets

from core.config.app_config import AppConfig as Config
from core.services.settings_service import SettingsService, SettingsError
from ui.i18n.translator import Translator
from ui.views.main_window import MainWindow


def _resolve(root: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (root / path)


def run() -> int:
    app = QtWidgets.QApplication(sys.argv)

    # ---------- Resolve project root & pre-load settings ----------
    project_root = Path(__file__).resolve().parent.parent
    Config.ROOT_DIR = project_root
    svc = SettingsService(project_root)

    try:
        snap, restored, reason = svc.load_or_restore()
    except SettingsError as ex:
        QtWidgets.QMessageBox.critical(None, "PySkryptor", f"Configuration error: {ex.key}")
        return 1

    # ---------- i18n BEFORE main UI ----------
    locales_dir = _resolve(project_root, snap.paths["locales_dir"])
    lang_code = str(snap.user.get("language", "en"))
    try:
        Translator.load(locales_dir, lang_code)
    except Exception:
        try:
            Translator.load_best(locales_dir, system_first=True, fallback="en")
        except Exception:
            pass

    # ---------- Apply runtime config ----------
    Config.initialize(svc)

    # ---------- Main window ----------
    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())
