import builtins
import json

import pytest

from app.main import main


def test_diagnostic_records_qt_load_failure_before_logging_is_available(monkeypatch, tmp_path):
  original_import = builtins.__import__

  def importing(name, *args, **kwargs):
    if name == 'app.bootstrap':
      raise ImportError('DLL load failed while importing QtCore: procedure not found')
    return original_import(name, *args, **kwargs)

  monkeypatch.setattr(builtins, '__import__', importing)
  report_path = tmp_path / 'startup.json'
  assert main(['--check-startup', str(report_path)]) == 1
  report = json.loads(report_path.read_text(encoding='utf-8'))
  assert not report['complete'] and report['stage'] == 'imports'
  assert 'QtCore' in report['error'] and report['error_type'] == 'ImportError'


def test_isolated_profile_is_only_available_for_startup_diagnostics(tmp_path):
  with pytest.raises(SystemExit) as error:
    main(['--check-startup-profile', str(tmp_path)])
  assert error.value.code == 2


def test_diagnostic_profile_and_report_are_passed_to_bootstrap(monkeypatch, tmp_path):
  from app import bootstrap

  calls = []

  def run(argv, **options):
    calls.append(options)
    return 0

  monkeypatch.setattr(bootstrap, 'run', run)
  report = tmp_path / 'startup.json'
  profile = tmp_path / 'profile'
  assert main(['--check-startup', str(report), '--check-startup-profile', str(profile)]) == 0
  assert calls == [{'startup_report': report, 'startup_profile': profile}]
