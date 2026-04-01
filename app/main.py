# app/main.py
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

LoggerLike = Any

_TRANSLATION_WORKER_ARG = '--translation-worker'

if __package__ in (None, ''):
    root_dir = Path(__file__).resolve().parents[1]
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))


def _resolve_bundle_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent)).resolve()
    return Path(__file__).resolve().parent.parent


def _resolve_install_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _dispatch_embedded_worker(argv: list[str] | None = None) -> int | None:
    args = list(argv if argv is not None else sys.argv[1:])
    if _TRANSLATION_WORKER_ARG not in args:
        return None

    from app.model.translation.runtime import cli_entry

    return cli_entry(['--worker'])


_worker_exit_code = _dispatch_embedded_worker()
if _worker_exit_code is not None:
    raise SystemExit(_worker_exit_code)

from PyQt5 import QtCore, QtGui, QtWidgets

from app.controller.coordinators.app_coordinator import AppCoordinator
from app.controller.workers.startup_worker import build_startup_tasks
from app.controller.platform.logging_setup import LoggingSetup
from app.model.core.config.config import AppConfig
from app.model.core.config.meta import AppMeta
from app.model.core.domain.errors import AppError
from app.model.core.domain.state import AppRuntimeState
from app.model.core.runtime.localization import current_language, load, load_best, tr
from app.model.core.runtime.platform import ensure_windows_platform
from app.model.settings.resolution import build_welcome_dialog_payload
from app.model.settings.service import SettingsService
from app.model.settings.validation import SettingsError
from app.view.components.loading_screen import LoadingScreenWidget
from app.view.components.hint_popup import install_application_tooltip_filter
from app.view.dialogs import (
    ask_welcome_dialog,
    critical_config_load_failed_choice,
    critical_defaults_missing_and_exit,
    critical_locales_missing_and_exit,
    critical_startup_error_and_exit,
)
from app.view.main_window import MainWindow
from app.view.support.theme_runtime import (
    app_icon,
    app_palette_colors,
    render_theme_stylesheet,
    system_theme_key,
)
from app.view.ui_config import UIConfig

_LOG = logging.getLogger(__name__)


def _load_fonts(app: QtWidgets.QApplication, fonts_dir: Path) -> None:
    """Load packaged fonts and apply the preferred application family when available."""
    try:
        if not fonts_dir.exists():
            return

        for font_path in sorted(fonts_dir.rglob('*.ttf')):
            try:
                QtGui.QFontDatabase.addApplicationFont(str(font_path))
            except (RuntimeError, OSError, TypeError, ValueError):
                continue

        families: set[str] = set()
        try:
            font_db = QtGui.QFontDatabase()
            for family in font_db.families():
                families.add(str(family))
        except (RuntimeError, TypeError, ValueError):
            families = set()

        if 'Roboto' in families:
            base_font = app.font()
            base_font.setFamily('Roboto')
            app.setFont(base_font)
    except (RuntimeError, OSError, TypeError, ValueError) as ex:
        _LOG.debug("Font loading skipped. fonts_dir=%s detail=%s", fonts_dir, ex)


def _apply_palette(app: QtWidgets.QApplication, theme: str) -> None:
    try:
        palette = app.palette()
        colors = app_palette_colors(theme)
        palette.setColor(QtGui.QPalette.Highlight, colors['highlight'])
        palette.setColor(QtGui.QPalette.Link, colors['link'])
        palette.setColor(QtGui.QPalette.LinkVisited, colors['link_visited'])
        palette.setColor(QtGui.QPalette.HighlightedText, colors['highlighted_text'])
        app.setPalette(palette)
    except (KeyError, RuntimeError, TypeError, ValueError) as ex:
        _LOG.debug("Palette application skipped. theme=%s detail=%s", theme, ex)


def _apply_stylesheet(app: QtWidgets.QApplication, styles_dir: Path, theme_pref: str) -> str:
    theme, stylesheet = render_theme_stylesheet(styles_dir, theme_pref, app=app)
    if stylesheet:
        app.setStyleSheet(stylesheet)

    try:
        app.setProperty('theme', theme)
    except (RuntimeError, TypeError) as ex:
        _LOG.debug("Application theme property update skipped. theme=%s detail=%s", theme, ex)

    return theme


