# controller/tasks/live_transcription_task.py
from __future__ import annotations

import time
import threading
from typing import Dict, Any, Tuple, List

from PyQt5 import QtCore
import numpy as np

from model.config.app_config import AppConfig as Config
from model.io.text import TextPostprocessor
from model.services.translation_service import TranslationService
from controller.platform.microphone import (
    resolve_input_device,
    make_pcm16_mono_format,
    ensure_supported_format,
    format_is_pcm16_mono_16k,
)
from view.utils.translating import tr


class _Cancelled(RuntimeError):
    pass


class LiveTranscriptionWorker(QtCore.QObject):
    """
    Captures audio from an input device and performs near real-time ASR using the
    already-loaded transformers pipeline.

    Adds a small FFT-based spectrum signal for input diagnostics.
    """

    log = QtCore.pyqtSignal(str)
    status = QtCore.pyqtSignal(str)
    detected_language = QtCore.pyqtSignal(str)
    source_text = QtCore.pyqtSignal(str)
    target_text = QtCore.pyqtSignal(str)
    spectrum = QtCore.pyqtSignal(object)  # List[float] in [0..1]
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        pipe: Any,
        device_name: str = "",
        mode: str = "transcribe",
        source_language: str = "",
        target_language: str = "en",
        include_source_in_translate: bool = True,
    ) -> None:
        super().__init__()
        self._pipe = pipe
        self._device_name = device_name.strip()
        self._mode = (mode or "transcribe").strip().lower()
        self._src_lang = source_language.strip().lower()
        self._tgt_lang = target_language.strip().lower() or "en"
        self._include_source = bool(include_source_in_translate)

        self._cancel = threading.Event()
        self._pause = threading.Event()

        self._merged_source = ""
        self._merged_target = ""

        # Optional text translation
        self._translator = TranslationService()

    # ----- External controls -----

    def cancel(self) -> None:
        self._cancel.set()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def _is_cancelled(self) -> bool:
        if self._cancel.is_set():
            return True
        try:
            return bool(QtCore.QThread.currentThread().isInterruptionRequested())
        except Exception:
            return False

    def _ensure_not_cancelled(self) -> None:
        if self._is_cancelled():
            raise _Cancelled()

    def _is_paused(self) -> bool:
        return bool(self._pause.is_set())

    # ----- Text merge -----

    @staticmethod
    def _merge_text(prev: str, cur: str) -> str:
        if not prev:
            return cur
        if not cur:
            return prev

        prev_words = prev.split()
        cur_words = cur.split()

        max_k = min(12, len(prev_words), len(cur_words))
        for k in range(max_k, 0, -1):
            if prev_words[-k:] == cur_words[:k]:
                cur_words = cur_words[k:]
                break

        if not cur_words:
            return prev
        return (prev + " " + " ".join(cur_words)).strip()

    def _call_pipe_safe(
        self,
        audio: np.ndarray,
        sr: int,
        *,
        return_timestamps: bool,
        generate_kwargs: Dict[str, Any],
        ignore_warning: bool,
    ) -> Dict[str, Any]:
        self._ensure_not_cancelled()
        payload = {"array": audio, "sampling_rate": sr}

        try:
            try:
                result = self._pipe(
                    payload,
                    return_timestamps=return_timestamps,
                    generate_kwargs=generate_kwargs,
                    ignore_warning=ignore_warning,
                )
            except TypeError:
                result = self._pipe(payload, return_timestamps=return_timestamps, generate_kwargs=generate_kwargs)

            return result if isinstance(result, dict) else {"text": str(result)}

        except Exception as e:
            msg = str(e)
            needs_ts = (
                "requires the model to predict timestamp tokens" in msg
                or "pass `return_timestamps=True`" in msg
                or "long-form generation" in msg
            )
            if needs_ts and not return_timestamps:
                try:
                    try:
                        result = self._pipe(
                            payload,
                            return_timestamps=True,
                            generate_kwargs=generate_kwargs,
                            ignore_warning=ignore_warning,
                        )
                    except TypeError:
                        result = self._pipe(payload, return_timestamps=True, generate_kwargs=generate_kwargs)
                    return result if isinstance(result, dict) else {"text": str(result)}
                except Exception:
                    raise e
            raise
    # ----- Optional text translation -----

    def _translate_text(self, text: str, *, src: str, tgt: str) -> str:
        if not text.strip():
            return ""
        try:
            return self._translator.translate(
                text,
                src_lang=src,
                tgt_lang=tgt,
                log=lambda m: self.log.emit(str(m)),
            )
        except Exception:
            return ""

    # ----- Spectrum helper -----


    @staticmethod
    def _compute_spectrum(audio: np.ndarray, *, bars: int = 24) -> List[float]:
        """
        Return a coarse spectrum (bars values in [0..1]).
        """
        try:
            x = np.asarray(audio, dtype=np.float32)
            if x.size < 256:
                return [0.0] * bars

            # window + rfft
            w = np.hanning(x.size).astype(np.float32)
            X = np.fft.rfft(x * w)
            mag = np.abs(X).astype(np.float32)

            # ignore DC, compress
            if mag.size > 2:
                mag[0] = 0.0
            mag = np.log1p(mag)

            # bin into bars
            out = []
            n = mag.size
            for i in range(bars):
                a = int(i * n / bars)
                b = int((i + 1) * n / bars)
                if b <= a:
                    b = a + 1
                seg = mag[a:b]
                v = float(np.max(seg)) if seg.size else 0.0
                out.append(v)

            mx = max(out) if out else 1.0
            if mx <= 1e-8:
                return [0.0] * bars
            out = [min(1.0, max(0.0, v / mx)) for v in out]
            return out
        except Exception:
            return [0.0] * bars

    # ----- Main loop -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        self.status.emit(tr("status.preparing"))
        snap = Config.SETTINGS
        if snap is None:
            raise RuntimeError("error.runtime.settings_not_initialized")
        model_cfg = snap.model.get("transcription_model", {}) if isinstance(snap.model, dict) else {}

        chunk_len_s = int(model_cfg.get("chunk_length_s", 5))
        stride_len_s = int(model_cfg.get("stride_length_s", 1))
        ignore_warn = bool(model_cfg.get("ignore_warning", True))

        chunk_len_s = max(1, min(30, chunk_len_s))
        stride_len_s = max(0, min(chunk_len_s - 1, stride_len_s))

        sr = 16000
        chunk_frames = int(chunk_len_s * sr)
        stride_frames = int(stride_len_s * sr)
        step_frames = max(1, chunk_frames - stride_frames)

        QtMultimedia, dev = resolve_input_device(self._device_name)
        if dev is None:
            self.log.emit(tr("log.live.audio.no_devices"))
            self.status.emit(tr("status.error"))
            self.finished.emit()
            return

        desired = make_pcm16_mono_format(sample_rate=sr)
        _exact, fmt = ensure_supported_format(dev, desired)

        if not format_is_pcm16_mono_16k(fmt, sample_rate=sr):
            try:
                rate = str(fmt.sampleRate())
                ch = str(fmt.channelCount())
                bits = str(fmt.sampleSize())
            except Exception:
                rate, ch, bits = "?", "?", "?"
            self.log.emit(tr("log.live.audio.format_unsupported", rate=rate, channels=ch, bits=bits))
            self.status.emit(tr("status.error"))
            self.finished.emit()
            return

        audio_in = None
        io = None

        try:
            self._merged_source = ""
            self._merged_target = ""

            audio_in = QtMultimedia.QAudioInput(dev, fmt)
            io = audio_in.start()
            self.status.emit(tr("status.listening"))

            buf = bytearray()
            processed_frames = 0
            return_ts = False
            was_paused = False

            def _asr_generate_kwargs(task: str) -> Dict[str, Any]:
                kw: Dict[str, Any] = {"task": task}
                if self._src_lang:
                    kw["language"] = self._src_lang
                return kw

            while True:
                self._ensure_not_cancelled()

                # Pause
                if self._is_paused():
                    if not was_paused:
                        was_paused = True
                        try:
                            if audio_in is not None and hasattr(audio_in, "suspend"):
                                audio_in.suspend()  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        self.status.emit(tr("status.paused"))
                        self.spectrum.emit([0.0] * 24)

                    time.sleep(0.05)
                    continue

                if was_paused:
                    was_paused = False
                    try:
                        if audio_in is not None and hasattr(audio_in, "resume"):
                            audio_in.resume()  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    self.status.emit(tr("status.listening"))
                    buf.clear()
                    processed_frames = 0

                try:
                    avail = int(io.bytesAvailable()) if io is not None else 0
                except Exception:
                    avail = 0

                if avail <= 0:
                    time.sleep(0.01)
                    continue

                chunk = bytes(io.read(avail))
                if chunk:
                    buf.extend(chunk)

                total_frames = len(buf) // 2  # 16-bit mono

                # Emit “quick” spectrum from latest slice if possible (diagnostic)
                try:
                    tail_frames = min(total_frames, 2048)
                    if tail_frames >= 512:
                        tail_b = tail_frames * 2
                        tail_pcm = np.frombuffer(buf[-tail_b:], dtype=np.int16)
                        tail_audio = tail_pcm.astype(np.float32) / 32768.0
                        self.spectrum.emit(self._compute_spectrum(tail_audio, bars=24))
                except Exception:
                    pass

                while (total_frames - processed_frames) >= chunk_frames:
                    self._ensure_not_cancelled()
                    if self._is_paused():
                        break

                    start_b = processed_frames * 2
                    end_b = (processed_frames + chunk_frames) * 2
                    pcm = np.frombuffer(buf[start_b:end_b], dtype=np.int16)
                    audio = pcm.astype(np.float32) / 32768.0

                    mode = self._mode
                    if mode not in ("transcribe", "translate"):
                        mode = "transcribe"

                    if mode == "transcribe":
                        res = self._call_pipe_safe(
                            audio,
                            sr,
                            return_timestamps=return_ts,
                            generate_kwargs=_asr_generate_kwargs("transcribe"),
                            ignore_warning=ignore_warn,
                        )
                        lang = str(res.get("language", "") or "").strip().lower()
                        if lang:
                            self.detected_language.emit(lang)

                        txt = TextPostprocessor.clean(str(res.get("text", "") or ""))
                        if txt:
                            self._merged_source = self._merge_text(self._merged_source, txt)
                            self.source_text.emit(self._merged_source)

                    else:
                        if self._tgt_lang == "en" and not self._include_source:
                            res = self._call_pipe_safe(
                                audio,
                                sr,
                                return_timestamps=return_ts,
                                generate_kwargs=_asr_generate_kwargs("translate"),
                                ignore_warning=ignore_warn,
                            )
                            txt = TextPostprocessor.clean(str(res.get("text", "") or ""))
                            if txt:
                                self._merged_target = self._merge_text(self._merged_target, txt)
                                self.target_text.emit(self._merged_target)

                        else:
                            res_src = self._call_pipe_safe(
                                audio,
                                sr,
                                return_timestamps=return_ts,
                                generate_kwargs=_asr_generate_kwargs("transcribe"),
                                ignore_warning=ignore_warn,
                            )
                            lang = str(res_src.get("language", "") or "").strip().lower()
                            if lang:
                                self.detected_language.emit(lang)

                            src_txt = TextPostprocessor.clean(str(res_src.get("text", "") or ""))
                            if src_txt and self._include_source:
                                self._merged_source = self._merge_text(self._merged_source, src_txt)
                                self.source_text.emit(self._merged_source)

                            if self._tgt_lang == "en":
                                res_tgt = self._call_pipe_safe(
                                    audio,
                                    sr,
                                    return_timestamps=return_ts,
                                    generate_kwargs=_asr_generate_kwargs("translate"),
                                    ignore_warning=ignore_warn,
                                )
                                tgt_txt = TextPostprocessor.clean(str(res_tgt.get("text", "") or ""))
                            else:
                                src_lang = self._src_lang or lang or "auto"
                                tgt_txt = self._translate_text(src_txt, src=src_lang, tgt=self._tgt_lang)

                            if tgt_txt:
                                self._merged_target = self._merge_text(self._merged_target, tgt_txt)
                                self.target_text.emit(self._merged_target)

                    processed_frames += step_frames
                    total_frames = len(buf) // 2

                    min_keep_frames = max(0, processed_frames - chunk_frames)
                    if min_keep_frames > 0:
                        drop_b = min_keep_frames * 2
                        del buf[:drop_b]
                        processed_frames -= min_keep_frames

        except _Cancelled:
            pass
        except Exception as e:
            self.log.emit(tr("log.worker_error", detail=str(e)))
            self.status.emit(tr("status.error"))
        finally:
            try:
                if audio_in is not None:
                    audio_in.stop()
            except Exception:
                pass
            self.spectrum.emit([0.0] * 24)
            self.status.emit(tr("status.idle"))
            self.finished.emit()
