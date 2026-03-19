# app/main.py
from __future__ import annotations

import sys
from pathlib import Path


# ----- Bootstrap -----
if __package__ in (None, ""):
    _root_dir = Path(__file__).resolve().parents[1]
    if str(_root_dir) not in sys.path:
        sys.path.insert(0, str(_root_dir))

from PyQt5 import QtCore, QtGui, QtWidgets

from app.model.config.app_config import AppConfig as Config
from app.controller.platform.logging import LoggingSetup
from app.model.services.settings_service import SettingsService, SettingsError
from app.controller.support.localization import Translator
from app.view.components.dialogs import (
    critical_defaults_missing_and_exit,
    critical_locales_missing_and_exit,
    critical_startup_error_and_exit,
    critical_config_load_failed_choice,
)
from app.view.support.theme_runtime import app_icon, render_theme_stylesheet, system_theme_key
from app.view.ui_config import UIConfig


def _load_fonts(app: QtWidgets.QApplication, fonts_dir: Path) -> None:
    """Load packaged fonts and set the default family when available."""
    try:
        if not fonts_dir.exists():
            return
        for f in sorted(fonts_dir.rglob("*.ttf")):
            try:
                QtGui.QFontDatabase.addApplicationFont(str(f))
            except Exception:
                pass

        fams = set()
        try:
            font_db = QtGui.QFontDatabase()
            for fam in font_db.families():
                fams.add(str(fam))
        except Exception:
            pass

        if "Roboto" in fams:
            base = app.font()
            base.setFamily("Roboto")
            app.setFont(base)
    except Exception:
        pass


def _apply_palette(app: QtWidgets.QApplication, theme: str) -> None:
    try:
        pal = app.palette()
        accent = QtGui.QColor("#70A82E")
        pal.setColor(QtGui.QPalette.Highlight, accent)
        pal.setColor(QtGui.QPalette.Link, accent)
        pal.setColor(QtGui.QPalette.LinkVisited, QtGui.QColor("#5E8F28"))

        if str(theme).strip().lower() == "dark":
            pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#0F140F"))
        else:
            pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#2B3328"))

        app.setPalette(pal)
    except Exception:
        pass


