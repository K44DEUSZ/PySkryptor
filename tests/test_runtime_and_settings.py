import copy
import json
import subprocess
import sys
import threading
from pathlib import Path

from app.core.config.config import AppConfig
from app.core.config.paths import PathCatalog
from app.core.domain.state import AppRuntimeState
from app.core.runtime.localization import list_locales
from app.features.settings.service import SettingsService
from app.runtime.client import TranscriptionEngineClient, _HostIO
from app.runtime.types import EngineRuntimeState
from app.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]


def test_gui_bootstrap_does_not_import_torch_or_old_qt():
  result = subprocess.run(
    [
      sys.executable,
      '-c',
      "import app.bootstrap, sys; assert 'torch' not in sys.modules; assert 'PyQt5' not in sys.modules",
    ],
    capture_output=True,
    text=True,
    timeout=35,
  )
  assert result.returncode == 0, result.stderr


def test_complete_gui_startup_and_cooperative_shutdown_without_ai_runtime(tmp_path):
  result = subprocess.run(
    [sys.executable, str(Path(__file__).with_name('bootstrap_probe.py')), str(tmp_path)],
    capture_output=True,
    text=True,
    timeout=35,
  )
  assert result.returncode == 0, result.stderr
  assert 'startup and shutdown complete' in result.stdout


def test_startup_diagnostic_reports_window_creation_and_shutdown(tmp_path):
  result = subprocess.run(
    [sys.executable, str(Path(__file__).with_name('bootstrap_probe.py')), str(tmp_path), '--check-startup'],
    capture_output=True,
    text=True,
    timeout=35,
  )
  assert result.returncode == 0, result.stderr
  assert 'startup and shutdown complete' in result.stdout


def test_quit_during_startup_waits_for_worker_cleanup(tmp_path):
  result = subprocess.run(
    [sys.executable, str(Path(__file__).with_name('bootstrap_probe.py')), str(tmp_path), '--cancel-startup'],
    capture_output=True,
    text=True,
    timeout=35,
  )
  assert result.returncode == 0, result.stderr
  assert 'startup and shutdown complete' in result.stdout


def test_engine_shutdown_terminates_and_reaps_a_busy_child_process():
  process = subprocess.Popen(
    [sys.executable, '-c', 'import time; time.sleep(60)'],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
  )
  client = TranscriptionEngineClient()
  lock = threading.Lock()
  lock.acquire()
  client._host = _HostIO(process, lock)
  try:
    client.shutdown()
    assert process.poll() is not None and client._host is None
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed
  finally:
    lock.release()
    if process.poll() is None:
      process.kill()
      process.wait(timeout=5)


def test_settings_roundtrip_preserves_new_formats_and_background_options(tmp_path):
  service = SettingsService(
    defaults_path=ROOT / 'app/features/settings/defaults.json', settings_path=tmp_path / 'settings.json'
  )
  service.restore_defaults()
  saved = service.save(
    {
      'app': {'ui': {'background': {'minimize_to_tray': True, 'close_to_tray': False}}},
      'transcription': {'output_formats': ['txt', 'srt', 'vtt', 'json', 'csv', 'md']},
    }
  )
  assert saved.app['ui']['background'] == {
    'minimize_to_tray': True, 'close_to_tray': False, 'close_behavior_chosen': False,
  }
  assert list(saved.transcription['output_formats']) == ['txt', 'srt', 'vtt', 'json', 'csv', 'md']
  assert saved.app['ui']['welcome_dialog']['show_on_startup']


def test_older_german_ui_setting_falls_back_and_languages_are_pl_en(tmp_path):
  service = SettingsService(
    defaults_path=ROOT / 'app/features/settings/defaults.json', settings_path=tmp_path / 'settings.json'
  )
  service.restore_defaults()
  assert service.save({'app': {'language': 'de'}}).app['language'] == 'auto'
  assert {code for code, _name in list_locales(ROOT / 'assets/locales')} == {'pl', 'en'}


