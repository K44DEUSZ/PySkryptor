import json
import struct
import zipfile
from dataclasses import replace

import pytest

from app.core.config.config import AppConfig
from app.core.domain.errors import AppError
from app.runtime.model_catalog import ModelCatalogService
from app.runtime.resolution import EngineCatalog, EngineResolver
from app.ui.panels.settings_panel import SettingsPanel


def weight_file(path):
  header = json.dumps({'test.weight': {'dtype': 'F32', 'shape': [1], 'data_offsets': [0, 4]}}).encode()
  header += b' ' * (-len(header) % 8)
  path.write_bytes(struct.pack('<Q', len(header)) + header + struct.pack('<f', 1.0))


def model_folder(path, family='whisper'):
  path.mkdir(parents=True)
  (path / 'config.json').write_text(json.dumps({'model_type': family}))
  if family == 'whisper':
    (path / 'preprocessor_config.json').write_text('{}')
    (path / 'tokenizer.json').write_text('{}')
  else:
    (path / 'vocab.json').write_text('{}')
    (path / 'sentencepiece.bpe.model').write_bytes(b'placeholder')
  weight_file(path / 'model.safetensors')
  return path


@pytest.mark.parametrize('family, role', [('whisper', 'transcription'), ('m2m_100', 'translation')])
def test_registers_external_models_without_copying_weights(tmp_path, family, role):
  folder = model_folder(tmp_path / 'external/My model', family)
  catalog_path = tmp_path / 'profile/model_catalog.json'
  catalog = ModelCatalogService(catalog_path)
  model = catalog.register(folder, expected_role=role)
  assert model.label == 'My model' and model.role == role
  assert ModelCatalogService(catalog_path).registered_paths()[model.id]['path'] == str(folder.resolve())
  assert folder.is_dir() and not (tmp_path / 'profile/model.safetensors').exists()
  assert catalog.register(folder, expected_role=role).id == model.id
  assert len(catalog.registered_paths()) == 1


def test_rejects_wrong_role_and_missing_tokenizer(tmp_path):
  folder = model_folder(tmp_path / 'whisper')
  catalog = ModelCatalogService(tmp_path / 'catalog.json')
  with pytest.raises(AppError, match='import_unsupported'):
    catalog.register(folder, expected_role='translation')
  (folder / 'tokenizer.json').unlink()
  with pytest.raises(AppError, match='import_missing_file'):
    catalog.register(folder, expected_role='transcription')
  assert not (tmp_path / 'catalog.json').exists()


def test_rejects_custom_model_code(tmp_path):
  folder = model_folder(tmp_path / 'custom')
  (folder / 'config.json').write_text(json.dumps({'model_type': 'whisper', 'auto_map': {'AutoModel': 'custom.Model'}}))
  with pytest.raises(AppError, match='import_custom_code'):
    ModelCatalogService().inspect(folder)


def test_sharded_weights_require_every_shard_and_reject_traversal(tmp_path):
  folder = model_folder(tmp_path / 'sharded')
  (folder / 'model.safetensors').rename(folder / 'part-1.safetensors')
  index = folder / 'model.safetensors.index.json'
  index.write_text(json.dumps({'weight_map': {'a': 'part-1.safetensors', 'b': 'part-2.safetensors'}}))
  with pytest.raises(AppError, match='import_missing_file'):
    ModelCatalogService().inspect(folder)
  weight_file(folder / 'part-2.safetensors')
  assert ModelCatalogService().inspect(folder).role == 'transcription'
  index.write_text(json.dumps({'weight_map': {'a': '../outside.safetensors'}}))
  with pytest.raises(AppError, match='import_invalid_file'):
    ModelCatalogService().inspect(folder)


def test_invalid_weights_do_not_replace_existing_catalog(tmp_path):
  folder = model_folder(tmp_path / 'valid')
  catalog_path = tmp_path / 'catalog.json'
  service = ModelCatalogService(catalog_path)
  service.register(folder, expected_role='transcription')
  original = catalog_path.read_bytes()
  (folder / 'model.safetensors').write_bytes(b'broken')
  with pytest.raises(AppError, match='import_invalid_file'):
    service.register(folder, expected_role='transcription')
  assert catalog_path.read_bytes() == original


def test_catalog_discovery_resolves_external_path_and_ui_label(tmp_path, qt_app):
  AppConfig.PATHS = replace(AppConfig.PATHS, USER_CONFIG_DIR=tmp_path / 'profile', AI_MODELS_DIR=tmp_path / 'installed')
  folder = model_folder(tmp_path / 'own/Whisper lokalny')
  model = ModelCatalogService().register(folder, expected_role='transcription')
  assert model.id in EngineCatalog.local_model_names('transcription')
  snapshot = replace(
    AppConfig.SETTINGS,
    model={
      **AppConfig.SETTINGS.model,
      'transcription_model': {**AppConfig.SETTINGS.model['transcription_model'], 'engine_name': model.id},
    },
  )
  AppConfig.initialize_from_snapshot(snapshot)
  assert AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR == folder.resolve()
  assert EngineResolver.active_engine_name(task='transcription') == model.id
  assert EngineCatalog.model_label(model.id) == 'Whisper lokalny'
  panel = SettingsPanel()
  panel.on_settings_loaded(snapshot)
  panel.on_model_added(model)
  assert panel.cmb_trans_engine.currentData() == model.id
  assert panel.cmb_trans_engine.currentText() == 'Whisper lokalny'
  assert 'Zapisz ustawienia' in panel._model_import_labels['transcription'].text()
  panel.deleteLater()


def test_weight_changes_change_fingerprint(tmp_path):
  folder = model_folder(tmp_path / 'model')
  service = ModelCatalogService()
  before = service.inspect(folder)
  with (folder / 'model.safetensors').open('ab') as stream:
    stream.write(b'additional data')
  assert service.inspect(folder).fingerprint != before.fingerprint


def test_bin_validation_does_not_unpickle_weights(tmp_path):
  folder = model_folder(tmp_path / 'm2m', 'm2m_100')
  (folder / 'model.safetensors').unlink()
  with zipfile.ZipFile(folder / 'pytorch_model.bin', 'w') as archive:
    archive.writestr('archive/data.pkl', b'This payload must not be executed during import')
  assert ModelCatalogService().inspect(folder).family == 'm2m_100'
