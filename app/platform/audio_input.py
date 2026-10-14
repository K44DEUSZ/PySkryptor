# app/platform/audio_input.py
from __future__ import annotations

import logging
from typing import Any

from PyQt6 import QtMultimedia

_LOG = logging.getLogger(__name__)


def list_input_devices() -> list[QtMultimedia.QAudioDevice]:
  """Return the available Qt audio input devices."""
  return list(QtMultimedia.QMediaDevices.audioInputs())


def list_input_device_names() -> list[str]:
  """Return microphone descriptions without duplicate labels."""
  return list(dict.fromkeys(device.description() for device in list_input_devices()))


def resolve_input_device(device_name: str = "") -> tuple[Any, QtMultimedia.QAudioDevice | None]:
  """Resolve a saved description, preferring the default device among matches."""
  devices = list_input_devices()
  default = QtMultimedia.QMediaDevices.defaultAudioInput()
  matches = [device for device in devices if device.description() == device_name]
  if matches:
    return QtMultimedia, next((device for device in matches if device.id() == default.id()), matches[0])
  if device_name:
    _LOG.warning("Requested microphone unavailable. description=%s", device_name)
    return QtMultimedia, None
  return QtMultimedia, None if default.isNull() else default


def make_pcm16_mono_format(*, sample_rate: int = 16000) -> QtMultimedia.QAudioFormat:
  """Build the preferred mono PCM format for speech recognition."""
  fmt = QtMultimedia.QAudioFormat()
  fmt.setSampleRate(sample_rate)
  fmt.setChannelCount(1)
  fmt.setSampleFormat(QtMultimedia.QAudioFormat.SampleFormat.Int16)
  return fmt


def ensure_supported_format(dev: Any, desired_fmt: Any) -> tuple[bool, Any]:
  """Use the device's preferred format when the requested format is unavailable."""
  if dev.isFormatSupported(desired_fmt):
    return True, desired_fmt
  return False, dev.preferredFormat()
