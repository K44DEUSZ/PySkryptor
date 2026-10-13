# app/bootstrap.py
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

LoggerLike = Any

if __package__ in (None, ''):
  root_dir = Path(__file__).resolve().parents[1]
  if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from PyQt6 import QtCore, QtGui, QtWidgets

from app.application.coordinators.app_coordinator import AppCoordinator
from app.core.config.config import AppConfig
from app.core.config.meta import AppMeta
from app.core.domain.errors import AppError
from app.core.domain.state import AppRuntimeState
from app.core.runtime.bootstrap import build_startup_labels, resolve_runtime_roots
from app.core.runtime.localization import current_language, load, load_best, tr
from app.core.runtime.platform import ensure_windows_platform
from app.features.settings.resolution import build_welcome_dialog_payload
from app.features.settings.service import SettingsService
from app.features.settings.validation import SettingsError
from app.platform.desktop_lifecycle import DesktopLifecycle
from app.platform.instance_guard import InstanceGuard
from app.platform.logging_bootstrap import LoggingBootstrap
from app.platform.startup_check import write_startup_report
from app.ui.components.hint_popup import install_application_tooltip_filter
from app.ui.components.loading_screen import LoadingScreenWidget
from app.ui.dialogs import (
  ask_welcome_dialog,
  critical_config_load_failed_choice,
  critical_defaults_missing_and_exit,
  critical_locales_missing_and_exit,
  critical_startup_error_and_exit,
)
from app.ui.main_window import MainWindow
from app.ui.support.dialog_positioning import install_dialog_positioning
from app.ui.support.theme_runtime import (
  app_icon,
  app_palette_colors,
  render_theme_stylesheet,
  system_theme_key,
)
from app.ui.ui_config import UIConfig

_LOG = logging.getLogger("app.main")


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
      for family in QtGui.QFontDatabase.families():
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
    palette.setColor(QtGui.QPalette.ColorRole.Window, colors['window'])
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, colors['highlight'])
    palette.setColor(QtGui.QPalette.ColorRole.Link, colors['link'])
    palette.setColor(QtGui.QPalette.ColorRole.LinkVisited, colors['link_visited'])
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, colors['highlighted_text'])
    app.setPalette(palette)
  except (KeyError, RuntimeError, TypeError, ValueError) as ex:
    _LOG.debug("Palette application skipped. theme=%s detail=%s", theme, ex)


def _apply_stylesheet(app: QtWidgets.QApplication, styles_dir: Path, theme_pref: str) -> str:
  theme, stylesheet = render_theme_stylesheet(styles_dir, theme_pref, app=app)
  app.setProperty('themePreference', theme_pref)
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
  bundle_root, install_root = resolve_runtime_roots(__file__, unfrozen_parent_index=1)
  AppConfig.set_root_dir(bundle_root, install_root=install_root, data_root=app.property('startup_check_profile'))

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
  install_dialog_positioning(app)

  _load_fonts(app, AppConfig.PATHS.ASSETS_DIR / 'fonts')
  return ui_cfg


