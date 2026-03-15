# app/controller/platform/microphone.py
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def _qtmm():
    from PyQt5 import QtMultimedia
    return QtMultimedia


def list_input_devices() -> List[object]:
    """Returns list of QtMultimedia.QAudioDeviceInfo for audio inputs."""
    QtMultimedia = _qtmm()
    try:
        return list(QtMultimedia.QAudioDeviceInfo.availableDevices(QtMultimedia.QAudio.AudioInput))
    except Exception:
        return []


def _device_base_name(dev: object) -> str:
    try:
        return str(dev.deviceName() or "").strip()
    except Exception:
        return ""


def _device_realm(dev: object) -> str:
    try:
        fn = getattr(dev, "realm", None)
        if callable(fn):
            return str(fn() or "").strip().lower()
    except Exception:
        pass
    return ""


def _format_signature(fmt: object) -> Tuple[int, int, int, str, int, int]:
    try:
        sample_rate = int(fmt.sampleRate() or 0)
    except Exception:
        sample_rate = 0

    try:
        channel_count = int(fmt.channelCount() or 0)
    except Exception:
        channel_count = 0

    try:
        sample_size = int(fmt.sampleSize() or 0)
    except Exception:
        sample_size = 0

    try:
        codec = str(fmt.codec() or "").strip().lower()
    except Exception:
        codec = ""

    try:
        sample_type = int(fmt.sampleType())
    except Exception:
        sample_type = -1

    try:
        byte_order = int(fmt.byteOrder())
    except Exception:
        byte_order = -1

    return (sample_rate, channel_count, sample_size, codec, sample_type, byte_order)


def _device_signature(dev: object) -> Tuple[object, ...]:
    try:
        preferred_fmt = dev.preferredFormat()
    except Exception:
        preferred_fmt = None

    return (
        _device_base_name(dev),
        _device_realm(dev),
        _format_signature(preferred_fmt) if preferred_fmt is not None else (0, 0, 0, "", -1, -1),
    )


def _supported_value_count(dev: object, method_name: str) -> int:
    try:
        values = list(getattr(dev, method_name)())
    except Exception:
        return 0
    return len(values)


def _device_score(dev: object, default_dev: Optional[object]) -> Tuple[int, int, int, int, int]:
    desired_fmt = make_pcm16_mono_format(sample_rate=16000)

    try:
        exact_supported = 1 if dev.isFormatSupported(desired_fmt) else 0
    except Exception:
        exact_supported = 0

    try:
        preferred_fmt = dev.preferredFormat()
    except Exception:
        preferred_fmt = None

    preferred_exact = 1 if preferred_fmt is not None and format_is_pcm16_mono_16k(preferred_fmt) else 0

    is_default = 0
    if default_dev is not None:
        try:
            is_default = 1 if _device_signature(dev) == _device_signature(default_dev) else 0
        except Exception:
            is_default = 0

    sample_rates = _supported_value_count(dev, "supportedSampleRates")
    channels = _supported_value_count(dev, "supportedChannelCounts")

    return (is_default, exact_supported, preferred_exact, sample_rates, channels)


def _group_input_devices() -> Dict[str, List[object]]:
    groups: Dict[str, List[object]] = {}
    for dev in list_input_devices():
        base = _device_base_name(dev)
        if not base:
            continue
        groups.setdefault(base, []).append(dev)
    return groups


def _pick_input_device(devices: List[object]) -> Optional[object]:
    if not devices:
        return None

    QtMultimedia = _qtmm()
    try:
        default_dev = QtMultimedia.QAudioDeviceInfo.defaultInputDevice()
    except Exception:
        default_dev = None

    best_dev: Optional[object] = None
    best_score: Optional[Tuple[int, int, int, int, int]] = None

    for dev in devices:
        score = _device_score(dev, default_dev)
        if best_score is None or score > best_score:
            best_dev = dev
            best_score = score

    return best_dev or devices[0]


def list_input_device_names() -> List[str]:
    groups = _group_input_devices()
    return list(groups.keys())


def resolve_input_device(device_name: str = "") -> Tuple[object, Optional[object]]:
    """Resolve a QtMultimedia input device by name, or return default."""
    QtMultimedia = _qtmm()
    devices = list_input_devices()
    if not devices:
        return QtMultimedia, None

    wanted = str(device_name or "").strip()
    if wanted:
        groups = _group_input_devices()
        matches = groups.get(wanted, [])
        picked = _pick_input_device(matches)
        if picked is not None:
            return QtMultimedia, picked

    try:
        return QtMultimedia, QtMultimedia.QAudioDeviceInfo.defaultInputDevice()
    except Exception:
        return QtMultimedia, devices[0]


def make_pcm16_mono_format(*, sample_rate: int = 16000) -> object:
    """Build desired QAudioFormat: 16kHz, mono, signed 16-bit PCM little endian."""
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
    """Ensure device supports desired format; if not, try nearestFormat."""
    try:
        if dev.isFormatSupported(desired_fmt):
            return True, desired_fmt
        fmt2 = dev.nearestFormat(desired_fmt)
        return False, fmt2
    except Exception:
        return False, desired_fmt


def format_is_pcm16_mono_16k(fmt: object, *, sample_rate: int = 16000) -> bool:
    """Strict check: must be exactly 16kHz, mono, signed 16-bit PCM."""
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
