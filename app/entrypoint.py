# app/entrypoint.py
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from model.config.app_config import AppConfig as Config
from model.services.app_logging_service import AppLoggingService
from model.services.settings_service import SettingsService, SettingsError, RuntimeConfigService
from view.utils.translating import Translator
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
    """Load and apply QSS from view/resources/styles (if present)."""
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

    # Load translations early so startup errors are readable.
    # Language preference will be applied later from settings.
    try:
        Translator.load_best(Config.LOCALES_DIR, system_first=True, fallback="en")
    except Exception:
        pass

    log_ctx = AppLoggingService.setup(
        Config.ROOT_DIR,
        log_dir_name=str(Path("data") / "logs"),
    )
    log = log_ctx.logger

    try:
        QtCore.qInstallMessageHandler(
            AppLoggingService.make_qt_message_handler(log, log_ctx.crash_log_path)
        )
    except Exception:
        pass

    log.info("entrypoint: QApplication created")
    log.info("entrypoint: root=%s", Config.ROOT_DIR)

    svc = SettingsService(Config.ROOT_DIR)

    try:
        log.info("entrypoint: load_or_restore() begin")
        snap, restored, reason = svc.load_or_restore()
        log.info("entrypoint: load_or_restore() ok")
    except SettingsError as ex:
        key = getattr(ex, "key", str(ex))
        log.error("entrypoint: load_or_restore() SettingsError key=%s", key)
        if key == "error.settings.defaults_missing":
            critical_defaults_missing_and_exit(None)
            return 1
        # Do not show internal details (paths/lines). Keep details in logs only.
        critical_config_load_failed_and_exit(None, "SettingsError")
        return 1
    except Exception as ex:
        log.exception("entrypoint: load_or_restore() failed: %s", ex)
        critical_config_load_failed_and_exit(None, type(ex).__name__)
        return 1

    locales_dir = Config.LOCALES_DIR
    lang_pref = str(snap.app.get("language", "auto"))

    try:
        log.info("entrypoint: Translator load lang=%s", lang_pref)
        if lang_pref.lower() == "auto":
            Translator.load_best(locales_dir, system_first=True, fallback="en")
        else:
            Translator.load(locales_dir, lang_pref)
        log.info("entrypoint: Translator ok")
    except Exception as ex:
        log.exception("entrypoint: Translator failed: %s", ex)
        critical_locales_missing_and_exit(None)
        return 1

    if restored and reason in ("error.settings.settings_missing", "error.settings.settings_invalid"):
        info_settings_restored(None)

    try:
        log.info("entrypoint: RuntimeConfigService.initialize() begin")
        RuntimeConfigService.initialize(Config, snap)
        log.info("entrypoint: RuntimeConfigService.initialize() ok")
    except Exception as ex:
        log.exception("entrypoint: RuntimeConfigService.initialize() failed: %s", ex)
        critical_config_load_failed_and_exit(None, type(ex).__name__)
        return 1

    try:
        log.info("entrypoint: apply stylesheet")
        _apply_stylesheet(app, Config.STYLES_DIR, str(snap.app.get("theme", "auto")))
    except Exception as ex:
        log.exception("entrypoint: stylesheet failed: %s", ex)

    from view.widgets.loading_screen import LoadingScreenWidget
    from controller.tasks.startup_task import StartupWorker, StartupTask

    loading = LoadingScreenWidget()
    loading.setWindowTitle(Translator.tr("window.loading"))
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
        log.error("entrypoint: startup worker failed: %s", msg)
        loading.close()
        thread.quit()
        thread.wait(2000)
        critical_config_load_failed_and_exit(None, "StartupError")

    def _on_ready(ctx: dict) -> None:
        log.info("entrypoint: startup worker ready")
        thread.quit()
        thread.wait(2000)

        try:
            log.info("entrypoint: importing MainWindow")
            from view.main_window import MainWindow
            log.info("entrypoint: imported MainWindow")
        except Exception as ex:
            log.exception("entrypoint: import MainWindow failed: %s", ex)
            critical_config_load_failed_and_exit(None, type(ex).__name__)
            return

        try:
            log.info("entrypoint: constructing MainWindow")
            win = MainWindow()
            boot_refs["win"] = win
            log.info("entrypoint: constructed MainWindow")
        except Exception as ex:
            log.exception("entrypoint: MainWindow() failed: %s", ex)
            critical_config_load_failed_and_exit(None, type(ex).__name__)
            return

        try:
            log.info("entrypoint: showing MainWindow")
            win.show()
            log.info("entrypoint: MainWindow shown")
        except Exception as ex:
            log.exception("entrypoint: show() failed: %s", ex)
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

    log.info("entrypoint: starting startup thread")
    thread.start()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())
