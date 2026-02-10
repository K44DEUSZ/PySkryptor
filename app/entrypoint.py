# app/entrypoint.py

from __future__ import annotations

import sys
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from model.config.app_config import AppConfig as Config
from model.services.app_logging_service import AppLoggingService
from model.services.settings_service import SettingsService, SettingsError
from view.utils.localization import Translator
from view.views.dialogs import (
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


    log_ctx = AppLoggingService.setup(Config.ROOT_DIR, log_dir_name=str(Path("data") / "logs"))
    log = log_ctx.logger

    try:
        QtCore.qInstallMessageHandler(AppLoggingService.make_qt_message_handler(log, log_ctx.crash_log_path))
    except Exception:
        pass

    svc = SettingsService(Config.ROOT_DIR)

    try:
        snap, restored, reason = svc.load_or_restore()
    except SettingsError as ex:
        key = getattr(ex, "key", str(ex))
        if key == "error.settings.defaults_missing":
            critical_defaults_missing_and_exit(None)
            return 1
        critical_config_load_failed_and_exit(None, "SettingsError")
        return 1
    except Exception as ex:
        log.exception("entrypoint: load_or_restore() failed: %s", ex)
        critical_config_load_failed_and_exit(None, type(ex).__name__)
        return 1

    try:
        logging_cfg = snap.app.get("logging", {}) if isinstance(snap.app.get("logging"), dict) else {}
        AppLoggingService.apply_settings(
            log_ctx,
            file_enabled=bool(logging_cfg.get("enabled", True)),
            level=str(logging_cfg.get("level", "info") or "info"),
        )
    except Exception:
        pass

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

    try:
        _apply_stylesheet(app, Config.STYLES_DIR, str(snap.app.get("theme", "auto")))
    except Exception as ex:
        log.exception("entrypoint: stylesheet failed: %s", ex)

    from view.widgets.loading_screen import LoadingScreenWidget
    from controller.tasks.startup_task import StartupWorker, build_startup_tasks

    loading = LoadingScreenWidget()
    loading.setWindowTitle(Translator.tr("window.loading"))
    loading.set_indeterminate(True)
    loading.show()
    app.processEvents()

    labels = {
        "asr": Translator.tr("loading.stage.transcription_model"),
        "tr": Translator.tr("loading.stage.translation_model"),
        "init": Translator.tr("loading.stage.init"),
        "dirs": Translator.tr("loading.stage.dirs"),
        "ffmpeg": Translator.tr("loading.stage.ffmpeg"),
    }
    tasks = build_startup_tasks(Config, snap, labels)

    thread = QtCore.QThread()
    worker = StartupWorker(tasks)
    worker.moveToThread(thread)

    boot_refs = {"loading": loading, "thread": thread, "worker": worker, "win": None}
    setattr(app, "_boot_refs", boot_refs)

    def _on_status(text: str) -> None:
        loading.set_status(text)

    def _on_progress(pct: int) -> None:
        loading.set_indeterminate(False)
        loading.set_progress(pct)

    def _on_failed(msg: str) -> None:
        log.error("entrypoint: startup worker failed: %s", msg)
        loading.close()
        thread.quit()
        thread.wait(2000)
        critical_config_load_failed_and_exit(None, "StartupError")

    def _on_ready(ctx: dict) -> None:
        thread.quit()
        thread.wait(2000)

        try:
            from view.main_window import MainWindow
            win = MainWindow(boot_ctx=ctx)
            boot_refs["win"] = win
            win.show()
        except Exception as ex:
            log.exception("entrypoint: MainWindow failed: %s", ex)
            critical_config_load_failed_and_exit(None, type(ex).__name__)
            return

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