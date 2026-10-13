import os

import pytest

from tools import build_bundle as module
from tools.build_bundle import build_environment, validate_binary_sources


def test_build_does_not_inherit_foreign_dependency_search_paths(monkeypatch, tmp_path):
  windows = tmp_path / 'Windows'
  foreign = str(tmp_path / 'image-tool')
  monkeypatch.setattr(module.sys, 'platform', 'win32')
  monkeypatch.setenv('SystemRoot', str(windows))
  monkeypatch.setenv('PATH', foreign)
  monkeypatch.setenv('PYTHONPATH', foreign)
  environment = build_environment()
  assert foreign not in environment['PATH'].split(os.pathsep)
  assert str((windows / 'System32').resolve()) in environment['PATH'].split(os.pathsep)
  assert 'PYTHONPATH' not in environment
  assert os.environ['PATH'] == foreign


@pytest.mark.parametrize('entry_type', ['BINARY', 'EXTENSION'])
def test_foreign_dll_from_tool_path_is_rejected(tmp_path, entry_type):
  environment = tmp_path / 'venv'
  foreign_dll = tmp_path / 'image-tool/icuuc.dll'
  toc = tmp_path / 'Analysis-00.toc'
  toc.write_text(repr([('icuuc.dll', str(foreign_dll), entry_type)]), encoding='utf-8')
  with pytest.raises(ValueError, match='outside the build environment'):
    validate_binary_sources(toc, [environment])


def test_selected_environment_and_system_dlls_are_allowed(tmp_path):
  environment = tmp_path / 'venv'
  windows = tmp_path / 'Windows'
  toc = tmp_path / 'Analysis-00.toc'
  toc.write_text(
    repr(
      [
        ('Qt6Core.dll', str(environment / 'Lib/site-packages/PyQt6/Qt6/bin/Qt6Core.dll'), 'BINARY'),
        ('icuuc.dll', str(windows / 'System32/icuuc.dll'), 'BINARY'),
      ]
    ),
    encoding='utf-8',
  )
  validate_binary_sources(toc, [environment, windows])
