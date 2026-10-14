import subprocess
import sys
import threading
import time
import wave
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from app.application.workers.live_worker import LiveError, LiveWorker
from app.core.config.config import AppConfig
from app.core.domain.errors import OperationCancelled
from app.core.infrastructure.command_runner import CommandRunner
from app.features.download.inventory import TrackInventory, TrackLabelHeuristics
from app.features.download.media_validation import validate_media
from app.features.transcription.live import LiveTranscriptionService
from app.features.translation.runtime_request import TranslationRuntimeConfig
from app.runtime.types import RecognizeAudioResult
from app.ui.components.source_table import SourceTable
from app.ui.panels.downloader_panel import DownloaderPanel, _Job
from app.ui.support.audio_track_labels import build_audio_track_display_map


def speech(seconds=1):
  return (np.sin(np.arange(16000 * seconds) * 0.1) * 12000).astype('<i2').tobytes()


def session_with_results(texts, *, mode='cumulative', translate=False):
  results = iter(texts)
  engine = SimpleNamespace(recognize_audio=lambda *_args, **_kwargs: RecognizeAudioResult(next(results), 'en'))
  session = LiveTranscriptionService(
    transcription_engine=engine,
    translation_engine=SimpleNamespace(translate_text=lambda *_args, **_kwargs: 'Dzień dobry.'),
    source_language='en',
    target_language='pl',
    translate_enabled=translate,
    cancel_check=lambda: False,
    output_mode=mode,
    runtime_profile={'chunk_length_s': 2, 'stride_length_s': 1},
  )
  session._translation._runtime = TranslationRuntimeConfig(256, 1000, 3, 0)
  return session


@pytest.mark.parametrize('mode', ['cumulative', 'stream'])
def test_overlapping_audio_corrects_an_earlier_partial_sentence(mode):
  session = session_with_results(
    [
      'Hello. We are testing the old output.',
      'We are testing the corrected output.',
      'the corrected output.',
    ],
    mode=mode,
  )
  updates = []
  for _ in range(3):
    updates.extend(session.push_pcm16(speech(), level=0.5))
  updates.extend(session.finalize())
  result = updates[-1]
  text = result.archive_source_text if mode == 'cumulative' else result.display_source_text
  assert text == 'Hello. We are testing the corrected output.'


def test_the_same_sentence_spoken_after_silence_is_kept_twice():
  sentence = 'Please repeat this sentence.'
  session = session_with_results([sentence, sentence])
  for _ in range(2):
    session.push_pcm16(speech(), level=0.5)
    session.push_pcm16(bytes(32000), level=0)
  result = session.finalize()[-1]
  assert result.archive_source_text == sentence + ' ' + sentence


def test_overlapping_audio_is_not_sent_back_as_a_recognition_prompt():
  requests = []
  session = session_with_results([])

  def recognize(request, **_kwargs):
    requests.append(request)
    return RecognizeAudioResult('We are testing the recognition context.', 'en')

  session._transcription_engine = SimpleNamespace(recognize_audio=recognize)
  for _ in range(3):
    session.push_pcm16(speech(), level=0.5)
  assert len(requests) == 2 and all(request.previous_text == '' for request in requests)


@pytest.mark.parametrize('mode', ['cumulative', 'stream'])
def test_final_short_utterance_is_also_translated(mode):
  session = session_with_results(['Good morning.'], mode=mode, translate=True)
  session.push_pcm16(speech(), level=0.5)
  result = session.finalize()[-1]
  assert (result.archive_target_text if mode == 'cumulative' else result.display_target_text) == 'Dzień dobry.'


def test_live_backpressure_pauses_capture_and_resumes_only_after_catching_up():
  worker = LiveWorker(transcription_engine=SimpleNamespace(), translation_engine=SimpleNamespace())
  state = {'value': 'active'}
  worker._qt_multimedia = SimpleNamespace(
    QAudio=SimpleNamespace(
      State=SimpleNamespace(
        ActiveState='active',
        SuspendedState='suspended',
      )
    )
  )
  worker._audio_in = SimpleNamespace(
    state=lambda: state['value'],
    suspend=lambda: state.update(value='suspended'),
    resume=lambda: state.update(value='active'),
  )
  for _ in range(10):
    worker._queue_chunk(speech(), 0.5)
  worker._update_audio_capture_state()
  assert state['value'] == 'suspended' and worker._status_key == 'status.live_catching_up'
  while worker._pending_audio_bytes > 32000 * 2:
    worker._pop_chunk()
  worker._update_audio_capture_state()
  assert state['value'] == 'active' and worker._status_key == 'status.listening'


