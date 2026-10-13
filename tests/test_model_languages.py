import json
import subprocess
import sys
from dataclasses import replace

from app.core.config.config import AppConfig
from app.runtime.capabilities import transcription_language_codes, translation_language_codes


def test_languages_come_from_model_tokens_without_control_tokens(tmp_path):
  AppConfig.PATHS = replace(
    AppConfig.PATHS,
    TRANSCRIPTION_ENGINE_DIR=tmp_path,
    TRANSLATION_ENGINE_DIR=tmp_path,
  )
  (tmp_path / 'tokenizer_config.json').write_text(
    json.dumps(
      {
        'additional_special_tokens': ['<|en|>', '<|pl|>', '<|yue|>', '<|notimestamps|>', '<|0.00|>'],
      }
    )
  )
  (tmp_path / 'special_tokens_map.json').write_text(
    json.dumps(
      {
        'additional_special_tokens': ['__en__', '__pl__', '__ast__', '__control__', '<pad>'],
      }
    )
  )
  assert transcription_language_codes() == {'en', 'pl', 'yue'}
  assert translation_language_codes() == {'en', 'pl', 'ast'}


def test_reading_real_model_language_files_never_imports_transformers():
  result = subprocess.run(
    [
      sys.executable,
      '-c',
      "from pathlib import Path; from app.core.config.config import AppConfig; "
      "from app.core.domain.entities import SettingsSnapshot; import json, sys; "
      "AppConfig.set_root_dir(Path.cwd()); "
      "defaults = json.loads(Path('app/features/settings/defaults.json').read_text()); "
      "AppConfig.initialize_from_snapshot(SettingsSnapshot(**defaults)); "
      "from app.runtime.capabilities import transcription_language_codes, translation_language_codes; "
      "transcription_language_codes(); translation_language_codes(); "
      "from app.features.transcription.runtime import pick_source_language; "
      "assert pick_source_language(default_lang='auto', detected_lang='en') == 'en'; "
      "assert 'torch' not in sys.modules and 'transformers' not in sys.modules",
    ],
    capture_output=True,
    text=True,
    timeout=10,
  )
  assert result.returncode == 0, result.stderr
