import sys
from types import SimpleNamespace

import pytest

from app.core.config.config import AppConfig
from app.core.domain.errors import AppError, OperationCancelled
from app.features.transcription.host_runtime import TranscriptionHostRuntime
from app.runtime import client as client_module
from app.runtime.client import TranscriptionEngineClient
from app.runtime.packs import RuntimePack, available_runtime_packs


def test_separate_cpu_pack_is_selected_after_cuda_import_failure(monkeypatch, tmp_path):
  cuda = RuntimePack('cuda', tmp_path / 'cuda.exe', True)
  cpu = RuntimePack('cpu', tmp_path / 'cpu.exe', True)
  monkeypatch.setattr(client_module, 'available_runtime_packs', lambda: [cuda, cpu])
  client = TranscriptionEngineClient()
  attempts = []

  def start():
    attempts.append(client._pack.name)
    if client._pack.name == 'cuda':
      raise AppError('error.engine.host_start_failed', {'detail': 'CUDA DLL load failed'})

  monkeypatch.setattr(client, 'ensure_started', start)
  monkeypatch.setattr(client, 'request', lambda *_args, **_kwargs: {'ok': True})
  client.warmup()
  assert attempts == ['cuda', 'cpu']
  assert client._pack == cpu and 'CUDA DLL' in client._fallback_reason


def test_failed_cpu_probe_is_not_reported_as_ready(monkeypatch, tmp_path):
  cpu = RuntimePack('cpu', tmp_path / 'cpu.exe', True)
  monkeypatch.setattr(client_module, 'available_runtime_packs', lambda: [cpu])
  client = TranscriptionEngineClient()
  monkeypatch.setattr(client, 'ensure_started', lambda: None)
  monkeypatch.setattr(client, 'request', lambda *_args, **_kwargs: {'ok': False, 'detail': 'Invalid model weights'})
  with pytest.raises(AppError, match='host_warmup_failed'):
    client.warmup()


def test_cpu_preference_does_not_start_installed_cuda_pack(monkeypatch, tmp_path):
  monkeypatch.setattr(sys, 'frozen', True, raising=False)
  AppConfig.PATHS.INSTALL_ROOT_DIR = tmp_path
  cuda = tmp_path / 'runtimes/cuda/AIModelHost.exe'
  cuda.parent.mkdir(parents=True)
  cuda.touch()
  AppConfig.SETTINGS.engine['preferred_device'] = 'cpu'
  assert [pack.name for pack in available_runtime_packs()] == ['cpu']
  AppConfig.SETTINGS.engine['preferred_device'] = 'auto'
  assert [pack.name for pack in available_runtime_packs()] == ['cuda', 'cpu']
  assert '--device' in available_runtime_packs()[-1].command('transcription')


def test_gpu_model_probe_reloads_on_cpu_and_requires_success(monkeypatch):
  import app.features.transcription.host_runtime as module

  runtime = TranscriptionHostRuntime()
  devices = iter(['cuda', 'cpu'])
  probes = []

  def load():
    pipeline = SimpleNamespace(device=SimpleNamespace(type=next(devices)))
    runtime._loaded = SimpleNamespace(pipeline=pipeline)
    return pipeline

  def probe(pipeline):
    probes.append(pipeline.device.type)
    if pipeline.device.type == 'cuda':
      raise RuntimeError('No kernel image for this GPU')

  monkeypatch.setattr(runtime, '_ensure_pipeline', load)
  monkeypatch.setattr(module, 'probe_transcription', probe)
  assert not runtime.health()['ready']
  runtime.warmup()
  assert probes == ['cuda', 'cpu'] and runtime.health()['ready']
  assert AppConfig.DEVICE_ID == 'cpu' and AppConfig.DTYPE_ID == 'float32'


def test_cpu_model_failure_keeps_health_unready(monkeypatch):
  import app.features.transcription.host_runtime as module

  runtime = TranscriptionHostRuntime()
  runtime._loaded = SimpleNamespace(
    pipeline=SimpleNamespace(device=SimpleNamespace(type='cpu')),
    model_path=str(AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR),
  )
  monkeypatch.setattr(
    module, 'probe_transcription', lambda _pipeline: (_ for _ in ()).throw(RuntimeError('Bad weights'))
  )
  with pytest.raises(RuntimeError, match='Bad weights'):
    runtime.warmup()
  assert not runtime.health()['ready']


def test_final_shutdown_prevents_startup_worker_from_spawning_another_engine(monkeypatch):
  client = TranscriptionEngineClient()
  client.close()
  starts = []
  monkeypatch.setattr(client_module.subprocess, 'Popen', lambda *_args, **_kwargs: starts.append(True))
  with pytest.raises(OperationCancelled):
    client.warmup()
  assert starts == [] and client._host is None
