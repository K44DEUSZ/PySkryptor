# model/io/microphone.py
from __future__ import annotations

from typing import List, Optional, Tuple


def _qtmm():
    from PyQt5 import QtMultimedia  # lazy import
    return QtMultimedia


def list_input_devices() -> List[object]:
    """
    Returns list of QtMultimedia.QAudioDeviceInfo for audio inputs.
    """
    QtMultimedia = _qtmm()
    try:
        return list(QtMultimedia.QAudioDeviceInfo.availableDevices(QtMultimedia.QAudio.AudioInput))
    except Exception:
        return []


def list_input_device_names() -> List[str]:
    out: List[str] = []
    for d in list_input_devices():
        try:
            name = str(d.deviceName() or "").strip()
        except Exception:
            name = ""
        if name:
            out.append(name)
    return out


def resolve_input_device(device_name: str = "") -> Tuple[object, Optional[object]]:
    """
    Resolve a QtMultimedia input device by name, or return default.
    Returned values are (QtMultimedia, device_info).
    """
    QtMultimedia = _qtmm()
    devices = list_input_devices()
    if not devices:
        return QtMultimedia, None

    wanted = (device_name or "").strip()
    if wanted:
        for d in devices:
            try:
                if str(d.deviceName() or "").strip() == wanted:
                    return QtMultimedia, d
            except Exception:
                continue

    try:
        return QtMultimedia, QtMultimedia.QAudioDeviceInfo.defaultInputDevice()
    except Exception:
        return QtMultimedia, devices[0]


def make_pcm16_mono_format(*, sample_rate: int = 16000) -> object:
    """
    Build desired QAudioFormat: 16kHz, mono, signed 16-bit PCM little endian.
    """
    QtMultimedia = _qtmm()
    fmt = QtMultimedia.QAudioFormat()
    fmt.setSampleRate(int(sample_rate))
    fmt.setChannelCount(1)
    fmt.setSampleSize(16)
    fmt.setCodec("audio/pcm")
    fmt.setByteOrder(QtMultimedia.QAudioFormat.LittleEndian)
    fmt.setSampleType(QtMultimedia.QAudioFormat.SignedInt)
    return fmt


def ensure_supported_format(dev: object, desired_fmt: object) -> Tuple[bool, object]:
    """
    Ensure device supports desired format; if not, try nearestFormat.
    Returns (is_supported_exactly, fmt_to_use).
    """
    try:
        if dev.isFormatSupported(desired_fmt):
            return True, desired_fmt
        fmt2 = dev.nearestFormat(desired_fmt)
        return False, fmt2
    except Exception:
        return False, desired_fmt


def format_is_pcm16_mono_16k(fmt: object, *, sample_rate: int = 16000) -> bool:
    """
    Strict check: must be exactly 16kHz, mono, signed 16-bit PCM.
    """
    QtMultimedia = _qtmm()
    try:
        return (
            int(fmt.sampleRate()) == int(sample_rate)
            and int(fmt.channelCount()) == 1
            and int(fmt.sampleSize()) == 16
            and fmt.sampleType() == QtMultimedia.QAudioFormat.SignedInt
        )
    except Exception:
        return False