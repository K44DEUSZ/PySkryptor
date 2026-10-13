import wave

import numpy as np

from app.features.transcription.errors import TranscriptionError
from app.features.transcription.runtime import transcribe_wav
from app.features.transcripts.writer import TextPostprocessor


def test_one_accepted_file_language_can_be_used_for_translation(tmp_path):
  path = tmp_path / 'speech.wav'
  audio = (np.sin(np.arange(32000) * 0.1) * 12000).astype('<i2').tobytes()
  with wave.open(str(path), 'wb') as stream:
    stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    stream.writeframes(audio)

  def backend(_audio, **_kwargs):
    return {'text': 'Dzień dobry.', 'language': 'pl'}

  text, _segments, language = transcribe_wav(
    backend=backend,
    wav_path=path,
    key='speech',
    chunk_len_s=45,
    stride_len_s=5,
    want_timestamps=False,
    ignore_warning=False,
    require_language=True,
    progress_cb=None,
    cancel_check=lambda: False,
    runtime_profile={'stable_language_min_hits': 2},
    postprocessor=TextPostprocessor(),
    error_factory=TranscriptionError,
  )
  assert text == 'Dzień dobry.' and language == 'pl'


def test_conflicting_unconfirmed_file_languages_remain_unknown(tmp_path):
  path = tmp_path / 'mixed.wav'
  audio = (np.sin(np.arange(64000) * 0.1) * 12000).astype('<i2').tobytes()
  with wave.open(str(path), 'wb') as stream:
    stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    stream.writeframes(audio)
  results = iter(
    [
      {'text': 'Dzień dobry.', 'language': 'pl'},
      {'text': 'Good morning.', 'language': 'en'},
    ]
  )
  _text, _segments, language = transcribe_wav(
    backend=lambda _audio, **_kwargs: next(results),
    wav_path=path,
    key='mixed',
    chunk_len_s=2,
    stride_len_s=0,
    want_timestamps=False,
    ignore_warning=False,
    require_language=True,
    progress_cb=None,
    cancel_check=lambda: False,
    runtime_profile={'stable_language_min_hits': 2},
    postprocessor=TextPostprocessor(),
    error_factory=TranscriptionError,
  )
  assert language == ''