def _setup_logging() -> Any:
  bootstrap_file_enabled, bootstrap_level = LoggingBootstrap.read_bootstrap_settings(
    AppConfig.PATHS.DEFAULTS_FILE,
    AppConfig.PATHS.SETTINGS_FILE,
  )
  log_ctx = LoggingBootstrap.setup(
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
    QtCore.qInstallMessageHandler(LoggingBootstrap.make_qt_message_handler(log_ctx.crash_log_path))
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
    logger.error('Entrypoint settings load failed. detail=%s', load_ex, exc_info=True)
    critical_startup_error_and_exit(None, type(load_ex).__name__)
    return None


def _apply_logging_settings(log_ctx: Any, snap: Any) -> None:
  try:
    logging_cfg = snap.app.get('logging', {}) if isinstance(snap.app.get('logging'), dict) else {}
    LoggingBootstrap.apply_settings(
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
    logger.error('Entrypoint stylesheet failed. detail=%s', stylesheet_ex, exc_info=True)
  return theme


def _apply_window_icon(app: QtWidgets.QApplication, theme: str) -> None:
  try:
    icon = app_icon(theme)
    if icon is not None and not icon.isNull():
      app.setWindowIcon(icon)
  except (RuntimeError, TypeError) as ex:
    _LOG.debug("Application window icon update skipped. theme=%s detail=%s", theme, ex)


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
    app.property("desktop_lifecycle").request_quit()
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
  app.property("instance_guard").set_window(loading)
  logger.debug('Loading screen shown.')

  controller = AppCoordinator(app)
  lifecycle = DesktopLifecycle(loading, controller)
  app.setProperty('desktop_lifecycle', lifecycle)
  runtime = controller.runtime
  labels = build_startup_labels()

  def _on_status(text: str) -> None:
    loading.set_status(text)
    logger.debug('Startup stage updated. label=%s', text)

  def _on_progress(pct: int) -> None:
    loading.set_indeterminate(False)
    loading.set_progress(pct)

  def _on_failed(_failed_key: str, params: dict[str, Any]) -> None:
    detail = str((params or {}).get('detail') or '').strip()
    path = str((params or {}).get('path') or '').strip()
    logger.error('Entrypoint runtime worker failed. detail=%s path=%s', detail, path)
    loading.finish()
    ui_detail = detail or path or 'StartupError'
    critical_startup_error_and_exit(None, ui_detail)

  def _on_ready(runtime_state: AppRuntimeState) -> None:
    if lifecycle._quitting:
      return
    try:
      controller.set_runtime_state(runtime_state)
      win = MainWindow(ui_cfg=ui_cfg)
      controller.bind_main_window(win)
      lifecycle.attach_window(win)
      app.property('instance_guard').set_window(win)
      win.show()
      live_panel = win.live_panel
      live_has_audio = bool(live_panel.has_audio_devices()) if live_panel is not None else False
      logger.info(
        'Startup context ready. asr_ready=%s translation_ready=%s network_status=%s microphones_detected=%s',
        bool(runtime_state.transcription.ready),
        bool(runtime_state.translation.ready),
        win.network_status(),
        live_has_audio,
      )
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as ready_ex:
      logger.error('Entrypoint main window creation failed. detail=%s', ready_ex, exc_info=True)
      loading.finish()
      critical_startup_error_and_exit(None, type(ready_ex).__name__)
      return

    loading.finish()
    loading.deleteLater()
    if app.property('startup_check'):
      app.setProperty(
        'startup_check_result',
        {
          'complete': True,
          'main_window_visible': win.isVisible(),
          'qt_platform': QtGui.QGuiApplication.platformName(),
          'qt_version': QtCore.qVersion(),
          'transcription': {
            'ready': bool(runtime_state.transcription.ready),
            'hardware': runtime_state.transcription.hardware,
            'error_key': runtime_state.transcription.error_key,
          },
          'translation': {
            'ready': bool(runtime_state.translation.ready),
            'hardware': runtime_state.translation.hardware,
            'error_key': runtime_state.translation.error_key,
          },
        },
      )
      QtCore.QTimer.singleShot(0, lifecycle._begin_shutdown)
      return
    QtCore.QTimer.singleShot(0, lambda: _show_runtime_welcome_dialog(app, win, settings_service, logger))

  def _connect_worker(runtime_worker: Any) -> None:
    runtime_worker.status.connect(_on_status)
    runtime_worker.progress.connect(_on_progress)
    runtime_worker.failed.connect(_on_failed)
    runtime_worker.ready.connect(_on_ready)

  worker = runtime.start(snap, labels=labels, connect=_connect_worker, cleanup_temporary=True)
  if worker is None:
    logger.error('Entrypoint startup worker could not be scheduled. reason=busy')
    loading.finish()
    critical_startup_error_and_exit(None, 'StartupBusy')
    return 1

  logger.debug('Runtime worker scheduled.')
  app.aboutToQuit.connect(controller.shutdown)
  return app.exec()


def _run_primary_instance(app: QtWidgets.QApplication) -> int:
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

  snap = AppConfig.SETTINGS or snap
  return _start_startup(app, ui_cfg=ui_cfg, snap=snap, logger=_LOG, settings_service=settings_service)


def run(
  argv: list[str] | None = None, *, startup_report: Path | None = None, startup_profile: Path | None = None,
) -> int:
  if startup_profile is not None and startup_report is None:
    raise ValueError('A diagnostic profile requires a startup report.')
  app = _create_application(argv)
  app.setApplicationName(AppMeta.NAME)
  app.setProperty('startup_check', startup_report is not None)
  if startup_profile is not None:
    startup_profile = startup_profile.resolve()
    app.setProperty('startup_check_profile', startup_profile)
  if startup_report is not None:
    watchdog = QtCore.QTimer(app)
    watchdog.setSingleShot(True)

    def timeout() -> None:
      app.setProperty('startup_check_result', {'complete': False, 'stage': 'timeout'})
      lifecycle = app.property('desktop_lifecycle')
      if lifecycle is not None:
        lifecycle._begin_shutdown()
      else:
        app.quit()

    watchdog.timeout.connect(timeout)
    watchdog.start(120000)
    app.setProperty('startup_check_watchdog', watchdog)
  profile_dir = startup_profile or (
    Path(QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.GenericDataLocation))
    / 'PySkryptor'
  )
  guard = InstanceGuard(profile_dir, app)
  try:
    if not guard.acquire():
      return 2 if startup_report is not None else 0
  except RuntimeError as error:
    QtWidgets.QMessageBox.critical(None, AppMeta.NAME, str(error))
    return 1
  app.setProperty('instance_guard', guard)
  try:
    code = _run_primary_instance(app)
  finally:
    guard.release()
    app.setProperty('ui_shutting_down', True)
    app.setProperty('desktop_lifecycle', None)
    for widget in app.topLevelWidgets():
      widget.hide()
      widget.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    QtCore.qInstallMessageHandler(None)
    if app.property('restart_requested'):
      command = [sys.executable, *sys.argv[1:]] if getattr(sys, 'frozen', False) else [sys.executable, *sys.argv]
      subprocess.Popen(
        command, cwd=str(AppConfig.PATHS.INSTALL_ROOT_DIR), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
      )
  if startup_report is not None:
    result = app.property('startup_check_result') or {'complete': False, 'stage': 'startup'}
    result['shutdown_completed'] = code == 0
    write_startup_report(startup_report, result)
    if not result['complete']:
      return code or 1
  return code


if __name__ == '__main__':
  raise SystemExit(run())
