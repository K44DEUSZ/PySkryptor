# app/entrypoint.py
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

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

    project_root = Path(__file__).resolve().parent.parent
    Config.set_root_dir(project_root)

    svc = SettingsService(Config.ROOT_DIR)

    try:
        snap, restored, reason = svc.load_or_restore()
    except SettingsError as ex:
        if getattr(ex, "key", "") == "error.settings.defaults_missing":
            critical_defaults_missing_and_exit(None)
            return 1
        critical_config_load_failed_and_exit(None, str(getattr(ex, "key", str(ex))))
        return 1

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

    if restored and reason in ("error.settings.settings_missing", "error.settings.settings_invalid"):
        info_settings_restored(None)

    Config.initialize_from_snapshot(snap)
    _apply_stylesheet(app, Config.STYLES_DIR, str(snap.app.get("theme", "auto")))

    from ui.widgets.loading_screen import LoadingScreenWidget
    from ui.workers.startup_worker import StartupWorker, StartupTask

    loading = LoadingScreenWidget()
    loading.setWindowTitle("Loading")
    loading.set_indeterminate(True)
    loading.show()
    app.processEvents()

    def _noop_task(progress, ctx) -> None:
        progress(100)

    tasks = [
        StartupTask(label=Translator.tr("loading.stage.start"), weight=1, fn=_noop_task),
    ]

    thread = QtCore.QThread()
    worker = StartupWorker(tasks)
    worker.moveToThread(thread)

    boot_refs = {
        "loading": loading,
        "thread": thread,
        "worker": worker,
        "win": None,
    }
    setattr(app, "_boot_refs", boot_refs)

    def _on_status(text: str) -> None:
        loading.set_status(text)

    def _on_progress(pct: int) -> None:
        loading.set_indeterminate(False)
        loading.set_progress(pct)

    def _on_failed(msg: str) -> None:
        loading.close()
        thread.quit()
        thread.wait(2000)
        critical_config_load_failed_and_exit(None, msg)

    def _on_ready(ctx: dict) -> None:
        thread.quit()
        thread.wait(2000)

        from ui.views.main_window import MainWindow

        win = MainWindow()
        boot_refs["win"] = win
        win.show()

        loading.close()
        loading.deleteLater()

    worker.status.connect(_on_status)
    worker.progress.connect(_on_progress)
    worker.failed.connect(_on_failed)
    worker.ready.connect(_on_ready)

    thread.started.connect(worker.run)
    worker.ready.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())