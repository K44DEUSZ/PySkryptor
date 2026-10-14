import copy
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.application.workers.live_worker import LiveError, LiveWorker
from app.core.config.config import AppConfig
from app.core.config.profiles import RuntimeProfiles
from app.core.domain.errors import OperationCancelled
from app.features.download.domain import DownloadError
from app.features.download.gateway import YtdlpGateway
from app.features.download.plan import DownloadPlanBuilder
from app.features.download.service import DownloadService
from app.features.transcription.live import LiveTranscriptionService
from app.platform import audio_input
from app.runtime.client import TranscriptionEngineClient, _HostIO
from app.runtime.manager import EngineManager
from app.runtime.types import RecognizeAudioResult


class SpeechEngine:
  def __init__(self):
    self.calls = []

  def recognize_audio(self, request, **_kwargs):
    self.calls.append(request)
    return RecognizeAudioResult('This is the final spoken sentence.', 'en')


def live_session(mode):
  engine = SpeechEngine()
  session = LiveTranscriptionService(
    transcription_engine=engine,
    translation_engine=SimpleNamespace(),
    source_language='en',
    target_language='pl',
    translate_enabled=False,
    cancel_check=lambda: False,
    output_mode=mode,
  )
  return engine, session


@pytest.mark.parametrize('mode', ['cumulative', 'stream'])
def test_stopping_live_preserves_speech_shorter_than_a_full_window(mode):
  engine, session = live_session(mode)
  speech = (np.sin(np.arange(16000) * 0.1) * 12000).astype('<i2').tobytes()
  session.push_pcm16(speech, level=0.5)
  updates = session.finalize()
  assert engine.calls and updates
  text = updates[-1].archive_source_text if mode == 'cumulative' else updates[-1].display_source_text
  assert text == 'This is the final spoken sentence.'


def test_slow_live_inference_has_a_bounded_audio_backlog():
  worker = LiveWorker(transcription_engine=SimpleNamespace(), translation_engine=SimpleNamespace())
  one_second = bytes(16000 * 2)
  with pytest.raises(LiveError, match='audio_backlog_full'):
    for _ in range(120):
      worker._queue_chunk(one_second, 0.5)
  pending_seconds = sum(len(chunk) for chunk, _level in worker._pending_chunks) / 32000
  assert worker._capture_backpressure
  assert pending_seconds <= 30, f'{pending_seconds} seconds pending in {len(worker._pending_chunks)} items'


def test_cancelled_request_does_not_wait_for_another_inference():
  client = TranscriptionEngineClient()
  lock = threading.Lock()
  lock.acquire()
  writes = []
  client._host = _HostIO(SimpleNamespace(
    poll=lambda: None,
    stdin=SimpleNamespace(write=writes.append, flush=lambda: None),
    stdout=SimpleNamespace(readline=lambda: '{"ok": true}\n'),
  ), lock)
  finished = threading.Event()
  outcome = []

  def request():
    try:
      client.request({'cmd': 'test'}, timeout_s=1, cancel_check=lambda: True)
    except OperationCancelled:
      outcome.append('cancelled')
    finally:
      finished.set()

  # Disposal is irrelevant to this test; only the occupied request lock is under test.
  client.dispose = lambda **_kwargs: None
  thread = threading.Thread(target=request, daemon=True)
  thread.start()
  try:
    cancelled_promptly = finished.wait(0.5)
  finally:
    lock.release()
    thread.join(timeout=2)
    client._host = None
  assert cancelled_promptly and outcome == ['cancelled'] and not writes


def test_missing_selected_microphone_is_not_silently_replaced(monkeypatch):
  default = SimpleNamespace(id=lambda: b'default', description=lambda: 'Laptop microphone', isNull=lambda: False)
  monkeypatch.setattr(audio_input, 'list_input_devices', lambda: [default])
  monkeypatch.setattr(audio_input.QtMultimedia.QMediaDevices, 'defaultAudioInput', lambda: default)
  _module, device = audio_input.resolve_input_device('Disconnected USB microphone')
  assert device is None