def _create_application(argv: list[str] | None = None) -> QtWidgets.QApplication:
    return QtWidgets.QApplication(list(argv or sys.argv))


def _configure_application(app: QtWidgets.QApplication) -> UIConfig:
    bundle_root = _resolve_bundle_root()
    install_root = _resolve_install_root()
    AppConfig.set_root_dir(bundle_root, install_root=install_root)

    try:
        app.setApplicationName(AppMeta.NAME)
        app.setApplicationDisplayName(AppMeta.NAME)
    except (RuntimeError, TypeError, ValueError) as ex:
        _LOG.debug("Application metadata update skipped. app_name=%s detail=%s", AppMeta.NAME, ex)

    ui_cfg = UIConfig()
    try:
        app.setProperty('ui_config', ui_cfg)
    except (RuntimeError, TypeError) as ex:
        _LOG.debug("Application ui_config property update skipped. detail=%s", ex)

    install_application_tooltip_filter(app)

    _load_fonts(app, AppConfig.PATHS.ASSETS_DIR / 'fonts')
    return ui_cfg


def _setup_logging() -> Any:
    bootstrap_file_enabled, bootstrap_level = LoggingSetup.read_bootstrap_settings(
        AppConfig.PATHS.DEFAULTS_FILE,
        AppConfig.PATHS.SETTINGS_FILE,
    )
    log_ctx = LoggingSetup.setup(
        AppConfig.PATHS.APP_LOG_PATH,
        AppConfig.PATHS.CRASH_LOG_PATH,
        file_enabled=bootstrap_file_enabled,
        bootstrap_level=bootstrap_level,
    )

    _LOG.debug(
        'Startup logging settings resolved. level=%s file_enabled=%s settings_file=%s defaults_file=%s',
        bootstrap_level,
        bool(bootstrap_file_enabled),
        AppConfig.PATHS.SETTINGS_FILE,
        AppConfig.PATHS.DEFAULTS_FILE,
    )

    try:
        QtCore.qInstallMessageHandler(
            LoggingSetup.make_qt_message_handler(log_ctx.crash_log_path)
        )
        _LOG.debug('Qt message handler installed. crash_log=%s', log_ctx.crash_log_path)
    except (RuntimeError, TypeError) as ex:
        _LOG.warning('Qt message handler installation failed. detail=%s', ex)

    return log_ctx


def _load_startup_localization(logger: LoggerLike) -> bool:
    try:
        load_best(AppConfig.PATHS.LOCALES_DIR, system_first=False, fallback='en')
        logger.debug('Startup localization loaded. locales_dir=%s', AppConfig.PATHS.LOCALES_DIR)
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        critical_locales_missing_and_exit(None)
        return False


def _ensure_supported_platform(logger: LoggerLike) -> bool:
    try:
        ensure_windows_platform()
        return True
    except AppError as ex:
        try:
            detail = tr(ex.key, **(ex.params or {}))
        except (RuntimeError, TypeError, ValueError, KeyError):
            detail = str(ex.key)
        logger.error("Unsupported runtime platform. detail=%s", detail)
        critical_startup_error_and_exit(None, detail)
        return False


def _load_settings(service: SettingsService, logger: LoggerLike) -> Any | None:
    try:
        return service.load()
    except SettingsError as settings_ex:
        settings_error_key = settings_ex.key
        if settings_error_key == 'error.settings.defaults_missing':
            critical_defaults_missing_and_exit(None)
            return None

        try:
            detail = tr(settings_error_key, **(settings_ex.params or {}))
        except (RuntimeError, TypeError, ValueError, KeyError):
            detail = str(settings_error_key)

        action = critical_config_load_failed_choice(None, detail)
        if action != 'restore_defaults':
            return None

        try:
            service.restore_defaults()
            logger.debug('Settings restored from defaults after load failure. detail=%s', detail)
            return service.load()
        except (OSError, RuntimeError, TypeError, ValueError, SettingsError) as restore_ex:
            critical_startup_error_and_exit(None, type(restore_ex).__name__)
            return None
    except (OSError, RuntimeError, TypeError, ValueError) as load_ex:
        logger.exception('Entrypoint settings load failed. detail=%s', load_ex)
        critical_startup_error_and_exit(None, type(load_ex).__name__)
        return None