def test_live_overflow_can_be_recovered_as_audio_without_discarding_the_last_chunk(tmp_path):
  AppConfig.PATHS = replace(AppConfig.PATHS, TRANSCRIPTIONS_DIR=tmp_path)
  worker = LiveWorker(transcription_engine=SimpleNamespace(), translation_engine=SimpleNamespace())
  worker._max_pending_audio_bytes = 32000
  worker._queue_chunk(speech(), 0.5)
  with pytest.raises(LiveError):
    worker._queue_chunk(speech(), 0.5)
  worker._save_pending_audio()
  with wave.open(str(worker._recovery_audio_path)) as recovered:
    assert recovered.getframerate() == 16000 and recovered.getnframes() == 32000
    assert recovered.readframes(32000) == speech() * 2


def test_stopping_a_busy_live_worker_does_not_block_the_event_loop():
  worker = LiveWorker(transcription_engine=SimpleNamespace(), translation_engine=SimpleNamespace())
  release = threading.Event()
  worker._inference_thread = threading.Thread(target=release.wait, daemon=True)
  worker._inference_thread.start()
  try:
    started = time.monotonic()
    worker._complete_stop_sequence()
    assert time.monotonic() - started < 0.2
    assert not worker.is_finalized()
  finally:
    release.set()
    worker._inference_thread.join(2)
  worker._complete_stop_sequence()
  assert worker.is_finalized()


def test_media_tools_drain_output_larger_than_an_os_pipe_buffer():
  result = CommandRunner.run(
    [sys.executable, '-c', "import sys; sys.stdout.write('a' * 200000); sys.stderr.write('b' * 200000)"],
    timeout_s=5,
    cancel_check=lambda: False,
  )
  assert len(result.stdout) == len(result.stderr) == 200000


def test_cancelling_media_inspection_terminates_and_reaps_its_process(monkeypatch):
  from app.features.download.media_validation import run_media_command

  processes = []
  original = subprocess.Popen

  def record(*args, **kwargs):
    process = original(*args, **kwargs)
    processes.append(process)
    return process

  monkeypatch.setattr(subprocess, 'Popen', record)
  started = time.monotonic()
  with pytest.raises(OperationCancelled):
    run_media_command(
      [sys.executable, '-c', 'import time; time.sleep(60)'], cancel_check=lambda: time.monotonic() - started >= 0.15
    )
  assert time.monotonic() - started < 2
  assert processes and all(process.poll() is not None for process in processes)


def test_direct_probe_preserves_cancellation_instead_of_reporting_a_download_failure(monkeypatch):
  from app.features.download import direct_media
  from app.features.download.gateway import YtdlpGateway
  from app.features.download.service import DownloadService

  info = {
    'extractor': 'generic',
    'extractor_key': 'Generic',
    'id': 'owned',
    'formats': [
      {'url': 'http://127.0.0.1/owned.mp4', 'format_id': 'mp4', 'ext': 'mp4'},
    ],
  }
  monkeypatch.setattr(YtdlpGateway, 'extract_info_with_fallback', lambda **_kwargs: (info, {}))

  def cancelled(*_args, **_kwargs):
    raise OperationCancelled()

  monkeypatch.setattr(direct_media, 'read_media_streams', cancelled)
  with pytest.raises(OperationCancelled):
    DownloadService.probe('http://127.0.0.1/owned.mp4')


def test_media_container_is_checked_independently_of_its_extension(tmp_path, monkeypatch):
  from app.features.download import media_validation
  from app.features.download.domain import DownloadError

  path = tmp_path / 'renamed.mp4'
  path.write_bytes(b'not empty')
  monkeypatch.setattr(
    media_validation,
    'read_media_streams',
    lambda *_args, **_kwargs: {
      'format': {'format_name': 'mp3'},
      'streams': [{'codec_type': 'audio'}],
    },
  )
  with pytest.raises(DownloadError):
    validate_media(path, kind='video', extension='mp4')


