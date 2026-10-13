import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.application.coordinators.app_coordinator import AppCoordinator
from app.application.workers.runtime_state_worker import RuntimeStateWorker
from app.core.config.config import AppConfig
from app.core.domain.errors import OperationCancelled
from app.core.domain.state import AppRuntimeState
from app.features.models.switch_policy import rollback_payload
from app.runtime.types import EngineRuntimeState


def changed_snapshot():
  models = copy.deepcopy(AppConfig.SETTINGS.model)
  models['transcription_model']['engine_name'] = 'new-model'
  return replace(AppConfig.SETTINGS, model=models)


def test_failed_candidate_restores_previous_working_selection():
  previous = AppRuntimeState(AppConfig.SETTINGS, EngineRuntimeState(True), EngineRuntimeState(True))
  candidate = AppRuntimeState(
    changed_snapshot(), EngineRuntimeState(False, 'error.model.failed'), EngineRuntimeState(True)
  )
  payload = rollback_payload(previous, candidate)
  assert payload == {'engine': AppConfig.SETTINGS.engine, 'model': AppConfig.SETTINGS.model}
  payload['model']['transcription_model']['engine_name'] = 'mutated'
  assert AppConfig.SETTINGS.model['transcription_model']['engine_name'] != 'mutated'


def test_disabled_model_does_not_trigger_rollback():
  previous = AppRuntimeState(AppConfig.SETTINGS, EngineRuntimeState(True))
  candidate = AppRuntimeState(changed_snapshot(), EngineRuntimeState())
  assert rollback_payload(previous, candidate) is None


def test_no_working_previous_model_does_not_retry_indefinitely():
  previous = AppRuntimeState(AppConfig.SETTINGS, EngineRuntimeState(False, 'error.model.missing'))
  candidate = AppRuntimeState(changed_snapshot(), EngineRuntimeState(False, 'error.model.failed'))
  assert rollback_payload(previous, candidate) is None


def test_engine_change_waits_for_active_job_and_schedules_once(monkeypatch):
  coordinator = AppCoordinator()
  coordinator.set_runtime_state(AppRuntimeState(AppConfig.SETTINGS, EngineRuntimeState(True)))
  active = {'running': True}
  monkeypatch.setattr(coordinator._files, 'is_transcribing', lambda: active['running'])
  scheduled = []

  def schedule(snapshot, **_kwargs):
    scheduled.append(snapshot)
    coordinator.runtime.busy_changed.emit(True)
    return object()

  monkeypatch.setattr(coordinator.runtime, 'start', schedule)
  candidate = changed_snapshot()
  previous = AppConfig.SETTINGS
  coordinator._on_settings_applied(candidate)
  assert scheduled == [] and AppConfig.SETTINGS is previous
  active['running'] = False
  coordinator._on_runtime_activity_changed(False)
  assert scheduled == [candidate]
  assert coordinator._pending_runtime_snapshot is None
  coordinator.shutdown()
  coordinator.deleteLater()


def test_model_reload_keeps_active_download_temporary_files(tmp_path):
  AppConfig.PATHS = replace(
    AppConfig.PATHS,
    DATA_DIR=tmp_path,
    USER_CONFIG_DIR=tmp_path / 'config',
    LOGS_DIR=tmp_path / 'logs',
    DOWNLOADS_DIR=tmp_path / 'downloads',
    TRANSCRIPTIONS_DIR=tmp_path / 'transcripts',
    DOWNLOADS_TMP_DIR=tmp_path / 'downloads/._tmp',
    TRANSCRIPTIONS_TMP_DIR=tmp_path / 'transcripts/._tmp',
  )
  AppConfig.ensure_dirs()
  active_file = AppConfig.PATHS.DOWNLOADS_TMP_DIR / 'ongoing.part'
  active_file.write_bytes(b'partial download')
  worker = RuntimeStateWorker(engine_manager=SimpleNamespace(), snapshot=AppConfig.SETTINGS, labels={})
  worker._stage_ensure_dirs(lambda _value: None, AppRuntimeState())
  assert active_file.read_bytes() == b'partial download'


def test_runtime_cancellation_prevents_starting_any_engine():
  calls = []
  worker = RuntimeStateWorker(
    engine_manager=SimpleNamespace(warmup_role=lambda role: calls.append(role)),
    snapshot=AppConfig.SETTINGS,
    labels=dict(init='init', dirs='dirs', ffmpeg='ffmpeg', asr='asr', translation='translation'),
  )
  worker.cancel()
  with pytest.raises(OperationCancelled):
    worker._execute()
  assert calls == []
