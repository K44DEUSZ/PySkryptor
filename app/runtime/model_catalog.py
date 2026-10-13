# app/runtime/model_catalog.py
from __future__ import annotations

import hashlib
import json
import logging
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config.config import AppConfig
from app.core.domain.errors import AppError
from app.runtime.model_families import model_role

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalModel:
  """A validated local model reference, independent of inference readiness."""

  id: str
  label: str
  path: Path
  family: str
  role: str
  fingerprint: str


class ModelCatalogService:
  """Validate supported local model folders and persist references without copying weights."""

  def __init__(self, catalog_path: Path | None = None) -> None:
    self._path = catalog_path or AppConfig.PATHS.USER_CONFIG_DIR / 'model_catalog.json'

  @staticmethod
  def _read_json(path: Path) -> dict[str, Any]:
    try:
      if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('JSON file exceeds 8 MiB')
      value = json.loads(path.read_text(encoding='utf-8'))
      if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
      return value
    except (OSError, ValueError) as error:
      raise AppError('error.model.import_invalid_file', {'path': str(path), 'detail': str(error)}) from error

  @staticmethod
  def _required_file(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
      raise AppError('error.model.import_invalid_file', {'path': name, 'detail': 'Path escapes the model folder'})
    path = (root / name).resolve()
    if not path.is_file() or path.stat().st_size == 0:
      raise AppError('error.model.import_missing_file', {'path': str(path)})
    return path

  @classmethod
  def _weights(cls, root: Path) -> list[Path]:
    single = root / 'model.safetensors'
    if single.is_file():
      return [cls._required_file(root, single.name)]
    index_name = 'model.safetensors.index.json'
    if not (root / index_name).is_file():
      if (root / 'pytorch_model.bin').is_file():
        return [cls._required_file(root, 'pytorch_model.bin')]
      if (root / 'pytorch_model.bin.index.json').is_file():
        index_name = 'pytorch_model.bin.index.json'
    index = cls._read_json(cls._required_file(root, index_name))
    mapping = index.get('weight_map')
    if not isinstance(mapping, dict) or not mapping or not all(isinstance(name, str) for name in mapping.values()):
      raise AppError('error.model.import_invalid_file', {'path': str(root), 'detail': 'Invalid weight_map'})
    return [cls._required_file(root, name) for name in sorted(set(mapping.values()))]

  @staticmethod
  def _validate_weight_header(path: Path) -> None:
    try:
      if path.suffix == '.bin':
        with zipfile.ZipFile(path) as archive:
          if not any(name.endswith('/data.pkl') for name in archive.namelist()):
            raise ValueError('Not a PyTorch weight archive')
        return
      with path.open('rb') as stream:
        raw_size = stream.read(8)
        if len(raw_size) != 8:
          raise ValueError('Incomplete safetensors header')
        header_size = struct.unpack('<Q', raw_size)[0]
        payload_size = path.stat().st_size - 8 - header_size
        if header_size <= 0 or header_size > 16 * 1024 * 1024 or payload_size <= 0:
          raise ValueError('Invalid safetensors length')
        header = json.loads(stream.read(header_size))
        tensors = [value for key, value in header.items() if key != '__metadata__']
        if not tensors:
          raise ValueError('No tensors in weight file')
        for tensor in tensors:
          start, end = tensor['data_offsets']
          if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= payload_size:
            raise ValueError('Invalid tensor data range')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error, zipfile.BadZipFile) as error:
      raise AppError('error.model.import_invalid_file', {'path': str(path), 'detail': str(error)}) from error

  @staticmethod
  def fingerprint(folder: Path) -> str:
    """Fingerprint configuration and file metadata without hashing multi-gigabyte weights."""
    fingerprint = hashlib.sha256((folder / 'config.json').read_bytes())
    for path in sorted(folder.iterdir()):
      if path.is_file() and path.suffix in {'.json', '.safetensors', '.bin', '.txt', '.model'}:
        stat = path.stat()
        fingerprint.update(f'{path.name}:{stat.st_size}:{stat.st_mtime_ns}'.encode('utf-8'))
    return fingerprint.hexdigest()

  def inspect(self, folder: Path, *, expected_role: str | None = None) -> LocalModel:
    """Check file structure; successful validation does not claim that inference works."""
    root = folder.resolve()
    config_path = self._required_file(root, 'config.json')
    config = self._read_json(config_path)
    family = str(config.get('model_type', '')).lower()
    role = model_role(family)
    if not role or (expected_role is not None and role != expected_role):
      raise AppError('error.model.import_unsupported', {'family': family or 'unknown'})
    if config.get('auto_map'):
      raise AppError('error.model.import_custom_code', {'path': str(root)})
    if role == 'transcription':
      self._required_file(root, 'preprocessor_config.json')
      if (root / 'tokenizer.json').is_file():
        self._required_file(root, 'tokenizer.json')
      else:
        for name in ('vocab.json', 'merges.txt'):
          self._required_file(root, name)
    else:
      for name in ('vocab.json', 'sentencepiece.bpe.model'):
        self._required_file(root, name)
    weights = self._weights(root)
    for path in weights:
      self._validate_weight_header(path)
    identity = hashlib.sha256(str(root).casefold().encode('utf-8')).hexdigest()[:24]
    return LocalModel('local:' + identity, root.name, root, family, role, self.fingerprint(root))

  def _records(self) -> dict[str, Any]:
    if not self._path.exists():
      return {}
    data = self._read_json(self._path)
    if data.get('schema_version') != 1 or not isinstance(data.get('models'), dict):
      raise AppError('error.model.import_invalid_file', {'path': str(self._path), 'detail': 'Invalid catalog schema'})
    return data['models']

  def registered_paths(self) -> dict[str, dict[str, Any]]:
    """Return registered references, retaining unavailable folders for diagnostics."""
    try:
      return {key: value for key, value in self._records().items() if isinstance(value, dict)}
    except AppError as error:
      _LOG.warning('Model catalog unavailable. detail=%s', error)
      return {}

  def register(self, folder: Path, *, expected_role: str) -> LocalModel:
    model = self.inspect(folder, expected_role=expected_role)
    records = self._records()
    records[model.id] = {
      'label': model.label,
      'path': str(model.path),
      'family': model.family,
      'role': model.role,
      'fingerprint': model.fingerprint,
    }
    self._path.parent.mkdir(parents=True, exist_ok=True)
    pending = self._path.with_suffix('.tmp')
    try:
      pending.write_text(
        json.dumps({'schema_version': 1, 'models': records}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
      )
      pending.replace(self._path)
    finally:
      pending.unlink(missing_ok=True)
    return model