def _apply_logging_settings(log_ctx: Any, snap: Any) -> None:
    try:
        logging_cfg = snap.app.get('logging', {}) if isinstance(snap.app.get('logging'), dict) else {}
        LoggingSetup.apply_settings(
            log_ctx,
            file_enabled=bool(logging_cfg.get('enabled', True)),
            level=str(logging_cfg.get('level', 'warning') or 'warning'),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError) as ex:
        _LOG.warning('Runtime logging settings apply failed. detail=%s', ex)


def _activate_application_localization(snap: Any, logger: LoggerLike) -> bool:
    lang_pref = str(snap.app.get('language', 'auto') or 'auto')
    try:
        if lang_pref.lower() == 'auto':
            load_best(AppConfig.PATHS.LOCALES_DIR, system_first=True, fallback='en')
        else:
            load(AppConfig.PATHS.LOCALES_DIR, lang_pref)

        logger.debug(
            'Application localization activated. language=%s locales_dir=%s',
            current_language(),
            AppConfig.PATHS.LOCALES_DIR,
        )
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        critical_locales_missing_and_exit(None)
        return False


def _apply_theme(app: QtWidgets.QApplication, snap: Any, logger: Any) -> str:
    theme = system_theme_key(app)
    try:
        theme = _apply_stylesheet(app, AppConfig.PATHS.STYLES_DIR, str(snap.app.get('theme', 'auto') or 'auto'))
        _apply_palette(app, theme)
        logger.debug('Application theme applied. theme=%s styles_dir=%s', theme, AppConfig.PATHS.STYLES_DIR)
    except (OSError, RuntimeError, TypeError, ValueError) as stylesheet_ex:
        logger.exception('Entrypoint stylesheet failed. detail=%s', stylesheet_ex)
    return theme


def _apply_window_icon(app: QtWidgets.QApplication, theme: str) -> None:
    try:
        icon = app_icon(theme)
        if icon is not None and not icon.isNull():
            app.setWindowIcon(icon)
    except (RuntimeError, TypeError) as ex:
        _LOG.debug("Application window icon update skipped. theme=%s detail=%s", theme, ex)


def _clamp_third_party_logging(logger: Any) -> None:
    try:
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
        logger.debug('Transformers logging clamped to error level.')
    except (ImportError, AttributeError, RuntimeError, TypeError, ValueError) as ex:
        logger.debug('Transformers logging clamp skipped. detail=%s', ex)


def _build_startup_labels() -> dict[str, str]:
    return {
        'asr': tr('loading.stage.transcription_model'),
        'translation': tr('loading.stage.translation_model'),
        'init': tr('loading.stage.init'),
        'dirs': tr('loading.stage.dirs'),
        'ffmpeg': tr('loading.stage.ffmpeg'),
    }


def _start_loading_screen(app: QtWidgets.QApplication) -> LoadingScreenWidget:
    loading = LoadingScreenWidget()
    loading.set_indeterminate(True)
    loading.show()
    app.processEvents()
    return loading


def _show_runtime_welcome_dialog(
    app: QtWidgets.QApplication,
    win: MainWindow,
    settings_service: SettingsService,
    logger: Any,
) -> None:
    if not AppConfig.ui_welcome_dialog_enabled():
        return

    accepted = ask_welcome_dialog(win)
    if not accepted:
        logger.debug("Startup welcome dialog rejected by user.")
        try:
            win.close()
        except (AttributeError, RuntimeError, TypeError):
            pass
        app.quit()
        return

    try:
        snap = settings_service.save(build_welcome_dialog_payload(show_on_startup=False))
    except (OSError, RuntimeError, TypeError, ValueError, SettingsError) as ex:
        logger.debug("Startup welcome preference save skipped. detail=%s", ex)
        return

    AppConfig.initialize_from_snapshot(snap)
    logger.debug("Startup welcome dialog dismissed permanently.")


