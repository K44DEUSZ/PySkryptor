# app/controller/tasks/live_transcription_task.py
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

import numpy as np
from PyQt5 import QtCore

from app.controller.platform.microphone import (
    ensure_supported_format,
    make_pcm16_mono_format,
    resolve_input_device,
)
from app.controller.tasks.live_session import LiveSession, LiveUpdate
from app.controller.support.cancellation import CancellationToken
from app.model.config.app_config import AppConfig as Config
from app.model.helpers.chunking import pcm16le_bytes_to_float32
from app.model.helpers.errors import AppError

_LOG = logging.getLogger(__name__)


# ----- Errors -----
class LiveError(AppError):
    """Key-based error used for i18n-friendly live task failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


class LiveTranscriptionWorker(QtCore.QObject):
    """Captures audio from an input device and performs live transcription."""

    status = QtCore.pyqtSignal(str)
    detected_language = QtCore.pyqtSignal(str)
    source_text = QtCore.pyqtSignal(str)
    target_text = QtCore.pyqtSignal(str)
    archive_source_text = QtCore.pyqtSignal(str)
    archive_target_text = QtCore.pyqtSignal(str)
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
        preset_id: str = Config.LIVE_DEFAULT_PRESET,
        output_mode: str = LiveSession.OUTPUT_MODE_CUMULATIVE,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        super().__init__()
        self._pipe = pipe
        self._device_name = str(device_name or "").strip()

        self._src_lang = str(source_language or "").strip()
        self._tgt_lang = str(target_language or "").strip()
        self._translate_enabled = bool(translate_enabled)
        self._preset_id = Config.normalize_live_preset(preset_id)
        self._output_mode = Config.normalize_live_output_mode(output_mode)

        self._cancel = cancel_token or CancellationToken()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._run_finished = False

        self._session: LiveSession | None = None
        self._qtmm: Any = None
        self._fmt: Any = None
        self._device_info: Any = None
        self._audio_in: Any = None
        self._io: Any = None
        self._timer: QtCore.QTimer | None = None
        self._status_key: str = ""
        self._last_emitted_language: str = ""
        self._last_emitted_source: str = ""
        self._last_emitted_target: str = ""
        self._last_emitted_archive_source: str = ""
        self._last_emitted_archive_target: str = ""
        self._last_spectrum_emit_s: float = 0.0
        self._spectrum_emit_interval_s: float = 0.08
        self._last_backlog_debug_s: float = 0.0
        self._backlog_debug_interval_s: float = 0.6
        self._backlog_compactions: int = 0
        self._pending_chunks: deque[tuple[bytes, float]] = deque()
        self._max_pending_chunks: int = int(Config.live_runtime_profile(output_mode=self._output_mode, preset=self._preset_id).get("max_pending_chunks", 4))
        self._pending_chunks_lock = threading.Lock()
        self._ready_updates: deque[LiveUpdate] = deque()
        self._ready_updates_lock = threading.Lock()
        self._inference_wakeup = threading.Event()
        self._inference_stop = threading.Event()
        self._inference_thread: threading.Thread | None = None
        self._inference_error: Exception | None = None

    # ----- External controls -----

    def cancel(self) -> None:
        _LOG.debug("Live worker cancel requested. worker=live_transcription")
        self._cancel.cancel()

    def stop(self) -> None:
        _LOG.debug("Live worker stop requested. worker=live_transcription")
        self._stop.set()

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
        except (AttributeError, RuntimeError):
            pass
        return False

    def _set_status(self, key: str) -> None:
        key = str(key or "").strip()
        if not key or key == self._status_key:
            return
        self._status_key = key
        self.status.emit(key)

    def _emit_text_update(self, u: LiveUpdate, *, force: bool = False) -> None:
        source_text = str(u.display_source_text or "")
        if force or source_text != self._last_emitted_source:
            self._last_emitted_source = source_text
            self.source_text.emit(source_text)

        target_text = str(u.display_target_text or "")
        if force or target_text != self._last_emitted_target:
            self._last_emitted_target = target_text
            self.target_text.emit(target_text)

        archive_source_text = str(u.archive_source_text or "")
        if force or archive_source_text != self._last_emitted_archive_source:
            self._last_emitted_archive_source = archive_source_text
            self.archive_source_text.emit(archive_source_text)

        archive_target_text = str(u.archive_target_text or "")
        if force or archive_target_text != self._last_emitted_archive_target:
            self._last_emitted_archive_target = archive_target_text
            self.archive_target_text.emit(archive_target_text)

    def _emit_updates(self, updates: list[LiveUpdate], *, force: bool = False) -> None:
        if not updates:
            return

        u = updates[-1]

        detected_language = str(u.detected_language or "")
        if detected_language and (force or detected_language != self._last_emitted_language):
            self._last_emitted_language = detected_language
            _LOG.debug("Live worker detected language updated. lang=%s", detected_language)
            self.detected_language.emit(detected_language)

        self._emit_text_update(u, force=force)

    def _stop_requested(self) -> bool:
        return self._stop.is_set() or self._is_cancelled()

    def _clear_pending_chunks(self) -> None:
        with self._pending_chunks_lock:
            self._pending_chunks.clear()
        self._inference_wakeup.clear()

    def _clear_ready_updates(self) -> None:
        with self._ready_updates_lock:
            self._ready_updates.clear()

    def _emit_pending_updates(self, *, force: bool = False) -> None:
        updates = self._drain_ready_updates()
        if updates:
            self._emit_updates(updates, force=force)

    @staticmethod
    def _level_from_audio(audio: np.ndarray) -> float:
        if audio.size == 0:
            return 0.0

        audio = audio - float(audio.mean())

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

    @staticmethod
    def _meter_from_level(level: float) -> list[float]:
        bars = 18
        lvl = max(0.0, min(1.0, float(level or 0.0)))
        filled = lvl * float(bars)
        full = int(filled)
        frac = float(filled - full)

        out = [0.0] * bars
        for idx in range(bars):
            if idx < full:
                out[idx] = 1.0
            elif idx == full and frac > 0.0:
                out[idx] = min(1.0, frac)
        return out

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
    def _normalize_pcm16(chunk: bytes, fmt: Any, qt_multimedia: Any) -> bytes:
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
            sample_type = qt_multimedia.QAudioFormat.SignedInt

        try:
            byte_order = fmt.byteOrder()
        except Exception:
            byte_order = qt_multimedia.QAudioFormat.LittleEndian

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
            and sample_type == qt_multimedia.QAudioFormat.SignedInt
            and byte_order == qt_multimedia.QAudioFormat.LittleEndian
        ):
            return chunk

        endian = "<" if byte_order == qt_multimedia.QAudioFormat.LittleEndian else ">"

        if sample_type == qt_multimedia.QAudioFormat.UnSignedInt:
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

    def _audio_error_detail(self, err: Any) -> str:
        qt_multimedia = self._qtmm
        if qt_multimedia is None:
            return str(err)
        try:
            error_names = {
                qt_multimedia.QAudio.NoError: "no_error",
                qt_multimedia.QAudio.OpenError: "open_error",
                qt_multimedia.QAudio.IOError: "io_error",
                qt_multimedia.QAudio.UnderrunError: "underrun_error",
                qt_multimedia.QAudio.FatalError: "fatal_error",
            }
            name = error_names.get(err, "unknown")
            return f"audio_error:{name}"
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return str(err)

    @staticmethod
    def _validate_audio_format(*, fmt: Any, qt_multimedia: Any) -> None:
        try:
            if int(fmt.sampleSize() or 0) != 16:
                raise LiveError("error.live.microphone_format_unsupported")
            sample_type = fmt.sampleType()
            if sample_type not in (qt_multimedia.QAudioFormat.SignedInt, qt_multimedia.QAudioFormat.UnSignedInt):
                raise LiveError("error.live.microphone_format_unsupported")
            codec = str(fmt.codec() or "").strip().lower()
            if codec and codec != "audio/pcm":
                raise LiveError("error.live.microphone_format_unsupported")
        except LiveError:
            raise
        except Exception as exc:
            raise LiveError("error.live.microphone_format_unsupported") from exc

    def _resolve_audio_runtime(self) -> tuple[Any, Any, Any]:
        qt_multimedia, dev = resolve_input_device(self._device_name)
        fmt = make_pcm16_mono_format()

        if dev is None:
            try:
                dev = qt_multimedia.QAudioDeviceInfo.defaultInputDevice()
            except (AttributeError, RuntimeError):
                dev = None
            _LOG.debug("Live worker using default input device. requested_device=%s", self._device_name)

        if dev is not None:
            _, fmt = ensure_supported_format(dev, fmt)

        self._validate_audio_format(fmt=fmt, qt_multimedia=qt_multimedia)
        return qt_multimedia, dev, fmt

    @staticmethod
    def _create_audio_input(*, qt_multimedia: Any, dev: Any, fmt: Any) -> Any:
        if dev is None:
            return qt_multimedia.QAudioInput(fmt)
        return qt_multimedia.QAudioInput(dev, fmt)

    def _start_audio_input(self) -> None:
        if self._qtmm is None or self._fmt is None:
            raise LiveError("error.live.audio_input_start_failed")

        self._audio_in = self._create_audio_input(
            qt_multimedia=self._qtmm,
            dev=self._device_info,
            fmt=self._fmt,
        )

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

    def _create_live_session(self) -> LiveSession:
        return LiveSession(
            pipe=self._pipe,
            source_language=self._src_lang,
            target_language=self._tgt_lang,
            translate_enabled=self._translate_enabled,
            cancel_check=self._is_cancelled,
            preset_id=self._preset_id,
            output_mode=self._output_mode,
        )

    def _start_tick_timer(self) -> None:
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        _LOG.debug("Live worker timer started. interval_ms=%s", self._timer.interval())

    def _read_available_audio_chunk(self) -> bytes:
        if self._io is None or self._qtmm is None or self._fmt is None:
            return b""
        try:
            chunk = bytes(self._io.readAll())
        except Exception:
            return b""
        if not chunk:
            return b""
        return self._normalize_pcm16(chunk, self._fmt, self._qtmm)

    def _emit_spectrum_if_due(self, *, level: float) -> None:
        meter = self._meter_from_level(level)
        now_s = time.monotonic()
        if (now_s - self._last_spectrum_emit_s) < self._spectrum_emit_interval_s:
            return
        self._last_spectrum_emit_s = now_s
        try:
            self.spectrum.emit(meter)
        except Exception:
            pass

    def _update_audio_capture_state(self) -> None:
        if self._audio_in is None or self._qtmm is None:
            return
        if self._stop_requested():
            return
        if self._pause.is_set():
            if self._audio_in.state() == self._qtmm.QAudio.ActiveState:
                try:
                    self._audio_in.suspend()
                except Exception:
                    pass
            self._set_status("status.paused")
            return

        if self._audio_in.state() == self._qtmm.QAudio.SuspendedState:
            try:
                self._audio_in.resume()
            except Exception:
                pass
        self._set_status("status.listening")

    def _finalize_worker(self) -> bool:
        if self._run_finished:
            return False
        self._run_finished = True
        self._cleanup()
        self.finished.emit()
        return True

    def _finish_requested_run(self) -> bool:
        if not self._flush_live_session():
            return False
        return self._finalize_worker()

    def _flush_pending_audio_input(self) -> None:
        tail_chunk = self._read_available_audio_chunk()
        if not tail_chunk:
            return
        self._queue_chunk(tail_chunk, self._chunk_level(tail_chunk))

    def _flush_live_session(self) -> bool:
        if self._session is None:
            return True
        try:
            self._flush_pending_audio_input()
            self._stop_inference_thread()
            self._emit_pending_updates(force=True)
            if self._inference_error is not None:
                raise self._inference_error
            return True
        except Exception as ex:
            self._clear_pending_chunks()
            self._fail(ex)
            return False

    def _cleanup(self) -> None:
        try:
            if self._timer is not None:
                self._timer.stop()
                self._timer.deleteLater()
        except Exception:
            pass
        self._timer = None
        self._stop_inference_thread()
        self._clear_pending_chunks()
        self._clear_ready_updates()

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
        self._device_info = None
        self._fmt = None
        self._qtmm = None
        self._session = None

    def _fail(self, err: Any) -> None:
        if self._run_finished:
            return

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
        self._finalize_worker()

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

    def _start_inference_thread(self) -> None:
        self._inference_stop.clear()
        self._inference_error = None
        self._inference_thread = threading.Thread(
            target=self._inference_loop,
            name="live_transcription_inference",
            daemon=True,
        )
        self._inference_thread.start()

    @staticmethod
    def _merge_pending_items(first: tuple[bytes, float], second: tuple[bytes, float]) -> tuple[bytes, float]:
        first_chunk, first_level = first
        second_chunk, second_level = second
        merged_chunk = bytes(first_chunk or b"") + bytes(second_chunk or b"")
        merged_level = max(float(first_level or 0.0), float(second_level or 0.0))
        return merged_chunk, merged_level

    def _compact_pending_backlog_locked(self) -> int:
        compactions = 0
        limit = max(1, int(self._max_pending_chunks))
        while len(self._pending_chunks) > limit and len(self._pending_chunks) >= 2:
            first = self._pending_chunks.popleft()
            second = self._pending_chunks.popleft()
            self._pending_chunks.appendleft(self._merge_pending_items(first, second))
            compactions += 1
        self._backlog_compactions += compactions
        return compactions

    def _queue_chunk(self, chunk: bytes, level: float) -> None:
        with self._pending_chunks_lock:
            self._pending_chunks.append((chunk, level))
            compactions = self._compact_pending_backlog_locked()
            backlog = len(self._pending_chunks)
        self._inference_wakeup.set()

        if _LOG.isEnabledFor(logging.DEBUG):
            now_s = time.monotonic()
            should_log = bool(compactions) or backlog >= self._max_pending_chunks
            if should_log and (now_s - self._last_backlog_debug_s) >= self._backlog_debug_interval_s:
                self._last_backlog_debug_s = now_s
                _LOG.debug(
                    "Live audio backlog updated. worker=live_transcription backlog=%s compacted=%s total_compactions=%s",
                    backlog,
                    int(compactions),
                    int(self._backlog_compactions),
                )

    def _pop_chunk(self) -> tuple[bytes, float] | None:
        with self._pending_chunks_lock:
            if not self._pending_chunks:
                self._inference_wakeup.clear()
                return None
            chunk = self._pending_chunks.popleft()
            if not self._pending_chunks:
                self._inference_wakeup.clear()
            return chunk

    def _push_ready_updates(self, updates: list[LiveUpdate]) -> None:
        if not updates:
            return
        with self._ready_updates_lock:
            self._ready_updates.extend(updates)

    def _drain_ready_updates(self) -> list[LiveUpdate]:
        with self._ready_updates_lock:
            if not self._ready_updates:
                return []
            updates = list(self._ready_updates)
            self._ready_updates.clear()
            return updates

    def _inference_loop(self) -> None:
        try:
            while True:
                item = self._pop_chunk()
                if item is None:
                    if self._inference_stop.is_set():
                        break
                    self._inference_wakeup.wait(0.05)
                    continue

                if self._session is None:
                    continue

                chunk, level = item
                updates = self._session.push_pcm16(
                    chunk,
                    level=level,
                    ignore_cancel=self._inference_stop.is_set(),
                )
                self._push_ready_updates(updates)
        except Exception as ex:
            self._inference_error = ex
        finally:
            try:
                if self._session is not None and self._inference_error is None:
                    self._push_ready_updates(self._session.finalize(ignore_cancel=True))
            except Exception as ex:
                self._inference_error = ex

    def _stop_inference_thread(self) -> None:
        self._inference_stop.set()
        self._inference_wakeup.set()
        th = self._inference_thread
        if th is not None and th.is_alive():
            th.join()
        self._inference_thread = None

    @QtCore.pyqtSlot()
    def _on_ready_read(self) -> None:
        if self._audio_in is None:
            return

        chunk = self._read_available_audio_chunk()
        if not chunk:
            return

        level = self._chunk_level(chunk)
        self._emit_spectrum_if_due(level=level)

        if self._pause.is_set() or self._stop_requested() or self._session is None:
            return

        self._queue_chunk(chunk, level)

    @QtCore.pyqtSlot()
    def _tick(self) -> None:
        if self._audio_in is None or self._qtmm is None:
            return

        if self._inference_error is not None:
            self._fail(self._inference_error)
            return

        self._emit_pending_updates()

        if self._stop_requested():
            self._finish_requested_run()
            return

        try:
            self._update_audio_capture_state()
        except Exception:
            pass

    # ----- Run -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        self._set_status("status.initializing")
        self._stop.clear()

        try:
            _LOG.debug(
                "Live worker starting. worker=live_transcription device=%s source_language=%s target_language=%s translate_enabled=%s preset=%s output_mode=%s",
                self._device_name,
                self._src_lang,
                self._tgt_lang,
                bool(self._translate_enabled),
                self._preset_id,
                self._output_mode,
            )

            self._qtmm, self._device_info, self._fmt = self._resolve_audio_runtime()
            _LOG.debug(
                "Live worker audio format resolved. sample_rate=%s channels=%s sample_size=%s codec=%s",
                int(self._fmt.sampleRate() or 0),
                int(self._fmt.channelCount() or 0),
                int(self._fmt.sampleSize() or 0),
                str(self._fmt.codec() or ""),
            )

            self._start_audio_input()
            self._session = self._create_live_session()
            self._start_inference_thread()
            self._set_status("status.listening")
            _LOG.debug("Live worker session initialized. worker=live_transcription")

            self._start_tick_timer()
        except Exception as ex:
            _LOG.exception("Live transcription failed.")
            self._fail(ex)