def _apply_stylesheet(app: QtWidgets.QApplication, styles_dir: Path, theme_pref: str) -> str:
    theme, qss = render_theme_stylesheet(styles_dir, theme_pref, app=app)
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

    try:
        app.setApplicationName(Config.APP_NAME)
        app.setApplicationDisplayName(Config.APP_NAME)
    except Exception:
        pass

    ui_cfg = UIConfig()
    try:
        app.setProperty("ui_config", ui_cfg)
    except Exception:
        pass

    _load_fonts(app, Config.ASSETS_DIR / "fonts")

    bootstrap_file_enabled, bootstrap_level = LoggingSetup.read_bootstrap_settings(
        Config.DEFAULTS_FILE,
        Config.SETTINGS_FILE,
    )
    log_ctx = LoggingSetup.setup(
        Config.APP_LOG_PATH,
        Config.CRASH_LOG_PATH,
        file_enabled=bootstrap_file_enabled,
        bootstrap_level=bootstrap_level,
    )
    log = log_ctx.logger
    log.debug(
        "Bootstrap logging settings resolved. level=%s file_enabled=%s settings_file=%s defaults_file=%s",
        bootstrap_level,
        bool(bootstrap_file_enabled),
        Config.SETTINGS_FILE,
        Config.DEFAULTS_FILE,
    )

    try:
        QtCore.qInstallMessageHandler(LoggingSetup.make_qt_message_handler(log, log_ctx.crash_log_path))
        log.debug("Qt message handler installed. crash_log=%s", log_ctx.crash_log_path)
    except Exception:
        pass

    svc = SettingsService()
    try:
        Translator.load_best(Config.LOCALES_DIR, system_first=False, fallback="en")
        log.debug("Bootstrap localization loaded. locales_dir=%s", Config.LOCALES_DIR)
    except Exception:
        critical_locales_missing_and_exit(None)
        return 1

    try:
        snap = svc.load()
    except SettingsError as settings_ex:
        settings_error_key = getattr(settings_ex, "key", str(settings_ex))
        if settings_error_key == "error.settings.defaults_missing":
            critical_defaults_missing_and_exit(None)
            return 1
        try:
            detail = Translator.tr(settings_error_key, **(getattr(settings_ex, "params", {}) or {}))
        except Exception:
            detail = str(settings_error_key)
        action = critical_config_load_failed_choice(None, detail)
        if action != "restore_defaults":
            return 1

        try:
            svc.restore_defaults()
            snap = svc.load()
            log.debug("Settings restored from defaults after load failure. detail=%s", detail)
        except Exception as restore_ex:
            critical_startup_error_and_exit(None, type(restore_ex).__name__)
            return 1
    except Exception as load_ex:
        log.exception("Entrypoint settings load failed. detail=%s", load_ex)
        critical_startup_error_and_exit(None, type(load_ex).__name__)
        return 1

    logging_cfg: dict[str, object] = {}
    try:
        logging_cfg = snap.app.get("logging", {}) if isinstance(snap.app.get("logging"), dict) else {}
        LoggingSetup.apply_settings(
            log_ctx,
            file_enabled=bool(logging_cfg.get("enabled", True)),
            level=str(logging_cfg.get("level", "warning") or "warning"),
        )
    except Exception:
        pass

    log.debug(
        "Settings snapshot loaded. language=%s theme=%s logging_level=%s logging_enabled=%s",
        snap.app.get("language", "auto"),
        snap.app.get("theme", "auto"),
        logging_cfg.get("level", "warning"),
        bool(logging_cfg.get("enabled", True)),
    )

    locales_dir = Config.LOCALES_DIR
    lang_pref = str(snap.app.get("language", "auto"))

    try:
        if lang_pref.lower() == "auto":
            Translator.load_best(locales_dir, system_first=True, fallback="en")
        else:
            Translator.load(locales_dir, lang_pref)
        log.debug("Application localization activated. language=%s locales_dir=%s", Translator.current_language(), locales_dir)
    except Exception:
        critical_locales_missing_and_exit(None)
        return 1

    theme = system_theme_key(app)
    try:
        theme = _apply_stylesheet(app, Config.STYLES_DIR, str(snap.app.get("theme", "auto")))
        _apply_palette(app, theme)
        log.debug("Application theme applied. theme=%s styles_dir=%s", theme, Config.STYLES_DIR)
    except Exception as stylesheet_ex:
        log.exception("Entrypoint stylesheet failed. detail=%s", stylesheet_ex)

    try:
        icon = app_icon(theme)
        if icon is not None and not icon.isNull():
            app.setWindowIcon(icon)
    except Exception:
        pass

    try:
        from transformers.utils import logging as hf_logging
        hf_logging.set_verbosity_error()
        log.debug("Transformers logging clamped to error level.")
    except Exception:
        pass

    from app.view.components.loading_screen import LoadingScreenWidget
    from app.controller.tasks.startup_task import StartupWorker, build_startup_tasks
    from app.controller.support.task_thread_runner import TaskThreadRunner

    loading = LoadingScreenWidget()
    loading.set_indeterminate(True)
    loading.show()
    app.processEvents()
    log.debug("Loading screen shown.")

    labels = {
        "asr": Translator.tr("loading.stage.transcription_model"),
        "tr": Translator.tr("loading.stage.translation_model"),
        "init": Translator.tr("loading.stage.init"),
        "dirs": Translator.tr("loading.stage.dirs"),
        "ffmpeg": Translator.tr("loading.stage.ffmpeg"),
    }
    tasks = build_startup_tasks(Config, snap, labels)
    log.debug("Startup tasks built. count=%s", len(tasks))

    runner = TaskThreadRunner(app)
    worker = StartupWorker(tasks)

    boot_refs = {"loading": loading, "runner": runner, "win": None}
    setattr(app, "_boot_refs", boot_refs)

    def _on_status(text: str) -> None:
        loading.set_status(text)
        log.debug("Startup stage updated. label=%s", text)

    def _on_progress(pct: int) -> None:
        loading.set_indeterminate(False)
        loading.set_progress(pct)

    def _on_failed(failed_key: str, params: dict) -> None:
        log.error("Entrypoint startup worker failed. key=%s params=%s", failed_key, params)
        loading.finish()
        details = str((params or {}).get("detail") or failed_key or "StartupError")
        critical_startup_error_and_exit(None, details)

    def _on_ready(ctx: dict) -> None:
        try:
            from app.view.main_window import MainWindow
            win = MainWindow(boot_ctx=ctx, ui_cfg=ui_cfg)
            boot_refs["win"] = win
            win.show()
            live_has_audio = bool(getattr(getattr(win, "live_panel", None), "_has_audio_devices", False))
            log.debug(
                "Startup context ready. asr_ready=%s translation_ready=%s network_status=%s microphones_detected=%s",
                bool(ctx.get("transcription_ready")),
                bool(ctx.get("translation_ready")),
                getattr(win, "network_status", lambda: "checking")(),
                live_has_audio,
            )
        except Exception as ready_ex:
            log.exception("Entrypoint main window creation failed. detail=%s", ready_ex)
            loading.finish()
            critical_startup_error_and_exit(None, type(ready_ex).__name__)
            return

        loading.finish()
        loading.deleteLater()

    def _connect(wk: StartupWorker) -> None:
        wk.status.connect(_on_status)
        wk.progress.connect(_on_progress)
        wk.failed.connect(_on_failed)
        wk.ready.connect(_on_ready)

    runner.start(worker, connect=_connect)
    log.debug("Startup worker scheduled.")
    return app.exec_()

if __name__ == "__main__":
    sys.exit(run())