def test_empty_recognition_is_skipped_without_translation_or_empty_exports(tmp_path, monkeypatch):
  from app.features.transcription import processing

  output_dir = tmp_path / 'outputs'
  output_dir.mkdir()
  statuses, done, errors = [], [], []
  monkeypatch.setattr(
    processing,
    'resolve_item_output_dir',
    lambda **_kwargs: SimpleNamespace(
      skipped=False,
      output_dir=output_dir,
      stem='silence',
      apply_all=None,
    ),
  )
  source = tmp_path / 'silence.wav'
  monkeypatch.setattr(processing, 'prepare_item_audio', lambda **_kwargs: (source, 1.0))
  monkeypatch.setattr(processing, 'transcribe_wav', lambda **_kwargs: ('', [], ''))

  def unexpected_translation(**_kwargs):
    raise AssertionError('Empty recognition must not be translated')

  monkeypatch.setattr(processing, 'translate_item_if_needed', unexpected_translation)
  options = SimpleNamespace(
    chunk_len_s=5,
    stride_len_s=1,
    want_timestamps=False,
    ignore_warning=True,
    want_translate=True,
    default_lang='en',
    runtime_profile={},
  )
  result = processing.process_materialized_work_item(
    transcription_engine=SimpleNamespace(),
    translation_service=SimpleNamespace(),
    work_item=SimpleNamespace(source_key='silence', source_path=source, forced_stem=None),
    apply_all=None,
    runtime=SimpleNamespace(session_id='skip-test', options=options, tracker=SimpleNamespace(mark_done=done.append)),
    callbacks=SimpleNamespace(
      cancel_check=lambda: False,
      item_status=lambda _key, status: statuses.append(status),
      item_progress=lambda *_args: None,
      item_error=lambda *args: errors.append(args),
    ),
  )
  assert not result.processed_any and not result.had_errors and not errors
  assert statuses[-1] == 'status.skipped' and done == ['silence']
  assert not output_dir.exists()


@pytest.mark.parametrize(
  'language,labels',
  [
    ('en-us', ['American English - original (original)', 'English (US) original']),
    ('zh-hans', ['简体中文 - dubbed', 'Chinese (Simplified)']),
    ('zh-hant', ['繁體中文 - dubbed', 'Chinese (Traditional)']),
  ],
)
def test_language_name_variants_do_not_create_extra_tracks(language, labels):
  assert all(not TrackLabelHeuristics.clean_audio_track_label_source(label, lang_code=language) for label in labels)


def test_distinct_voices_in_one_language_are_retained():
  info = {
    'extractor': 'youtube',
    'formats': [
      {
        'format_id': voice,
        'language': 'en',
        'acodec': 'aac',
        'vcodec': 'none',
        'ext': 'm4a',
        'audio_track': {'id': voice, 'language': 'en'},
      }
      for voice in ('voice-one', 'voice-two')
    ],
  }
  inventory = TrackInventory.build_audio_track_inventory(info)
  assert len(inventory['tracks']) == 2
  labels = build_audio_track_display_map(inventory['tracks'])
  assert len(set(labels.values())) == 2


def test_multiple_tracks_expand_across_formats_and_survive_source_refresh(qt_app):
  panel = DownloaderPanel()
  job = _Job(
    key='owned',
    url='http://localhost/owned.mp4',
    output_types=['audio'],
    output_exts=['mp3', 'wav'],
    quality='auto',
    status='queued',
    audio_track_id=None,
    audio_track_ids=['en', 'pl'],
  )
  panel._jobs = [job]
  items = panel._build_queue_items(['owned'])
  assert [(item.audio_track_id, item.ext) for item in items] == [
    ('en', 'mp3'),
    ('pl', 'mp3'),
    ('en', 'wav'),
    ('pl', 'wav'),
  ]
  table = SourceTable()
  table.setColumnCount(1)
  table.setRowCount(1)
  table.setCellWidget(0, 0, table.make_audio_track_combo(internal_key='owned', default_text='Audio', multiple=True))
  meta = {'audio_tracks': [{'track_id': code, 'lang_code': code} for code in ('en', 'pl')]}
  table.update_audio_tracks(row=0, col=0, meta=meta, default_text='Audio', preferred_audio_track_ids=['en', 'pl'])
  assert table.audio_track_ids_at(0, 0) == ['en', 'pl']
  field = table.multi_select_field_at(0, 0)
  assert field.currentText() == 'Wybrano: 2'
  assert 'angielski' in field.toolTip() and 'polski' in field.toolTip()
  table.update_audio_tracks(row=0, col=0, meta={'audio_tracks': meta['audio_tracks'][:1]}, default_text='Audio')
  assert table.audio_track_ids_at(0, 0) == ['en', 'pl']
  table.update_audio_tracks(row=0, col=0, meta=meta, default_text='Audio', preferred_audio_track_ids=[])
  assert table.audio_track_ids_at(0, 0) == []
  panel.deleteLater()
  table.deleteLater()
