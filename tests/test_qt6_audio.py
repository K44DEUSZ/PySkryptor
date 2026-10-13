import numpy as np
import pytest
from PyQt6 import QtMultimedia

from app.application.workers.live_worker import LiveWorker
from app.platform.audio_input import ensure_supported_format, make_pcm16_mono_format


@pytest.mark.parametrize(
  'sample_format, dtype, values',
  [
    (QtMultimedia.QAudioFormat.SampleFormat.Int16, '<i2', [-32768, 0, 16384]),
    (QtMultimedia.QAudioFormat.SampleFormat.Int32, '<i4', [-2147483648, 0, 1073741824]),
    (QtMultimedia.QAudioFormat.SampleFormat.UInt8, 'u1', [0, 128, 192]),
    (QtMultimedia.QAudioFormat.SampleFormat.Float, '<f4', [-1.0, 0.0, 0.5]),
  ],
)
def test_supported_sample_formats_normalize_to_asr_pcm(sample_format, dtype, values):
  fmt = make_pcm16_mono_format()
  fmt.setSampleFormat(sample_format)
  output = LiveWorker._normalize_pcm16(np.array(values, dtype=dtype).tobytes(), fmt, QtMultimedia)
  np.testing.assert_array_equal(np.frombuffer(output, dtype='<i2'), [-32768, 0, 16384])


def test_stereo_input_is_mixed_to_mono():
  fmt = make_pcm16_mono_format()
  fmt.setChannelCount(2)
  output = LiveWorker._normalize_pcm16(np.array([1000, 3000, -1000, 1000], dtype='<i2').tobytes(), fmt, QtMultimedia)
  np.testing.assert_array_equal(np.frombuffer(output, dtype='<i2'), [2000, 0])


def test_preferred_microphone_format_is_used_when_pcm16_is_unavailable():
  preferred = make_pcm16_mono_format(sample_rate=48000)
  preferred.setSampleFormat(QtMultimedia.QAudioFormat.SampleFormat.Float)

  class Device:
    def isFormatSupported(self, _format):
      return False

    def preferredFormat(self):
      return preferred

  exact, fmt = ensure_supported_format(Device(), make_pcm16_mono_format())
  assert not exact and fmt.sampleRate() == 48000
  LiveWorker._validate_audio_format(fmt=fmt, qt_multimedia=QtMultimedia)


def test_resampling_preserves_duration():
  fmt = make_pcm16_mono_format(sample_rate=48000)
  chunk = np.zeros(48000, dtype='<i2').tobytes()
  output = LiveWorker._normalize_pcm16(chunk, fmt, QtMultimedia)
  assert len(output) == 16000 * 2
