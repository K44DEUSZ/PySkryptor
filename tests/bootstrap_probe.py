import faulthandler
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

profile = Path(sys.argv[1])
profile.mkdir(exist_ok=True)
trace_file = (profile / 'probe-trace.txt').open('w', encoding='utf-8')
faulthandler.dump_traceback_later(10, file=trace_file)


def milestone(name):
  trace_file.write(f'{time.perf_counter():.3f}: {name}\n')
  trace_file.flush()


milestone('imports')

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6 import QtCore

import app.bootstrap as bootstrap
from app.core.config.config import AppConfig
from app.features.settings.service import SettingsService
from app.platform.instance_guard import InstanceGuard

root = Path(__file__).resolve().parents[1]
service = SettingsService(
  defaults_path=root / 'app/features/settings/defaults.json', settings_path=profile / 'settings.json'
)
profile.mkdir(exist_ok=True)
service.restore_defaults()
service.save({'app': {'ui': {'welcome_dialog': {'show_on_startup': False}}}})
milestone('settings prepared')
original_configure = bootstrap._configure_application


def configure(app):
  milestone('configuring application')
  ui = original_configure(app)
  AppConfig.PATHS = replace(
    AppConfig.PATHS,
    DATA_DIR=profile,
    USER_CONFIG_DIR=profile,
    SETTINGS_FILE=profile / 'settings.json',
    LOGS_DIR=profile,
    APP_LOG_PATH=profile / 'app.log',
    CRASH_LOG_PATH=profile / 'crash.log',
    AI_MODELS_DIR=profile / 'models',
    DOWNLOADS_DIR=profile / 'downloads',
    TRANSCRIPTIONS_DIR=profile / 'transcriptions',
    DOWNLOADS_TMP_DIR=profile / 'downloads/._tmp',
    TRANSCRIPTIONS_TMP_DIR=profile / 'transcriptions/._tmp',
  )
  return ui


bootstrap._configure_application = configure
bootstrap.SettingsService = lambda: service
bootstrap.InstanceGuard = lambda _profile, app: InstanceGuard(profile, app)
bootstrap._show_runtime_welcome_dialog = lambda *_args: None
original_create = bootstrap._create_application
state = {'ready': False}


def create(argv):
  milestone('creating QApplication')
  app = original_create(argv)
  milestone('QApplication created')
  if '--check-startup' in sys.argv:
    QtCore.QTimer.singleShot(25000, lambda: os._exit(3))
    return app
  timer = QtCore.QTimer(app)
  timer.setInterval(100)

  def finish():
    lifecycle = app.property('desktop_lifecycle')
    if lifecycle is None or ('--cancel-startup' not in sys.argv and lifecycle._window.objectName() != 'MainWindow'):
      return
    timer.stop()
    timer.timeout.disconnect(finish)
    timer.deleteLater()
    state['ready'] = True
    milestone('begin shutdown')
    lifecycle._begin_shutdown()

  timer.timeout.connect(finish)
  timer.start()
  QtCore.QTimer.singleShot(25000, lambda: os._exit(3))
  return app


bootstrap._create_application = create
report_path = profile / 'startup.json' if '--check-startup' in sys.argv else None
code = bootstrap.run([], startup_report=report_path)
milestone('bootstrap returned')
faulthandler.cancel_dump_traceback_later()
trace_file.close()
if report_path is not None:
  report = json.loads(report_path.read_text(encoding='utf-8'))
  assert report['complete'] and report['shutdown_completed'] and report['main_window_visible'], report
  assert report['qt_platform'] == 'offscreen', report
else:
  assert state['ready']
assert code == 0
assert 'torch' not in sys.modules
assert not (profile / 'application.lock').exists()
print('startup and shutdown complete', flush=True)
