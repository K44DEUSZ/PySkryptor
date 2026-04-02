# app/controller/workers/live_worker.py
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
from app.controller.support.cancellation import CancellationToken
from app.controller.workers.session_worker import SessionWorker
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.errors import AppError
from app.model.core.domain.results import LiveUpdate
from app.model.engines.contracts import TranscriptionEngineProtocol, TranslationEngineProtocol
from app.model.transcription.chunking import pcm16le_bytes_to_float32
from app.model.transcription.live import LiveTranscriptionService

_LOG = logging.getLogger(__name__)


class LiveError(AppError):
    """Key-based error used for i18n-friendly live task failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


class LiveWorker(SessionWorker):
    """Captures audio from an input device and performs live transcription."""

    status = QtCore.pyqtSignal(str)
    detected_language = QtCore.pyqtSignal(str)
    source_text = QtCore.pyqtSignal(str)
    target_text = QtCore.pyqtSignal(str)
    archive_source_text = QtCore.pyqtSignal(str)
    archive_target_text = QtCore.pyqtSignal(str)
    spectrum = QtCore.pyqtSignal(object)

    def __init__(
        self,
        *,
        transcription_engine: TranscriptionEngineProtocol,
        translation_engine: TranslationEngineProtocol,
        device_name: str = "",
        source_language: str = "",
        target_language: str = "",
        translate_enabled: bool = False,
        profile: str = RuntimeProfiles.LIVE_DEFAULT_PROFILE,
        runtime_profile: dict[str, Any] | None = None,
        output_mode: str = LiveTranscriptionService.OUTPUT_MODE_CUMULATIVE,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        super().__init__(cancel_token=cancel_token)
        self._transcription_engine = transcription_engine
        self._translation_engine = translation_engine
        self._device_name = str(device_name or "").strip()

        self._src_lang = str(source_language or "").strip()
        self._tgt_lang = str(target_language or "").strip()
        self._translate_enabled = bool(translate_enabled)
        self._output_mode = RuntimeProfiles.normalize_live_output_mode(output_mode)
        self._profile = RuntimeProfiles.normalize_live_profile(profile)
        self._runtime_profile = dict(
            runtime_profile
            or RuntimeProfiles.resolve_live_runtime(output_mode=self._output_mode, profile=self._profile)
        )

        self._pause = threading.Event()

        self._session: LiveTranscriptionService | None = None
        self._qt_multimedia: Any = None
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
        self._max_pending_chunks: int = int(
            self._runtime_profile.get("max_pending_chunks", 4)
        )
        self._pending_chunks_lock = threading.Lock()
        self._ready_updates: deque[LiveUpdate] = deque()
        self._ready_updates_lock = threading.Lock()
        self._inference_wakeup = threading.Event()
        self._inference_stop = threading.Event()
        self._inference_thread: threading.Thread | None = None
        self._inference_error: Exception | None = None

    def cancel(self) -> None:
        _LOG.debug("Live worker cancel requested. worker=live_transcription")
        super().cancel()

    def stop(self) -> None:
        _LOG.debug("Live worker stop requested. worker=live_transcription")
        super().stop()

    def pause(self) -> None:
        _LOG.debug("Live worker pause requested. worker=live_transcription")
        self._pause.set()

    def resume(self) -> None:
        _LOG.debug("Live worker resume requested. worker=live_transcription")
        self._pause.clear()

    def _handle_failure(self, ex: BaseException) -> None:
        self._set_status("status.error")

        if isinstance(ex, AppError):
            self._emit_failure(str(ex.key), dict(ex.params or {}))
            return

        self._emit_failure("error.live.failed", {"detail": str(ex)})

    def _request_stop(self) -> None:
        self._inference_wakeup.set()

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
        level = float(max(0.0, min(1.0, level)))
        bars = 16
        eased = min(1.0, level * 1.2)
        count = int(round(eased * bars))
        values: list[float] = []
        for idx in range(bars):
            if idx < count:
                bar = level * (0.85 + 0.15 * (idx / max(1, bars - 1)))
                values.append(float(max(0.0, min(1.0, bar))))
            else:
                values.append(0.0)
        return values

    @staticmethod
    def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
        if audio.size == 0 or src_sr <= 0 or dst_sr <= 0 or src_sr == dst_sr:
            return audio

        if audio.size == 1:
            return np.repeat(audio, max(1, int(round(dst_sr / float(src_sr))))).astype(np.float32, copy=False)

        src_idx = np.arange(audio.shape[0], dtype=np.float32)
        dst_len = int(round(audio.shape[0] * (float(dst_sr) / float(src_sr))))
        if dst_len <= 1:
            return audio[:1].astype(np.float32, copy=False)
        dst_idx = np.linspace(0.0, float(audio.shape[0] - 1), dst_len, dtype=np.float32)
        out = np.interp(dst_idx, src_idx, audio.astype(np.float32, copy=False))
        return out.astype(np.float32, copy=False)

    @staticmethod
    def _normalize_pcm16(chunk: bytes, fmt: Any, qt_multimedia: Any) -> bytes:
        if not chunk:
            return b""

        byte_order = fmt.byteOrder()
        sample_type = fmt.sampleType()
        sample_size = int(fmt.sampleSize() or 0)
        channels = int(fmt.channelCount() or 0)
        sample_rate = int(fmt.sampleRate() or 0)

        if sample_size != 16:
            raise LiveError("error.live.microphone_format_unsupported")

        if sample_type == qt_multimedia.QAudioFormat.SignedInt:
            dtype = np.dtype("<i2") if byte_order == qt_multimedia.QAudioFormat.LittleEndian else np.dtype(">i2")
        elif sample_type == qt_multimedia.QAudioFormat.UnSignedInt:
            dtype = np.dtype("<u2") if byte_order == qt_multimedia.QAudioFormat.LittleEndian else np.dtype(">u2")
        else:
            raise LiveError("error.live.microphone_format_unsupported")

        arr = np.frombuffer(chunk, dtype=dtype)
        if arr.size == 0:
            return b""

        if sample_type == qt_multimedia.QAudioFormat.UnSignedInt:
            arr = arr.astype(np.int32) - 32768
        else:
            arr = arr.astype(np.int32)

        if channels > 1:
            usable = (arr.size // channels) * channels
            if usable <= 0:
                return b""
            arr = arr[:usable].reshape(-1, channels)
            arr = np.mean(arr, axis=1).astype(np.int32)

        if sample_rate > 0 and sample_rate != 16000:
            mono = arr.astype(np.float32) / 32768.0
            mono = LiveWorker._resample(mono, sample_rate, 16000)
            mono = np.clip(np.round(mono * 32768.0), -32768, 32767).astype(np.int16)
        else:
            mono = np.clip(arr, -32768, 32767).astype(np.int16)

        if byte_order == qt_multimedia.QAudioFormat.BigEndian:
            mono = mono.byteswap()

        return mono.tobytes()

    def _audio_error_detail(self, err: Any) -> str:
        qt_multimedia = self._qt_multimedia
        if qt_multimedia is None:
            return str(err or "audio_error")

        q_audio = getattr(qt_multimedia, "QAudio", None)
        mapping = {
            getattr(q_audio, "OpenError", object()): "open_error",
            getattr(q_audio, "IOError", object()): "io_error",
            getattr(q_audio, "UnderrunError", object()): "underrun_error",
            getattr(q_audio, "FatalError", object()): "fatal_error",
        }
        return str(mapping.get(err, str(err or "audio_error")))

    @staticmethod
    def _validate_audio_format(*, fmt: Any, qt_multimedia: Any) -> None:
        try:
            channels = int(fmt.channelCount() or 0)
            sample_size = int(fmt.sampleSize() or 0)
            sample_type = fmt.sampleType()
            sample_rate = int(fmt.sampleRate() or 0)
            if channels <= 0 or sample_size != 16 or sample_rate <= 0:
                raise LiveError("error.live.microphone_format_unsupported")
            if sample_type not in (
                qt_multimedia.QAudioFormat.SignedInt,
                qt_multimedia.QAudioFormat.UnSignedInt,
            ):
                raise LiveError("error.live.microphone_format_unsupported")
        except LiveError:
            raise
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
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
        if self._qt_multimedia is None or self._fmt is None:
            raise LiveError("error.live.audio_input_start_failed")

        self._audio_in = self._create_audio_input(
            qt_multimedia=self._qt_multimedia,
            dev=self._device_info,
            fmt=self._fmt,
        )

        try:
            self._audio_in.stateChanged.connect(self._on_audio_state_changed)
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug("Live audio state signal hookup skipped. detail=%s", ex)

        self._io = self._audio_in.start()
        if self._io is None:
            raise LiveError("error.live.audio_input_start_failed")

        try:
            self._io.readyRead.connect(self._on_ready_read)
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug("Live audio readyRead hookup skipped. detail=%s", ex)

    def _create_live_session(self) -> LiveTranscriptionService:
        return LiveTranscriptionService(
            transcription_engine=self._transcription_engine,
            translation_engine=self._translation_engine,
            source_language=self._src_lang,
            target_language=self._tgt_lang,
            translate_enabled=self._translate_enabled,
            cancel_check=self.cancel_check,
            profile=self._profile,
            runtime_profile=self._runtime_profile,
            output_mode=self._output_mode,
        )

    def _start_tick_timer(self) -> None:
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        _LOG.debug("Live worker timer started. interval_ms=%s", self._timer.interval())

    def _read_available_audio_chunk(self) -> bytes:
        if self._io is None or self._qt_multimedia is None or self._fmt is None:
            return b""
        try:
            chunk = bytes(self._io.readAll())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return b""
        if not chunk:
            return b""
        return self._normalize_pcm16(chunk, self._fmt, self._qt_multimedia)

    def _emit_spectrum_if_due(self, *, level: float) -> None:
        meter = self._meter_from_level(level)
        now_s = time.monotonic()
        if (now_s - self._last_spectrum_emit_s) < self._spectrum_emit_interval_s:
            return
        self._last_spectrum_emit_s = now_s
        try:
            self.spectrum.emit(meter)
        except (RuntimeError, TypeError) as ex:
            _LOG.debug("Live spectrum update skipped. detail=%s", ex)

    def _update_audio_capture_state(self) -> None:
        if self._audio_in is None or self._qt_multimedia is None:
            return
        if self.is_stop_requested():
            return
        if self._pause.is_set():
            if self._audio_in.state() == self._qt_multimedia.QAudio.ActiveState:
                try:
                    self._audio_in.suspend()
                except (AttributeError, RuntimeError, TypeError) as ex:
                    _LOG.debug("Live audio suspend skipped. detail=%s", ex)
            self._set_status("status.paused")
            return

        if self._audio_in.state() == self._qt_multimedia.QAudio.SuspendedState:
            try:
                self._audio_in.resume()
            except (AttributeError, RuntimeError, TypeError) as ex:
                _LOG.debug("Live audio resume skipped. detail=%s", ex)
        self._set_status("status.listening")

    def _flush_pending_audio_input(self) -> None:
        tail_chunk = self._read_available_audio_chunk()
        if not tail_chunk:
            return
        self._queue_chunk(tail_chunk, self._chunk_level(tail_chunk))

    def _complete_stop_sequence(self) -> None:
        if self.is_finalized():
            return

        try:
            self._flush_pending_audio_input()
            self._stop_inference_thread()
            self._emit_pending_updates(force=True)
            if self._inference_error is not None:
                raise self._inference_error
            self._shutdown_session()
        except (AttributeError, RuntimeError, TypeError, ValueError) as ex:
            self._complete_failure_sequence(ex)
            return

        self._finish_success()

    def _complete_cancel_sequence(self) -> None:
        if self.is_finalized():
            return

        try:
            self._stop_inference_thread()
            self._clear_pending_chunks()
            self._clear_ready_updates()
            self._shutdown_session()
        except Exception as ex:
            self._complete_failure_sequence(ex)
            return

        self._finish_cancelled()

    def _complete_failure_sequence(self, ex: BaseException) -> None:
        if self.is_finalized():
            return

        try:
            self._shutdown_session()
        except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
            _LOG.error("Live worker shutdown after failure failed.", exc_info=True)

        self._finish_failure(ex)

    def _shutdown_session(self) -> None:
        try:
            if self._timer is not None:
                self._timer.stop()
                self._timer.deleteLater()
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug("Live timer shutdown skipped. detail=%s", ex)
        self._timer = None

        self._stop_inference_thread()
        self._clear_pending_chunks()
        self._clear_ready_updates()

        if self._io is not None:
            try:
                self._io.readyRead.disconnect(self._on_ready_read)
            except (AttributeError, RuntimeError, TypeError) as ex:
                _LOG.debug("Live readyRead disconnect skipped. detail=%s", ex)

        if self._audio_in is not None:
            try:
                self._audio_in.stateChanged.disconnect(self._on_audio_state_changed)
            except (AttributeError, RuntimeError, TypeError) as ex:
                _LOG.debug("Live audio state disconnect skipped. detail=%s", ex)
            try:
                self._audio_in.stop()
            except (AttributeError, RuntimeError, TypeError) as ex:
                _LOG.debug("Live audio stop skipped. detail=%s", ex)

        self._io = None
        self._audio_in = None
        self._device_info = None
        self._fmt = None
        self._qt_multimedia = None
        self._session = None

    def _on_audio_state_changed(self, state: int) -> None:
        if self._audio_in is None or self._qt_multimedia is None:
            return
        if self.cancel_check():
            return
        try:
            if state == self._qt_multimedia.QAudio.StoppedState:
                err = self._audio_in.error()
                if err != self._qt_multimedia.QAudio.NoError:
                    self._complete_failure_sequence(RuntimeError(self._audio_error_detail(err)))
        except (AttributeError, RuntimeError, TypeError, ValueError) as ex:
            self._complete_failure_sequence(ex)

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
                    (
                        "Live audio backlog updated. worker=live_transcription backlog=%s compacted=%s "
                        "total_compactions=%s"
                    ),
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

        if self._pause.is_set() or self.is_stop_requested() or self._session is None:
            return

        self._queue_chunk(chunk, level)

    @QtCore.pyqtSlot()
    def _tick(self) -> None:
        if self._audio_in is None or self._qt_multimedia is None:
            return

        if self.cancel_check():
            self._complete_cancel_sequence()
            return

        if self._inference_error is not None:
            self._complete_failure_sequence(self._inference_error)
            return

        self._emit_pending_updates()

        if self.is_stop_requested():
            self._complete_stop_sequence()
            return

        try:
            self._update_audio_capture_state()
        except (AttributeError, RuntimeError, TypeError, ValueError) as ex:
            self._complete_failure_sequence(ex)

    def _start_session(self) -> None:
        self._set_status("status.initializing")
        self._stop_requested.clear()
        self._pause.clear()

        _LOG.debug(
            (
                "Live worker starting. worker=live_transcription device=%s source_language=%s "
                "target_language=%s translate_enabled=%s profile=%s output_mode=%s"
            ),
            self._device_name,
            self._src_lang,
            self._tgt_lang,
            bool(self._translate_enabled),
            self._profile,
            self._output_mode,
        )

        self._qt_multimedia, self._device_info, self._fmt = self._resolve_audio_runtime()
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
