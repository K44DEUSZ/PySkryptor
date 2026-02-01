# app/entrypoint.py
from __future__ import annotations

import atexit
import logging
import logging.handlers
import sys
import traceback
from datetime import datetime
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


def _setup_bootstrap_logging(root_dir: Path) -> tuple[logging.Logger, Path, Path]:
    """
    Bootstrap logging early (before UI is constructed) to capture startup crashes.
    Keeps UI logging (QtHtmlLogSink) separate.
    """
    logs_dir = root_dir / ".logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    app_log = logs_dir / "app.log"
    startup_log = logs_dir / "startup.log"

    logger = logging.getLogger("PySkryptor")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        fh = logging.handlers.RotatingFileHandler(
            app_log, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    with startup_log.open("a", encoding="utf-8") as f:
        f.write("\n--- startup {} ---\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    return logger, logs_dir, startup_log


def run() -> int:
    app = QtWidgets.QApplication(sys.argv)

    project_root = Path(__file__).resolve().parent.parent
    Config.set_root_dir(project_root)

    logger, logs_dir, startup_log = _setup_bootstrap_logging(Config.ROOT_DIR)
    crash_py_log = logs_dir / "crash_python.log"
    fh_log = logs_dir / "faulthandler.log"

    def slog(msg: str) -> None:
        try:
            with startup_log.open("a", encoding="utf-8") as f:
                f.write(msg.rstrip() + "\n")
        except Exception:
            pass
        logger.info(msg)

    # faulthandler (helps for some native crashes)
    fh_file = None
    try:
        import faulthandler

        fh_file = fh_log.open("a", encoding="utf-8")
        faulthandler.enable(file=fh_file)
        atexit.register(lambda: fh_file.close() if fh_file else None)
        slog(f"logging: faulthandler enabled -> {fh_log}")
    except Exception as ex:
        slog(f"logging: faulthandler enable failed: {ex}")

    # Qt messages into logs
    def _qt_handler(mode, context, message) -> None:
        try:
            # mode is QtMsgType; map loosely to levels
            if mode == QtCore.QtCriticalMsg or mode == QtCore.QtFatalMsg:
                logger.error("[qt] %s", message)
            elif mode == QtCore.QtWarningMsg:
                logger.warning("[qt] %s", message)
            else:
                logger.info("[qt] %s", message)

            with startup_log.open("a", encoding="utf-8") as f:
                f.write("[qt] " + str(message) + "\n")
        except Exception:
            pass

    try:
        QtCore.qInstallMessageHandler(_qt_handler)
    except Exception:
        pass

    # Unhandled Python exceptions
    def _excepthook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logger.error("unhandled exception:\n%s", text)
        try:
            with crash_py_log.open("a", encoding="utf-8") as f:
                f.write("\n--- crash {} ---\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                f.write(text + "\n")
        except Exception:
            pass

    sys.excepthook = _excepthook

    slog("entrypoint: QApplication created")
    slog(f"entrypoint: root={Config.ROOT_DIR}")

    svc = SettingsService(Config.ROOT_DIR)

    try:
        slog("entrypoint: load_or_restore() begin")
        snap, restored, reason = svc.load_or_restore()
        slog("entrypoint: load_or_restore() ok")
    except SettingsError as ex:
        slog(f"entrypoint: load_or_restore() SettingsError key={getattr(ex, 'key', str(ex))}")
        if getattr(ex, "key", "") == "error.settings.defaults_missing":
            critical_defaults_missing_and_exit(None)
            return 1
        critical_config_load_failed_and_exit(None, str(getattr(ex, "key", str(ex))))
        return 1
    except Exception as ex:
        slog(f"entrypoint: load_or_restore() Exception: {ex}")
        critical_config_load_failed_and_exit(None, str(ex))
        return 1

    locales_dir = Config.LOCALES_DIR
    lang_pref = str(snap.app.get("language", "auto"))

    try:
        slog(f"entrypoint: Translator load lang={lang_pref}")
        if lang_pref.lower() == "auto":
            Translator.load_best(locales_dir, system_first=True, fallback="en")
        else:
            Translator.load(locales_dir, lang_pref)
        slog("entrypoint: Translator ok")
    except Exception as ex:
        slog(f"entrypoint: Translator failed: {ex}")
        critical_locales_missing_and_exit(None)
        return 1

    if restored and reason in ("error.settings.settings_missing", "error.settings.settings_invalid"):
        info_settings_restored(None)

    try:
        slog("entrypoint: Config.initialize_from_snapshot() begin")
        Config.initialize_from_snapshot(snap)
        slog("entrypoint: Config.initialize_from_snapshot() ok")
    except Exception as ex:
        slog(f"entrypoint: Config.initialize_from_snapshot() failed: {ex}")
        critical_config_load_failed_and_exit(None, str(ex))
        return 1

    try:
        slog("entrypoint: apply stylesheet")
        _apply_stylesheet(app, Config.STYLES_DIR, str(snap.app.get("theme", "auto")))
    except Exception as ex:
        slog(f"entrypoint: stylesheet failed: {ex}")

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
        slog(f"entrypoint: startup worker failed: {msg}")
        loading.close()
        thread.quit()
        thread.wait(2000)
        critical_config_load_failed_and_exit(None, msg)

    def _on_ready(ctx: dict) -> None:
        slog("entrypoint: startup worker ready")
        thread.quit()
        thread.wait(2000)

        try:
            slog("entrypoint: importing MainWindow")
            from ui.views.main_window import MainWindow
            slog("entrypoint: imported MainWindow")
        except Exception as ex:
            slog(f"entrypoint: import MainWindow failed: {ex}")
            critical_config_load_failed_and_exit(None, str(ex))
            return

        try:
            slog("entrypoint: constructing MainWindow")
            win = MainWindow()
            boot_refs["win"] = win
            slog("entrypoint: constructed MainWindow")
        except Exception as ex:
            slog(f"entrypoint: MainWindow() failed: {ex}")
            critical_config_load_failed_and_exit(None, str(ex))
            return

        try:
            slog("entrypoint: showing MainWindow")
            win.show()
            slog("entrypoint: MainWindow shown")
        except Exception as ex:
            slog(f"entrypoint: show() failed: {ex}")
            critical_config_load_failed_and_exit(None, str(ex))
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

    slog("entrypoint: starting startup thread")
    thread.start()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())