@pytest.mark.parametrize('content', [b'', b'not an mp4 file'])
def test_download_rejects_empty_or_corrupt_final_media(monkeypatch, tmp_path, content):
  AppConfig.PATHS = replace(AppConfig.PATHS, DOWNLOADS_TMP_DIR=tmp_path / 'stage')

  def extract(**kwargs):
    path = Path(kwargs['ydl_opts']['outtmpl'].replace('%(ext)s', 'mp4'))
    path.write_bytes(content)
    return {'filepath': str(path), 'ext': 'mp4'}, {}

  monkeypatch.setattr(YtdlpGateway, 'extract_info_with_fallback', extract)
  with pytest.raises(DownloadError):
    DownloadService.download(
      url='http://127.0.0.1/owned.mp4', kind='video', quality='auto', ext='mp4',
      out_dir=tmp_path / 'outputs', file_stem='owned', meta={},
    )


def test_network_settings_reach_the_downloader_backend():
  AppConfig.SETTINGS = replace(AppConfig.SETTINGS, network={
    'retries': 7, 'max_bandwidth_kbps': 128, 'concurrent_fragments': 5, 'http_timeout_s': 11,
  })
  options = YtdlpGateway.base_ydl_opts(url='http://127.0.0.1/owned.mp4', quiet=True, skip_download=False)
  assert options['retries'] == 7
  assert options['ratelimit'] == 128 * 1024
  assert options['concurrent_fragment_downloads'] == 5
  assert options['socket_timeout'] == 11


@pytest.mark.parametrize('profile,beams', [('fast', 1), ('balanced', 3), ('accurate', 5)])
def test_translation_profiles_control_actual_decoding_parameters(profile, beams):
  runtime = RuntimeProfiles.resolve_translation_runtime(profile=profile)
  assert runtime['num_beams'] == beams


def test_custom_transcription_controls_reach_generate_kwargs():
  from app.features.transcription.whisper import build_whisper_generate_kwargs

  runtime = RuntimeProfiles.resolve_transcription_runtime(profile='custom', overrides={
    'context_policy': 'off', 'silence_guard': 'strict', 'language_stability': 'strict',
    'chunk_length_s': 12, 'stride_length_s': 2,
  })
  kwargs = build_whisper_generate_kwargs(profile=runtime, source_language='pl', signal_kind='solid')
  assert runtime['chunk_length_s'] == 12 and runtime['stride_length_s'] == 2
  assert kwargs['condition_on_prev_tokens'] is False and kwargs['language'] == 'pl'
  assert kwargs['no_speech_threshold'] == 0.58


def test_selected_audio_quality_selects_the_requested_native_bitrate():
  track = {'candidates': [
    {'format_id': 'high', 'ext': 'm4a', 'abr': 129, 'has_audio': True, 'has_video': False},
    {'format_id': 'low', 'ext': 'm4a', 'abr': 49, 'has_audio': True, 'has_video': False},
  ]}
  plan = DownloadPlanBuilder.build_audio_plan(
    info={}, quality='49', ext_l='m4a', lang_base='', selected_audio_track=track,
    purpose='download', keep_output=True,
  )
  assert plan['format'].split('/')[0] == 'low' or any(
    processor.get('preferredquality') == '49' for processor in plan['postprocessors']
  )


def test_multiple_output_formats_keep_the_selected_audio_track(qt_app):
  from app.ui.panels.downloader_panel import DownloaderPanel, _Job

  panel = DownloaderPanel()
  panel._jobs = [_Job(
    key='owned', url='http://127.0.0.1/owned.mp4', output_types=['audio', 'video'],
    output_exts=['mp3', 'wav', 'mp4'], quality='480p', audio_track_id='polish', status='queued',
  )]
  items = panel._build_queue_items(['owned'])
  assert [(item.kind, item.ext, item.audio_track_id) for item in items] == [
    ('audio', 'mp3', 'polish'), ('audio', 'wav', 'polish'), ('video', 'mp4', 'polish'),
  ]
  panel.deleteLater()


def test_decoding_profile_change_does_not_reload_model_weights():
  models = copy.deepcopy(AppConfig.SETTINGS.model)
  models['translation_model']['profile'] = 'fast'
  candidate = replace(AppConfig.SETTINGS, model=models)
  assert not EngineManager.settings_require_reload(AppConfig.SETTINGS, candidate)