def _start_startup(
    app: QtWidgets.QApplication,
    *,
    ui_cfg: UIConfig,
    snap: Any,
    logger: Any,
    settings_service: SettingsService,
) -> int:
    loading = _start_loading_screen(app)
    logger.debug('Loading screen shown.')

    controller = AppCoordinator(app)
    startup = controller.startup
    labels = _build_startup_labels()

    def _on_status(text: str) -> None:
        loading.set_status(text)
        logger.debug('Startup stage updated. label=%s', text)

    def _on_progress(pct: int) -> None:
        loading.set_indeterminate(False)
        loading.set_progress(pct)

    def _on_failed(_failed_key: str, params: dict[str, Any]) -> None:
        detail = str((params or {}).get('detail') or '').strip()
        path = str((params or {}).get('path') or '').strip()
        logger.error('Entrypoint startup worker failed. detail=%s path=%s', detail, path)
        loading.finish()
        ui_detail = detail or path or 'StartupError'
        critical_startup_error_and_exit(None, ui_detail)

    def _on_ready(runtime_state: AppRuntimeState) -> None:
        try:
            controller.set_runtime_state(runtime_state)
            win = MainWindow(ui_cfg=ui_cfg)
            controller.bind_main_window(win)
            win.show()
            live_panel = win.live_panel
            live_has_audio = bool(live_panel.has_audio_devices()) if live_panel is not None else False
            logger.debug(
                'Startup context ready. asr_ready=%s translation_ready=%s network_status=%s microphones_detected=%s',
                bool(runtime_state.transcription_ready),
                bool(runtime_state.translation_ready),
                win.network_status(),
                live_has_audio,
            )
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as ready_ex:
            logger.exception('Entrypoint main window creation failed. detail=%s', ready_ex)
            loading.finish()
            critical_startup_error_and_exit(None, type(ready_ex).__name__)
            return

        loading.finish()
        loading.deleteLater()
        QtCore.QTimer.singleShot(0, lambda: _show_runtime_welcome_dialog(app, win, settings_service, logger))

    def _connect_worker(startup_worker: Any) -> None:
        startup_worker.status.connect(_on_status)
        startup_worker.progress.connect(_on_progress)
        startup_worker.failed.connect(_on_failed)
        startup_worker.ready.connect(_on_ready)

    worker = startup.start(build_startup_tasks(AppConfig, snap, labels), connect=_connect_worker)
    if worker is None:
        logger.error('Entrypoint startup worker could not be scheduled. reason=busy')
        loading.finish()
        critical_startup_error_and_exit(None, 'StartupBusy')
        return 1

    logger.debug('Startup worker scheduled.')
    return app.exec_()


def run(argv: list[str] | None = None) -> int:
    app = _create_application(argv)
    ui_cfg = _configure_application(app)

    log_ctx = _setup_logging()
    if not _load_startup_localization(_LOG):
        return 1
    if not _ensure_supported_platform(_LOG):
        return 1

    settings_service = SettingsService()
    snap = _load_settings(settings_service, _LOG)
    if snap is None:
        return 1

    AppConfig.initialize_from_snapshot(snap)
    _apply_logging_settings(log_ctx, snap)

    _LOG.debug(
        'Settings snapshot loaded. language=%s theme=%s',
        snap.app.get('language', 'auto'),
        snap.app.get('theme', 'auto'),
    )

    if not _activate_application_localization(snap, _LOG):
        return 1

    theme = _apply_theme(app, snap, _LOG)
    _apply_window_icon(app, theme)
    _clamp_third_party_logging(_LOG)

    snap = AppConfig.SETTINGS or snap
    return _start_startup(app, ui_cfg=ui_cfg, snap=snap, logger=_LOG, settings_service=settings_service)


if __name__ == '__main__':
    raise SystemExit(run())
