import json
import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6 import QtWidgets

from app.core.config.config import AppConfig
from app.core.domain.entities import SettingsSnapshot
from app.core.runtime.localization import load

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='session')
def qt_app():
  app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
  app.setQuitOnLastWindowClosed(False)
  return app


@pytest.fixture(autouse=True)
def app_config(qt_app, tmp_path):
  AppConfig.set_root_dir(ROOT)
  AppConfig.PATHS = replace(AppConfig.PATHS, AI_MODELS_DIR=tmp_path / 'models', USER_CONFIG_DIR=tmp_path)
  defaults = json.loads((ROOT / 'app/features/settings/defaults.json').read_text(encoding='utf-8'))
  AppConfig.initialize_from_snapshot(SettingsSnapshot(**defaults))
  load(ROOT / 'assets/locales', 'pl')
  yield
  qt_app.processEvents()
