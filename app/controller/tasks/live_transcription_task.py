# app/controller/tasks/live_transcription_task.py
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, List, Optional

import numpy as np
from PyQt5 import QtCore

from app.controller.platform.microphone import (
    ensure_supported_format,
    make_pcm16_mono_format,
    resolve_input_device,
)
from app.controller.support.cancellation import CancellationToken
from app.model.helpers.errors import AppError
from app.model.helpers.chunking import pcm16le_bytes_to_float32
from app.model.services.transcription_service import LiveSession, LiveUpdate

_LOG = logging.getLogger(__name__)


# ----- Errors -----
class LiveError(AppError):
    """Key-based error used for i18n-friendly live task failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key=str(key), params=dict(params or {}))


class LiveTranscriptionWorker(QtCore.QObject):
    """Captures audio from an input device and performs live transcription."""

    status = QtCore.pyqtSignal(str)
    detected_language = QtCore.pyqtSignal(str)
    source_text = QtCore.pyqtSignal(str)
    target_text = QtCore.pyqtSignal(str)
    spectrum = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str, dict)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        pipe: Any,
        device_name: str = "",
        source_language: str = "",
        target_language: str = "",
        translate_enabled: bool = False,
        include_source_in_translate: bool = True,
        chunk_length_s: Optional[int] = None,
        stride_length_s: Optional[int] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> None:
        super().__init__()
        self._pipe = pipe
        self._device_name = str(device_name or "").strip()

        self._src_lang = str(source_language or "").strip()
        self._tgt_lang = str(target_language or "").strip()
        self._translate_enabled = bool(translate_enabled)
        self._include_source = bool(include_source_in_translate)
        self._chunk_length_s = int(chunk_length_s) if chunk_length_s is not None else None
        self._stride_length_s = int(stride_length_s) if stride_length_s is not None else None

        self._cancel = cancel_token or CancellationToken()
        self._pause = threading.Event()

        self._session: Optional[LiveSession] = None
        self._qtmm: Any = None
        self._fmt: Any = None
        self._audio_in: Any = None
        self._io: Any = None
        self._timer: Optional[QtCore.QTimer] = None
        self._status_key: str = ""
        self._meter_level: float = 0.0
        self._last_emitted_language: str = ""
        self._last_emitted_source: str = ""
        self._last_emitted_target: str = ""
        self._last_spectrum_emit_s: float = 0.0
        self._spectrum_emit_interval_s: float = 0.08
        self._last_backlog_debug_s: float = 0.0
        self._backlog_debug_interval_s: float = 0.6
        self._pending_chunks = deque()
        self._processing_audio: bool = False
        self._process_audio_scheduled: bool = False

    # ----- External controls -----

    def cancel(self) -> None:
        _LOG.debug("Live worker cancel requested. worker=live_transcription")
        self._cancel.cancel()
        self._pause.set()

    def pause(self) -> None:
        _LOG.debug("Live worker pause requested. worker=live_transcription")
        self._pause.set()

    def resume(self) -> None:
        _LOG.debug("Live worker resume requested. worker=live_transcription")
        self._pause.clear()

    # ----- Internals -----

    def _is_cancelled(self) -> bool:
        if self._cancel.is_cancelled:
            return True
        try:
            th = QtCore.QThread.currentThread()
            if th is not None and th.isInterruptionRequested():
                return True
        except Exception:
            pass
        return False

    def _set_status(self, key: str) -> None:
        key = str(key or "").strip()
        if not key or key == self._status_key:
            return
        self._status_key = key
        self.status.emit(key)

    def _emit_updates(self, updates: List[LiveUpdate]) -> None:
        if not updates:
            return

        u = updates[-1]

        detected_language = str(u.detected_language or "")
        if detected_language and detected_language != self._last_emitted_language:
            self._last_emitted_language = detected_language
            _LOG.debug("Live worker detected language updated. lang=%s", detected_language)
            self.detected_language.emit(detected_language)

        source_text = str(u.source_text or "")
        if source_text != self._last_emitted_source:
            self._last_emitted_source = source_text
            self.source_text.emit(source_text)

        target_text = str(u.target_text or "")
        if self._translate_enabled:
            if target_text != self._last_emitted_target:
                self._last_emitted_target = target_text
                self.target_text.emit(target_text)
        elif target_text and target_text != self._last_emitted_target:
            self._last_emitted_target = target_text
            self.target_text.emit(target_text)

    @staticmethod
    def _level_from_audio(audio: np.ndarray) -> float:
        if audio.size == 0:
            return 0.0

        try:
            audio = audio - float(audio.mean())
        except Exception:
            pass

        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        if rms <= 0.0:
            return 0.0

        db = 20.0 * float(np.log10(rms))
        db_min = -60.0
        if db <= db_min:
            return 0.0
        if db >= 0.0:
            return 1.0
        return (db - db_min) / (0.0 - db_min)

    def _chunk_level(self, chunk: bytes) -> float:
        if not chunk:
            return 0.0
        audio = pcm16le_bytes_to_float32(chunk)
        if audio.size == 0:
            return 0.0
        return self._level_from_audio(audio)

    def _meter_from_level(self, level: float) -> List[float]:
        lvl = max(0.0, min(1.0, float(level or 0.0)))

        prev = float(self._meter_level)
        alpha = 0.55 if lvl > prev else 0.08
        new = prev + alpha * (lvl - prev)
        self._meter_level = new

        bars = 24
        filled = new * bars
        full = int(filled)
        frac = float(filled - full)

        out = [0.0] * bars
        for i in range(bars):
            if i < full:
                out[i] = 1.0
            elif i == full and frac > 0.0:
                out[i] = min(1.0, frac)

        return out

    def _meter_from_pcm16(self, chunk: bytes) -> List[float]:
        return self._meter_from_level(self._chunk_level(chunk))

    @staticmethod
    def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
        if audio.size == 0:
            return np.zeros((0,), dtype=np.float32)
        src = int(src_sr)
        dst = int(dst_sr)
        if src <= 0 or dst <= 0 or src == dst:
            return audio.astype(np.float32, copy=False)
        n = int(round(float(audio.size) * float(dst) / float(src)))
        if n <= 0:
            return np.zeros((0,), dtype=np.float32)
        x_old = np.arange(int(audio.size), dtype=np.float32)
        x_new = np.linspace(0.0, float(audio.size - 1), n, dtype=np.float32)
        return np.interp(x_new, x_old, audio.astype(np.float32, copy=False)).astype(np.float32)

    @staticmethod
    def _normalize_pcm16(chunk: bytes, fmt: object, QtMultimedia: object) -> bytes:
        if not chunk:
            return b""

        try:
            ch = int(fmt.channelCount() or 1)
        except Exception:
            ch = 1
        if ch <= 0:
            ch = 1

        try:
            sr = int(fmt.sampleRate() or 16000)
        except Exception:
            sr = 16000
        if sr <= 0:
            sr = 16000

        try:
            sample_type = fmt.sampleType()
        except Exception:
            sample_type = QtMultimedia.QAudioFormat.SignedInt

        try:
            byte_order = fmt.byteOrder()
        except Exception:
            byte_order = QtMultimedia.QAudioFormat.LittleEndian

        frame_bytes = int(ch) * 2
        if frame_bytes <= 0:
            frame_bytes = 2
        if len(chunk) % frame_bytes != 0:
            chunk = chunk[: len(chunk) - (len(chunk) % frame_bytes)]
        if not chunk:
            return b""

        if (
            ch == 1
            and sr == 16000
            and sample_type == QtMultimedia.QAudioFormat.SignedInt
            and byte_order == QtMultimedia.QAudioFormat.LittleEndian
        ):
            return chunk

        endian = "<" if byte_order == QtMultimedia.QAudioFormat.LittleEndian else ">"

        if sample_type == QtMultimedia.QAudioFormat.UnSignedInt:
            a = np.frombuffer(chunk, dtype=np.dtype(endian + "u2")).astype(np.int32)
            a = (a - 32768).astype(np.int16)
        else:
            a = np.frombuffer(chunk, dtype=np.dtype(endian + "i2")).astype(np.int16)

        audio = a.astype(np.float32) / 32768.0
        if ch > 1:
            audio = audio.reshape(-1, ch).mean(axis=1)

        if sr != 16000:
            audio = LiveTranscriptionWorker._resample(audio, sr, 16000)

        if audio.size == 0:
            return b""

        audio = np.clip(audio, -1.0, 1.0)
        out = (audio * 32767.0).astype(np.int16)
        return out.astype("<i2", copy=False).tobytes()

    def _audio_error_detail(self, err: object) -> str:
        QtMultimedia = self._qtmm
        if QtMultimedia is None:
            return str(err)
        try:
            m = {
                QtMultimedia.QAudio.NoError: "no_error",
                QtMultimedia.QAudio.OpenError: "open_error",
                QtMultimedia.QAudio.IOError: "io_error",
                QtMultimedia.QAudio.UnderrunError: "underrun_error",
                QtMultimedia.QAudio.FatalError: "fatal_error",
            }
            name = m.get(err, "unknown")
            return f"audio_error:{name}"
        except Exception:
            return str(err)

    def _cleanup(self) -> None:
        try:
            if self._timer is not None:
                self._timer.stop()
                self._timer.deleteLater()
        except Exception:
            pass
        self._timer = None
        self._pending_chunks.clear()
        self._processing_audio = False
        self._process_audio_scheduled = False

        try:
            if self._io is not None:
                try:
                    self._io.readyRead.disconnect(self._on_ready_read)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if self._audio_in is not None:
                try:
                    self._audio_in.stateChanged.disconnect(self._on_audio_state_changed)
                except Exception:
                    pass
                self._audio_in.stop()
        except Exception:
            pass

        self._io = None
        self._audio_in = None

    def _fail(self, err: Any) -> None:
        err_key = getattr(err, "key", None)
        err_params = getattr(err, "params", None)

        if err_key:
            _LOG.error("Live transcription failed. key=%s", err_key)
            self.error.emit(str(err_key), dict(err_params or {}))
        else:
            detail = str(err)
            _LOG.error("Live transcription failed. detail=%s", detail)
            self.error.emit("error.live.failed", {"detail": detail})

        self._set_status("status.error")
        self._cleanup()
        self.finished.emit()

    # ----- Qt slots -----

    def _on_audio_state_changed(self, state: int) -> None:
        if self._audio_in is None or self._qtmm is None:
            return
        if self._is_cancelled():
            return
        try:
            if state == self._qtmm.QAudio.StoppedState:
                err = self._audio_in.error()
                if err != self._qtmm.QAudio.NoError:
                    self._fail(self._audio_error_detail(err))
        except Exception as ex:
            self._fail(ex)

    def _schedule_audio_processing(self) -> None:
        if self._processing_audio or self._process_audio_scheduled:
            return
        self._process_audio_scheduled = True
        QtCore.QTimer.singleShot(0, self._process_next_chunk)

    @QtCore.pyqtSlot()
    def _process_next_chunk(self) -> None:
        self._process_audio_scheduled = False

        if self._pause.is_set() or self._is_cancelled() or self._session is None:
            if self._pause.is_set():
                self._pending_chunks.clear()
            return
        if self._processing_audio or not self._pending_chunks:
            return

        self._processing_audio = True
        try:
            chunk, level = self._pending_chunks.popleft()
            updates = self._session.push_pcm16(chunk, level=level)
        except Exception as ex:
            self._pending_chunks.clear()
            self._processing_audio = False
            self._fail(ex)
            return

        self._processing_audio = False
        self._emit_updates(updates)

        if self._pending_chunks and not self._pause.is_set() and not self._is_cancelled():
            self._schedule_audio_processing()

    @QtCore.pyqtSlot()
    def _on_ready_read(self) -> None:
        if self._io is None or self._audio_in is None or self._qtmm is None or self._fmt is None:
            return
        try:
            chunk = bytes(self._io.readAll())
        except Exception:
            return
        if not chunk:
            return

        chunk = self._normalize_pcm16(chunk, self._fmt, self._qtmm)
        if not chunk:
            return

        level = self._chunk_level(chunk)

        meter = self._meter_from_level(level)
        now_s = time.monotonic()
        if (now_s - self._last_spectrum_emit_s) >= self._spectrum_emit_interval_s:
            self._last_spectrum_emit_s = now_s
            try:
                self.spectrum.emit(meter)
            except Exception:
                pass

        if self._pause.is_set() or self._is_cancelled() or self._session is None:
            return

        self._pending_chunks.append((chunk, level))
        if _LOG.isEnabledFor(logging.DEBUG):
            backlog = len(self._pending_chunks)
            if backlog >= 3 and (now_s - self._last_backlog_debug_s) >= self._backlog_debug_interval_s:
                self._last_backlog_debug_s = now_s
                _LOG.debug("Live audio backlog updated. worker=live_transcription backlog=%s", backlog)
        self._schedule_audio_processing()

    @QtCore.pyqtSlot()
    def _tick(self) -> None:
        if self._audio_in is None or self._qtmm is None:
            return

        if self._is_cancelled():
            self._cleanup()
            self.finished.emit()
            return

        try:
            if self._pause.is_set():
                if self._audio_in.state() == self._qtmm.QAudio.ActiveState:
                    try:
                        self._audio_in.suspend()
                    except Exception:
                        pass
                self._set_status("status.paused")
            else:
                if self._audio_in.state() == self._qtmm.QAudio.SuspendedState:
                    try:
                        self._audio_in.resume()
                    except Exception:
                        pass
                self._set_status("status.listening")
        except Exception:
            pass

    # ----- Run -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        self._set_status("status.initializing")

        try:
            _LOG.debug(
                "Live worker starting. worker=live_transcription device=%s source_language=%s target_language=%s translate_enabled=%s chunk_length_s=%s stride_length_s=%s",
                self._device_name,
                self._src_lang,
                self._tgt_lang,
                bool(self._translate_enabled),
                self._chunk_length_s,
                self._stride_length_s,
            )
            QtMultimedia, dev = resolve_input_device(self._device_name)
            fmt = make_pcm16_mono_format()

            if dev is None:
                try:
                    dev = QtMultimedia.QAudioDeviceInfo.defaultInputDevice()
                except Exception:
                    dev = None
                _LOG.debug("Live worker using default input device. requested_device=%s", self._device_name)

            if dev is not None:
                _, fmt = ensure_supported_format(dev, fmt)

            try:
                if int(fmt.sampleSize() or 0) != 16:
                    raise LiveError("error.live.microphone_format_unsupported")
                st = fmt.sampleType()
                if st not in (QtMultimedia.QAudioFormat.SignedInt, QtMultimedia.QAudioFormat.UnSignedInt):
                    raise LiveError("error.live.microphone_format_unsupported")
                c = str(fmt.codec() or "").strip().lower()
                if c and c != "audio/pcm":
                    raise LiveError("error.live.microphone_format_unsupported")
            except Exception:
                raise LiveError("error.live.microphone_format_unsupported")

            self._qtmm = QtMultimedia
            self._fmt = fmt
            _LOG.debug(
                "Live worker audio format resolved. sample_rate=%s channels=%s sample_size=%s codec=%s",
                int(fmt.sampleRate() or 0),
                int(fmt.channelCount() or 0),
                int(fmt.sampleSize() or 0),
                str(fmt.codec() or ""),
            )

            if dev is None:
                self._audio_in = QtMultimedia.QAudioInput(fmt)
            else:
                self._audio_in = QtMultimedia.QAudioInput(dev, fmt)

            try:
                self._audio_in.stateChanged.connect(self._on_audio_state_changed)
            except Exception:
                pass

            self._io = self._audio_in.start()
            if self._io is None:
                raise LiveError("error.live.audio_input_start_failed")

            try:
                self._io.readyRead.connect(self._on_ready_read)
            except Exception:
                pass

            self._session = LiveSession(
                pipe=self._pipe,
                source_language=self._src_lang,
                target_language=self._tgt_lang,
                translate_enabled=self._translate_enabled,
                include_source_in_translate=self._include_source,
                cancel_check=self._is_cancelled,
                chunk_length_s=self._chunk_length_s,
                stride_length_s=self._stride_length_s,
            )

            self._set_status("status.listening")
            _LOG.debug("Live worker session initialized. worker=live_transcription")

            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(50)
            self._timer.timeout.connect(self._tick)
            self._timer.start()
            _LOG.debug("Live worker timer started. interval_ms=%s", self._timer.interval())
        except Exception as ex:
            _LOG.exception("Live transcription failed.")
            self._fail(ex)
