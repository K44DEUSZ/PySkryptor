"""Exercise real inference hosts and file/live workflows in an isolated profile."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
import wave
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6 import QtWidgets

from app.application.workers.transcription_worker import TranscriptionWorker
from app.application.workers.worker_runner import WorkerRunner
from app.core.config.config import AppConfig
from app.core.config.profiles import RuntimeProfiles
from app.core.domain.entities import TranscriptionSessionRequest
from app.core.domain.errors import OperationCancelled
from app.core.runtime.ffmpeg import setup_ffmpeg_runtime
from app.features.settings.service import SettingsService
from app.features.transcription.live import LiveTranscriptionService
from app.features.translation.runtime_request import build_translation_request
from app.runtime.client import TranscriptionEngineClient, TranslationEngineClient
from app.runtime.types import TranscribeWavRequest


def background_call(app, function):
  result = {}
  metrics = {}
  try:
    import psutil
    parent = psutil.Process()
  except ImportError:
    parent = None

  def run():
    try:
      result['value'] = function()
    except BaseException as error:
      result['error'] = error
      result['traceback'] = traceback.format_exc()

  thread = threading.Thread(target=run, daemon=True)
  thread.start()
  gaps = []
  previous = time.perf_counter()
  sampled_at = 0.0
  while thread.is_alive():
    app.processEvents()
    now = time.perf_counter()
    gaps.append(now - previous)
    previous = now
    if parent is not None and now - sampled_at >= 0.2:
      sampled_at = now
      for process in parent.children(recursive=True):
        try:
          cpu = process.cpu_times()
          total_cpu = cpu.user + cpu.system
          sample = metrics.setdefault(process.pid, {
            'cpu_start_s': total_cpu, 'cpu_end_s': total_cpu, 'peak_rss_mib': 0, 'peak_threads': 0,
          })
          sample['cpu_end_s'] = total_cpu
          sample['peak_rss_mib'] = max(sample['peak_rss_mib'], process.memory_info().rss / 1048576)
          sample['peak_threads'] = max(sample['peak_threads'], process.num_threads())
        except psutil.Error:
          pass
    thread.join(0.01)
  if 'error' in result:
    raise result['error']
  return result.get('value'), max(gaps, default=0), metrics


def run(args):
  app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
  app.setQuitOnLastWindowClosed(False)
  output = args.report_dir.resolve()
  output.mkdir(parents=True, exist_ok=True)
  profile = output / 'profile'
  AppConfig.set_root_dir(ROOT, data_root=profile)
  AppConfig.ensure_dirs()
  settings = SettingsService()
  settings.restore_defaults()
  snapshot = settings.save({'engine': {
    'preferred_device': args.device, 'precision': args.precision, 'fp32_math_mode': args.math_mode,
  }})
  AppConfig.initialize_from_snapshot(snapshot)
  setup_ffmpeg_runtime(AppConfig)
  os.environ['PYSKRYPTOR_CPU_PYTHON'] = str(args.cpu_python.resolve())
  os.environ['PYSKRYPTOR_CUDA_PYTHON'] = str(args.cuda_python.resolve())
  if args.threads:
    os.environ['OMP_NUM_THREADS'] = str(args.threads)
    os.environ['MKL_NUM_THREADS'] = str(args.threads)
  asr = TranscriptionEngineClient()
  translation = TranslationEngineClient()
  report = {'requested': vars(args) | {'speech': str(args.speech)}, 'checks': [], 'failures': []}
  pids = []

  def check(name, function):
    start = time.perf_counter()
    print(f'START {name}', flush=True)
    try:
      value, gap, metrics = background_call(app, function)
      entry = {
        'name': name, 'seconds': time.perf_counter() - start,
        'max_event_loop_gap_s': gap, 'process_metrics': metrics, 'result': value,
      }
      report['checks'].append(entry)
      print(json.dumps(entry, ensure_ascii=True, default=str), flush=True)
      return value
    except BaseException as error:
      report['failures'].append({'name': name, 'error': str(error), 'traceback': traceback.format_exc()})
      print(f'FAIL {name}: {error}', flush=True)
      return None
    finally:
      (output / 'results.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding='utf-8',
      )

  try:
    for client in (asr, translation):
      def warmup(client=client):
        client.warmup()
        health = client.health()
        assert health.ready
        pids.append(health.pid)
        return asdict(health)
      check(client.role + '_warmup', warmup)

    runtime = RuntimeProfiles.resolve_transcription_runtime(profile='balanced')

    def file_inference():
      result = asr.transcribe_wav(TranscribeWavRequest(
        str(args.speech.resolve()), 'functional-speech', 45, 5, True, False, True, '', runtime,
      ))
      assert result.merged_text and result.detected_language == args.language and result.segments
      for segment in result.segments:
        assert 0 <= segment['start'] <= segment['end']
      return asdict(result)

    file_result = check('file_auto_language_timestamps', file_inference)
    for translation_profile in ('fast', 'balanced', 'accurate'):
      def translate(translation_profile=translation_profile):
        snapshot = settings.save({'model': {'translation_model': {'profile': translation_profile}}})
        AppConfig.initialize_from_snapshot(snapshot)
        request = build_translation_request(
          text=file_result['merged_text'], src_lang=args.language, tgt_lang=args.target_language,
        )
        result = translation.translate_text(request)
        assert result and result != file_result['merged_text']
        return {'text': result, 'request': asdict(request)}
      check('translation_' + translation_profile, translate)

    # Use the application's real Qt worker for the complete export workflow.
    runner = WorkerRunner()
    worker = TranscriptionWorker(
      transcription_engine=asr, translation_engine=translation, entries=[str(args.speech.resolve())],
      session_request=TranscriptionSessionRequest(
        args.language, args.target_language, True, ('txt', 'txt_ts', 'srt', 'vtt', 'json', 'csv', 'md'),
        True, False, 'm4a', False, 'mp4',
      ),
    )
    batch = {'errors': [], 'progress': [], 'session': None}
    worker.item_error.connect(lambda key, error, params: batch['errors'].append([key, error, params]))
    worker.progress.connect(batch['progress'].append)
    worker.session_done.connect(lambda *values: batch.update(session=values))
    worker.conflict_check.connect(lambda *_args: worker.on_conflict_decided('rename', 'functional-copy'))
    start = time.perf_counter()
    runner.start(worker)
    gaps = []
    previous = time.perf_counter()
    while runner.is_running() and time.perf_counter() - start < 300:
      app.processEvents()
      now = time.perf_counter()
      gaps.append(now - previous)
      previous = now
      time.sleep(0.01)
    batch['seconds'] = time.perf_counter() - start
    batch['max_event_loop_gap_s'] = max(gaps, default=0)
    batch['outputs'] = [str(path.relative_to(profile)) for path in profile.rglob('transcript*') if path.is_file()]
    report['checks'].append({'name': 'qt_file_worker_all_exports', 'result': batch})
    if batch['errors'] or not batch['session'] or not batch['session'][1] or batch['session'][2]:
      report['failures'].append({'name': 'qt_file_worker_all_exports', 'error': str(batch)})

    with wave.open(str(args.speech), 'rb') as source:
      assert source.getframerate() == 16000 and source.getsampwidth() == 2 and source.getnchannels() == 1
      pcm = source.readframes(source.getnframes())
    for mode in ('cumulative', 'stream'):
      def live(mode=mode):
        calls = []

        def recognize(request, **kwargs):
          result = asr.recognize_audio(request, **kwargs)
          calls.append({'text': result.text, 'context': request.previous_text})
          return result

        session = LiveTranscriptionService(
          transcription_engine=SimpleNamespace(recognize_audio=recognize),
          translation_engine=translation, source_language=args.language,
          target_language=args.target_language, translate_enabled=True, cancel_check=lambda: False,
          profile='balanced', output_mode=mode,
        )
        updates = []
        for offset in range(0, len(pcm), 32000):
          updates.extend(session.push_pcm16(pcm[offset:offset + 32000]))
        updates.extend(session.push_pcm16(bytes(32000)))
        updates.extend(session.finalize())
        assert updates
        if mode == 'cumulative':
          assert updates[-1].archive_source_text and updates[-1].archive_target_text
          sample = updates[-1]
        else:
          captions = [update for update in updates if update.display_source_text and update.display_target_text]
          assert captions
          sample = captions[-1]
        return {'sample': asdict(sample), 'final': asdict(updates[-1]), 'update_count': len(updates), 'calls': calls}
      check('live_translation_' + mode, live)

    def cancel_and_restart():
      old_pid = asr.health().pid
      cancelled_at = time.monotonic()
      try:
        asr.transcribe_wav(TranscribeWavRequest(
          str(args.speech.resolve()), 'cancelled-speech', 45, 5, True, False, True, args.language, runtime,
        ), cancel_check=lambda: time.monotonic() - cancelled_at >= 0.2)
        raise AssertionError('Inference completed before cancellation could be tested')
      except OperationCancelled:
        elapsed = time.monotonic() - cancelled_at
      asr.warmup()
      health = asr.health()
      pids.append(health.pid)
      assert health.ready and health.pid != old_pid
      return {'cancel_seconds': elapsed, 'old_pid': old_pid, 'new_pid': health.pid}
    check('cancel_inference_and_restart', cancel_and_restart)
    report['gui_imported_torch'] = 'torch' in sys.modules
    assert not report['gui_imported_torch']
  finally:
    asr.shutdown()
    translation.shutdown()
    try:
      import psutil
      processes = []
      for pid in pids:
        try:
          processes.append(psutil.Process(pid))
        except psutil.Error:
          pass
      _gone, alive = psutil.wait_procs(processes, timeout=5)
      report['remaining_host_pids'] = [process.pid for process in alive]
    except ImportError:
      report['remaining_host_pids'] = 'psutil unavailable'
    (output / 'results.json').write_text(
      json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding='utf-8',
    )
  return 1 if report['failures'] else 0


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--report-dir', required=True, type=Path)
  parser.add_argument('--speech', required=True, type=Path)
  parser.add_argument('--cpu-python', required=True, type=Path)
  parser.add_argument('--cuda-python', required=True, type=Path)
  parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
  parser.add_argument('--precision', choices=['auto', 'float32', 'float16', 'bfloat16'], default='auto')
  parser.add_argument('--math-mode', choices=['ieee', 'tf32'], default='ieee')
  parser.add_argument('--language', default='en')
  parser.add_argument('--target-language', default='pl')
  parser.add_argument('--threads', type=int)
  raise SystemExit(run(parser.parse_args()))
