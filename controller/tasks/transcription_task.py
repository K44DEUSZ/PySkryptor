# view/controller/transcription_task.py
from __future__ import annotations

import math
import shutil
import threading
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np
from PyQt5 import QtCore

from model.config.app_config import AppConfig as Config
from model.io.audio_extractor import AudioExtractor
from model.io.file_manager import FileManager
from model.io.text import TextPostprocessor, is_url, sanitize_filename
from model.services.conflict_service import ConflictService
from model.services.download_service import DownloadCancelled, DownloadError, DownloadService
from view.utils.concurrency import CancellationToken
from view.utils.translating import tr

GUIEntry = Union[str, Dict[str, Any]]
WorkItem = Tuple[str, Path, Optional[str]]


class _Cancelled(RuntimeError):
    pass


class _FatalPipeError(RuntimeError):
    """A pipeline error that should stop the whole session (config/model mismatch)."""


class TranscriptionWorker(QtCore.QObject):
    """Transcribe a list of files/URLs in a background thread."""

    finished = QtCore.pyqtSignal()
    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)

    item_status = QtCore.pyqtSignal(str, str)         # key, status string
    item_progress = QtCore.pyqtSignal(str, int)       # key, pct
    item_path_update = QtCore.pyqtSignal(str, str)    # old_key, new_key
    transcript_ready = QtCore.pyqtSignal(str, str)    # key, transcript path

    conflict_check = QtCore.pyqtSignal(str, str)      # stem, existing_dir

    # session_dir, processed_any, had_errors, was_cancelled
    session_done = QtCore.pyqtSignal(str, bool, bool, bool)

    def __init__(self, pipe, entries: List[GUIEntry]) -> None:
        super().__init__()
        self._pipe = pipe
        self._entries = list(entries)

        self._download = DownloadService()
        self._downloaded: Set[Path] = set()

        self._token = CancellationToken()
        self._cancel_evt = threading.Event()

        self._conflict_event = threading.Event()
        self._conflict_action: Optional[str] = None
        self._conflict_new_stem: str = ""
        self._conflict_apply_all: bool = False

        self._total_chunks: int = 0
        self._done_chunks: int = 0

        self._had_errors: bool = False

    # ----- Cancellation -----

    @QtCore.pyqtSlot()
    def cancel(self) -> None:
        self._token.cancel()
        self._cancel_evt.set()

    def _is_cancelled(self) -> bool:
        if self._token.is_cancelled:
            return True
        th = QtCore.QThread.currentThread()
        return bool(th and th.isInterruptionRequested())

    def _ensure_not_cancelled(self) -> None:
        if self._is_cancelled():
            raise _Cancelled()

    # ----- Conflict decision from UI -----

    def on_conflict_decided(self, action: str, new_name: str = "", apply_all: bool = False) -> None:
        self._conflict_action = str(action or "").strip().lower()
        self._conflict_new_stem = str(new_name or "").strip()
        self._conflict_apply_all = bool(apply_all)
        try:
            self._conflict_event.set()
        except Exception:
            pass

    # ----- Main -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        processed_any = False
        self._had_errors = False

        keep_wav_with_transcript = False

        try:
            FileManager.plan_session()

            # ----- Read settings -----
            model_cfg = Config.model_settings()
            trans_cfg = Config.transcription_settings()

            task = str(model_cfg.get("ai_engine_name") or "transcribe").strip().lower()
            if task not in ("transcribe", "translate"):
                task = "transcribe"

            chunk_len = int(model_cfg.get("chunk_length_s", 30))
            stride_len = int(model_cfg.get("stride_length_s", 5))
            ignore_warn = bool(model_cfg.get("ignore_warning", True))
            default_lang = model_cfg.get("default_language", None)
            timestamps_output = bool(trans_cfg.get("timestamps_output", False))
            out_ext = str(trans_cfg.get("output_ext") or "txt").strip().lower().lstrip(".") or "txt"

            # NOTE: keep_wav_temp is now interpreted as:
            # "Keep WAV audio next to transcript" (per-item output folder).
            keep_wav_with_transcript = bool(trans_cfg.get("keep_wav_temp", False))

            want_timestamped_output = bool(out_ext == "srt" or timestamps_output)
            # User should not manage timestamps as a separate setting.
            # We request timestamps only when required by the output format or by long-form generation.
            return_ts_base = bool(want_timestamped_output)

            # ----- Materialize entries into work items -----
            work_items: List[WorkItem] = []
            for entry in self._entries:
                if self._is_cancelled():
                    break
                try:
                    work_items.extend(self._materialize_entry(entry))
                except _Cancelled:
                    break
                except Exception as e:
                    self._had_errors = True
                    self.log.emit(tr("log.worker_error", detail=str(e)))

            if self._is_cancelled():
                raise _Cancelled()

            if not work_items:
                self.progress.emit(0)
                self.finished.emit()
                return

            # ----- Global progress estimation -----
            total_dur = 0.0
            for _key, p, _forced in work_items:
                try:
                    d = AudioExtractor.probe_duration(p)
                    total_dur += float(d or 0.0)
                except Exception:
                    pass

            self._total_chunks = max(1, int(self._estimate_chunks(total_dur, chunk_len, stride_len)))
            self._bump_global_progress()

            # ----- Process items -----
            for key, path, forced_stem in work_items:
                if self._is_cancelled():
                    break

                stem = sanitize_filename(forced_stem) if forced_stem else sanitize_filename(path.stem)

                self.item_status.emit(key, tr("status.prep"))
                self.item_progress.emit(key, 0)

                # ----- Resolve output folder conflicts -----
                out_dir = self._resolve_output_dir(stem)
                if out_dir is None:
                    self._had_errors = True
                    self.item_status.emit(key, tr("status.error"))
                    continue

                stem = out_dir.name  # might change after conflict resolution
                self.item_status.emit(key, tr("status.proc"))

                tmp_wav: Optional[Path] = None
                wav_path: Optional[Path] = None

                try:
                    wav_path = FileManager.ensure_tmp_wav(
                        path,
                        log=lambda m: self.log.emit(str(m)),
                        cancel_check=self._is_cancelled,
                    )
                    tmp_wav = wav_path if wav_path != path else None

                    if self._is_cancelled():
                        break

                    # ----- Transcribe (chunked) -----
                    merged_text, segments = self._transcribe_wav(
                        key=key,
                        wav_path=wav_path,
                        task=task,
                        chunk_len_s=chunk_len,
                        stride_len_s=stride_len,
                        default_lang=default_lang,
                        return_ts_base=return_ts_base,
                        ignore_warn=ignore_warn,
                    )

                    # ----- Write outputs -----
                    transcript_path = self._write_outputs(
                        key=key,
                        stem=stem,
                        out_dir=out_dir,
                        merged_text=merged_text,
                        segments=segments,
                        out_ext=out_ext,
                        timestamps_output=timestamps_output,
                    )
                    if transcript_path is None:
                        self._had_errors = True
                        self.item_status.emit(key, tr("status.error"))
                        continue

                    # ----- Optionally keep WAV next to transcript -----
                    if keep_wav_with_transcript and wav_path is not None:
                        try:
                            self._persist_wav_asset(
                                stem=stem,
                                out_dir=out_dir,
                                wav_path=wav_path,
                                tmp_wav=tmp_wav,
                            )
                            # If we moved the temp wav into output, do not delete it below.
                            if tmp_wav is not None and FileManager.audio_wav_path(stem).exists():
                                tmp_wav = None
                        except Exception as e:
                            self._had_errors = True
                            self.log.emit(tr("log.worker_error", detail=str(e)))

                    # ----- Mark done -----
                    self.item_progress.emit(key, 100)
                    self.item_status.emit(key, tr("status.done"))
                    processed_any = True

                except _FatalPipeError as e:
                    self._had_errors = True
                    self.log.emit(tr("log.transcription_failed", name="ASR pipeline", detail=str(e)))
                    self.item_status.emit(key, tr("status.error"))
                    break
                except _Cancelled:
                    break
                except Exception as e:
                    self._had_errors = True
                    self.log.emit(tr("log.transcription_failed", name=str(path.name), detail=str(e)))
                    self.item_status.emit(key, tr("status.error"))
                    continue
                finally:
                    # ----- Cleanup temp WAV (if not preserved) -----
                    if tmp_wav is not None and tmp_wav.exists() and not keep_wav_with_transcript:
                        try:
                            tmp_wav.unlink(missing_ok=True)  # type: ignore[call-arg]
                        except Exception:
                            pass

                    # ----- Cleanup downloaded source (URL temp file) -----
                    try:
                        if path in self._downloaded:
                            try:
                                path.unlink(missing_ok=True)  # type: ignore[call-arg]
                            except Exception:
                                pass
                            self._downloaded.discard(path)
                    except Exception:
                        pass

            # ----- Final log -----
            if self._is_cancelled():
                self.log.emit(tr("log.cancelled"))
            elif self._had_errors:
                self.log.emit(tr("log.finished_with_errors"))
            elif processed_any:
                self.log.emit(tr("log.done"))

        except Exception as e:
            self._had_errors = True
            self.log.emit(tr("log.worker_error", detail=str(e)))

        finally:
            session_dir_str = ""
            try:
                session_dir_str = str(FileManager.session_dir())
            except Exception:
                session_dir_str = ""

            # Temp directories are always cleaned. If user wants audio preserved,
            # we store it next to the transcript in the output folder.
            try:
                if Config.INPUT_TMP_DIR.exists():
                    shutil.rmtree(Config.INPUT_TMP_DIR, ignore_errors=True)
            except Exception:
                pass

            try:
                FileManager.rollback_session_if_empty()
            except Exception:
                pass
            finally:
                FileManager.end_session()

            try:
                was_cancelled = bool(self._token.is_cancelled or self._cancel_evt.is_set())
                self.session_done.emit(session_dir_str, bool(processed_any), bool(self._had_errors), was_cancelled)
            except Exception:
                pass

            if not processed_any and self._is_cancelled():
                self.progress.emit(0)
            else:
                self.progress.emit(100 if processed_any else 0)

            self.finished.emit()

    # ----- Output dir / conflicts -----

    def _resolve_output_dir(self, stem: str) -> Optional[Path]:
        existing_str = ConflictService.existing_dir(stem)
        existing = Path(existing_str) if existing_str else None

        if existing is None:
            return FileManager.ensure_output(stem)

        if self._conflict_apply_all:
            action = (self._conflict_action or "skip").strip().lower()
            if action == "overwrite":
                return existing
            if action == "skip":
                return None
            if action == "new":
                new_stem = sanitize_filename(self._conflict_new_stem) or f"{stem} (2)"
                return FileManager.ensure_output(new_stem)
            return None

        self._conflict_action = None
        self._conflict_new_stem = ""
        self._conflict_event.clear()

        self.conflict_check.emit(stem, str(existing))
        self._conflict_event.wait()
        if self._is_cancelled():
            return None

        action = (self._conflict_action or "skip").strip().lower()
        if action == "skip":
            return None
        if action == "overwrite":
            return existing
        if action == "new":
            new_stem = sanitize_filename(self._conflict_new_stem) or f"{stem} (2)"
            return FileManager.ensure_output(new_stem)
        return None

    # ----- Entry materialization -----

    def _materialize_entry(self, entry: GUIEntry) -> List[WorkItem]:
        self._ensure_not_cancelled()

        if isinstance(entry, str):
            raw = entry.strip()
            if not raw:
                return []
            if is_url(raw):
                return self._materialize_url(raw, meta={})
            return self._materialize_path(Path(raw), meta={})

        if isinstance(entry, dict):
            url = (entry.get("url") or entry.get("link") or "").strip()
            path_val = entry.get("path") or entry.get("file_path")
            title = str(entry.get("title") or entry.get("name") or "").strip()
            audio_lang = entry.get("audio_lang") or entry.get("lang") or None

            meta = {"title": title, "audio_lang": audio_lang}

            if url:
                return self._materialize_url(url, meta=meta)

            if path_val:
                return self._materialize_path(Path(str(path_val)), meta=meta)

            raw = str(entry.get("value") or "").strip()
            if raw:
                if is_url(raw):
                    return self._materialize_url(raw, meta=meta)
                return self._materialize_path(Path(raw), meta=meta)

            return []

        return []

    def _materialize_url(self, url: str, *, meta: Dict[str, Any]) -> List[WorkItem]:
        self._ensure_not_cancelled()

        key = url
        self.item_status.emit(key, tr("status.prep"))

        audio_lang = meta.get("audio_lang")
        title = meta.get("title") or None

        def _log(msg: str) -> None:
            self.log.emit(str(msg))

        def _cancel_check() -> bool:
            return self._is_cancelled()

        try:
            info = self._download.probe(url, log=_log)
        except DownloadError as ex:
            self._had_errors = True
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr(ex.key, **getattr(ex, "params", {})))
            return []

        if self._is_cancelled():
            raise _Cancelled()

        title_probe = str(info.get("title") or "").strip()
        if not title:
            title = title_probe or "download"

        safe_stem = sanitize_filename(title) or "download"

        kind = "audio" if bool(Config.transcription_settings().get("download_audio_only", True)) else "video"
        self.item_status.emit(key, tr("status.proc"))

        def _progress_hook(d: Dict[str, Any]) -> None:
            if self._is_cancelled():
                raise DownloadCancelled()

            st = d.get("status")
            if st == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes") or 0
                if total:
                    pct = int(downloaded * 100 / total)
                    pct = max(0, min(100, pct))
                    self.item_progress.emit(key, pct)
            elif st == "finished":
                self.item_progress.emit(key, 100)

        try:
            out_path = self._download.download(
                url=url,
                kind=kind,
                quality="auto",
                ext="m4a" if kind == "audio" else "mp4",
                out_dir=FileManager.url_tmp_dir(),
                progress_cb=_progress_hook,
                log=_log,
                audio_lang=audio_lang,
                file_stem=safe_stem,
                cancel_check=_cancel_check,
            )
        except DownloadCancelled:
            raise _Cancelled()
        except DownloadError as ex:
            self._had_errors = True
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr(ex.key, **getattr(ex, "params", {})))
            return []
        except Exception as e:
            self._had_errors = True
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr("error.down.download_failed", detail=str(e)))
            return []

        if not out_path:
            self._had_errors = True
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr("error.down.no_output_file"))
            return []

        self._downloaded.add(out_path)
        new_key = str(out_path)
        self.item_path_update.emit(key, new_key)
        return [(new_key, out_path, safe_stem)]

    def _materialize_path(self, path: Path, *, meta: Dict[str, Any]) -> List[WorkItem]:
        self._ensure_not_cancelled()

        p = Path(path)
        key = str(p)
        if not p.exists():
            self._had_errors = True
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr("log.path_not_found", path=str(p)))
            return []

        title = str(meta.get("title") or "").strip()
        forced_stem = sanitize_filename(title) if title else None

        self.item_status.emit(key, tr("status.prep"))
        self.item_progress.emit(key, 0)
        return [(key, p, forced_stem)]

    # ----- Transcription model -----

    def _transcribe_wav(
        self,
        *,
        key: str,
        wav_path: Path,
        task: str,
        chunk_len_s: int,
        stride_len_s: int,
        default_lang: Optional[str],
        return_ts_base: bool,
        ignore_warn: bool,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        segments: List[Dict[str, Any]] = []
        merged_text = ""

        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()

            if n_channels != 1:
                raise RuntimeError(f"expected-mono; got {n_channels}")

            duration_s = n_frames / float(sr) if sr else 0.0

            chunk_len_s = max(1, int(chunk_len_s))
            stride_len_s = max(0, int(stride_len_s))
            step_s = max(1, chunk_len_s - stride_len_s)

            chunk_len_frames = int(chunk_len_s * sr)
            step_frames = int(step_s * sr)

            start_frame = 0
            while start_frame < n_frames:
                self._ensure_not_cancelled()

                wf.setpos(start_frame)
                to_read = min(chunk_len_frames, n_frames - start_frame)
                raw = wf.readframes(to_read)

                audio = self._pcm_bytes_to_float32(raw, sampwidth=sampwidth)
                if audio.size == 0:
                    break

                offset_s = start_frame / float(sr) if sr else 0.0
                chunk_seconds = (float(audio.shape[0]) / float(sr)) if sr else 0.0

                generate_kwargs: Dict[str, Any] = {"task": task}
                if default_lang:
                    generate_kwargs["language"] = default_lang

                # transformers long-form generation may REQUIRE return_timestamps=True for >~30s input
                return_ts_effective = bool(return_ts_base or (chunk_seconds > 30.0))

                result = self._call_pipe_safe(
                    audio,
                    sr,
                    return_timestamps=return_ts_effective,
                    generate_kwargs=generate_kwargs,
                    ignore_warning=ignore_warn,
                )

                text = str(result.get("text", "") or "")
                merged_text = self._merge_text(merged_text, text)

                segments.extend(self._extract_segments(result, offset_s=offset_s))

                # Per-item progress
                done_s = min(duration_s, offset_s + chunk_len_s)
                pct_item = int((done_s * 100.0) / duration_s) if duration_s > 0 else 100
                pct_item = max(0, min(100, pct_item))
                self.item_progress.emit(key, pct_item)

                # Global progress by chunks
                self._done_chunks += 1
                self._bump_global_progress()

                if start_frame + chunk_len_frames >= n_frames:
                    break
                start_frame += step_frames

        return merged_text, segments

    # ----- Pipeline helpers -----

    @staticmethod
    def _pcm_bytes_to_float32(raw: bytes, *, sampwidth: int) -> np.ndarray:
        if not raw:
            return np.array([], dtype=np.float32)

        if sampwidth == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            return data / 32768.0

        if sampwidth == 4:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32)
            return data / 2147483648.0

        return np.array([], dtype=np.float32)

    @staticmethod
    def _is_longform_timestamp_requirement(err: Exception) -> bool:
        msg = str(err).lower()
        return (
            "long-form generation" in msg
            and "return_timestamps=true" in msg
            and "timestamp tokens" in msg
        )

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

        if self._pipe is None:
            raise _FatalPipeError("pipe-not-ready")

        payload = {"array": audio, "sampling_rate": sr}

        try:
            try:
                result = self._pipe(
                    payload,
                    return_timestamps=return_timestamps,
                    generate_kwargs=generate_kwargs,
                )
            except TypeError:
                result = self._pipe(payload, generate_kwargs=generate_kwargs)

            if isinstance(result, dict):
                return result
            return {"text": str(result)}

        except Exception as e:
            # Known transformers behavior: long-form requires timestamps even if output doesn't.
            if self._is_longform_timestamp_requirement(e) and not return_timestamps:
                try:
                    result = self._pipe(
                        payload,
                        return_timestamps=True,
                        generate_kwargs=generate_kwargs,
                    )
                    if isinstance(result, dict):
                        return result
                    return {"text": str(result)}
                except Exception as e2:
                    raise _FatalPipeError(str(e2)) from e2

            # Do not silently swallow pipeline errors (prevents false "Done").
            if ignore_warning:
                raise _FatalPipeError(str(e)) from e
            raise

    # ----- Result processing -----

    @staticmethod
    def _merge_text(prev: str, nxt: str) -> str:
        p = (prev or "").strip()
        n = (nxt or "").strip()
        if not p:
            return n
        if not n:
            return p
        if p.endswith((" ", "\n")):
            return p + n
        return p + " " + n

    @staticmethod
    def _extract_segments(result: Dict[str, Any], *, offset_s: float) -> List[Dict[str, Any]]:
        chunks = result.get("chunks") or []
        segments: List[Dict[str, Any]] = []

        for ch in chunks:
            ts = ch.get("timestamp")
            if not ts or not isinstance(ts, (tuple, list)) or len(ts) != 2:
                continue
            s, e = ts
            try:
                s_f = float(s) + float(offset_s)
                e_f = float(e) + float(offset_s)
            except Exception:
                continue

            text = str(ch.get("text", "") or "")
            segments.append({"start": max(0.0, s_f), "end": max(0.0, e_f), "text": text})

        return segments

    def _write_outputs(
        self,
        *,
        key: str,
        stem: str,
        out_dir: Path,
        merged_text: str,
        segments: List[Dict[str, Any]],
        out_ext: str,
        timestamps_output: bool,
    ) -> Optional[Path]:
        self._ensure_not_cancelled()

        out_text = self._render_transcript(
            merged_text=merged_text,
            segments=segments,
            out_ext=out_ext,
            timestamps_output=timestamps_output,
        )

        base_name = tr("files.transcript.default_name")
        base_name = sanitize_filename(str(base_name)) or "Transcript"
        out_path = FileManager.transcript_path(stem, base_name=base_name)

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_text(out_text, encoding="utf-8")
        except Exception as e:
            self._had_errors = True
            self.log.emit(tr("log.transcript.save_failed", name=stem, detail=str(e)))
            self.item_status.emit(key, tr("status.error"))
            return None

        self.transcript_ready.emit(key, str(out_path))
        return out_path

    @staticmethod
    def _render_transcript(
        *,
        merged_text: str,
        segments: List[Dict[str, Any]],
        out_ext: str,
        timestamps_output: bool,
    ) -> str:
        out_ext = (out_ext or "txt").lower().strip().lstrip(".") or "txt"
        if out_ext not in ("txt", "srt", "sub"):
            out_ext = "txt"

        if out_ext == "srt":
            return TextPostprocessor.to_srt(segments)
        if out_ext == "txt" and timestamps_output:
            return TextPostprocessor.to_timestamped_plain(segments)

        merged = TextPostprocessor.clean(merged_text)
        if merged:
            return merged
        return TextPostprocessor.to_plain(segments)

    # ----- WAV persistence -----

    def _persist_wav_asset(self, *, stem: str, out_dir: Path, wav_path: Path, tmp_wav: Optional[Path]) -> None:
        """Save a WAV file next to the transcript inside the item's output folder."""
        target = FileManager.audio_wav_path(stem)

        try:
            if target.exists():
                target.unlink(missing_ok=True)  # type: ignore[call-arg]
        except Exception:
            pass

        # Prefer moving a temp WAV produced by extraction to avoid duplicating disk usage.
        if tmp_wav is not None and tmp_wav.exists():
            try:
                shutil.move(str(tmp_wav), str(target))
                return
            except Exception:
                # Fallback to copy.
                pass

        if wav_path.exists():
            shutil.copy2(str(wav_path), str(target))

    # ----- Progress helpers -----

    @staticmethod
    def _estimate_chunks(total_dur_s: float, chunk_len: int, stride_len: int) -> int:
        if total_dur_s <= 0:
            return 1
        step = max(1, int(chunk_len) - int(stride_len))
        return max(1, int(math.ceil(total_dur_s / float(step))))

    def _bump_global_progress(self) -> None:
        if self._total_chunks <= 0:
            self.progress.emit(0)
            return
        pct = int((self._done_chunks * 100.0) / float(self._total_chunks))
        pct = max(0, min(100, pct))
        self.progress.emit(pct)