def test_removed_memory_flag_is_not_exposed_or_saved(tmp_path):
  service = SettingsService(
    defaults_path=ROOT / 'app/features/settings/defaults.json',
    settings_path=tmp_path / 'settings.json',
  )
  service.restore_defaults()
  saved = service.save({'engine': {'low_cpu_mem_usage': False}})
  assert 'low_cpu_mem_usage' not in saved.engine


def build_paths(root, install_root):
  return PathCatalog.build(
    root, install_root_dir=install_root, app_log_name='app.log', crash_log_name='crash.log', missing_value='missing'
  )


def test_installed_user_data_is_outside_program_directory(monkeypatch, tmp_path):
  monkeypatch.setattr(sys, 'frozen', True, raising=False)
  monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'profile'))
  paths = build_paths(ROOT, tmp_path / 'Program Files/PySkryptor')
  assert paths.DATA_DIR == tmp_path / 'profile/PySkryptor'
  PathCatalog.ensure_runtime_dirs(paths)
  assert paths.USER_CONFIG_DIR.is_dir()
  assert not paths.INSTALL_ROOT_DIR.exists()


def test_portable_marker_keeps_data_next_to_executable(monkeypatch, tmp_path):
  monkeypatch.setattr(sys, 'frozen', True, raising=False)
  (tmp_path / 'portable.flag').touch()
  paths = build_paths(ROOT, tmp_path)
  assert paths.DATA_DIR == tmp_path / 'userdata'


def test_main_panels_construct_with_qt6_and_icons(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  assert window.tabs.count() == 6
  assert all(not window.tabs.tabIcon(index).isNull() for index in range(6))
  assert not window.files_panel.btn_add_files.icon().isNull()
  assert len(window.files_panel._out_checks) == 7
  window.settings_panel.on_settings_loaded(AppConfig.SETTINGS)
  before = copy.deepcopy(window.settings_panel._collect_app_payload())
  window.settings_panel.tg_minimize_to_tray.setChecked(True)
  after = window.settings_panel._collect_app_payload()
  assert before['ui']['background']['minimize_to_tray'] is False
  assert after['ui']['background']['minimize_to_tray'] is True
  window.close()
  window.deleteLater()


def test_device_badge_follows_actual_role_and_selected_mode(qt_app, monkeypatch):
  window = MainWindow()
  panel = window.files_panel
  monkeypatch.setattr(panel, '_translation_runtime_available', lambda: True)
  panel.on_runtime_state_changed(
    AppRuntimeState(
      AppConfig.SETTINGS,
      EngineRuntimeState(True, hardware={'DEVICE_FRIENDLY_NAME': 'CPU'}),
      EngineRuntimeState(True, hardware={'DEVICE_FRIENDLY_NAME': 'GPU'}),
    )
  )
  assert panel.model_info.lbl_device_value.text() == 'CPU'
  panel.tg_mode.set_first_checked(False)
  panel._refresh_mode_badge()
  assert panel.model_info.lbl_device_value.text() == 'CPU / GPU'
  window.live_panel._first_shown = True
  window.deleteLater()


def test_pl_en_keys_and_parameters_match():
  def flatten(data, prefix=''):
    items = {}
    for key, value in data.items():
      name = f'{prefix}.{key}' if prefix else key
      items.update(flatten(value, name) if isinstance(value, dict) else {name: value})
    return items

  import string

  messages = [
    flatten(json.loads((ROOT / f'assets/locales/{language}.json').read_text(encoding='utf-8')))
    for language in ('pl', 'en')
  ]
  assert messages[0].keys() == messages[1].keys()
  for key, value in messages[0].items():
    pl_fields = {field for _text, field, _spec, _conversion in string.Formatter().parse(value) if field}
    en_fields = {field for _text, field, _spec, _conversion in string.Formatter().parse(messages[1][key]) if field}
    assert pl_fields == en_fields, key
