# ui/workers/transcription_worker.py
from __future__ import annotations

import math
import shutil
import wave
import threading
from pathlib import Path
from typing import List, Optional, Tuple, Union, Dict, Any, Set

from PyQt5 import QtCore

import numpy as np

from core.config.app_config import AppConfig as Config
from core.io.file_manager import FileManager
from core.io.audio_extractor import AudioExtractor
from core.services.download_service import DownloadService, DownloadError, DownloadCancelled
from core.services.conflict_service import ConflictService
from core.io.text import is_url, sanitize_filename, TextPostprocessor
from ui.utils.translating import tr

GUIEntry = Union[str, Dict[str, Any]]
WorkItem = Tuple[str, Path, Optional[str]]


class _Cancelled(RuntimeError):
    pass


class TranscriptionWorker(QtCore.QObject):
    """
    Processes a list of entries (local files or URLs), prepares model input,
    runs the ASR pipeline using settings.json, and saves transcripts.
    """

    # ----- Signals -----

    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal()

    conflict_check = QtCore.pyqtSignal(str, str)  # stem, existing_dir

    item_status = QtCore.pyqtSignal(str, str)       # key, status label
    item_progress = QtCore.pyqtSignal(str, int)     # key, percent
    item_path_update = QtCore.pyqtSignal(str, str)  # old_key, new_local_path
    transcript_ready = QtCore.pyqtSignal(str, str)  # key, transcript_path

    # ----- Lifecycle -----

    def __init__(self, files: Optional[List[Path]] = None, pipe=None, entries: Optional[List[GUIEntry]] = None) -> None:
        super().__init__()
        self._cancel = threading.Event()
        self._pipe = pipe
        self._raw_entries: List[GUIEntry] = list(entries or [])
        self._download = DownloadService()
        self._downloaded: Set[Path] = set()

        # Conflict dialog rendezvous
        self._conflict_event = threading.Event()
        self._conflict_action: Optional[str] = None  # "skip" | "overwrite" | "new"
        self._conflict_new_stem: str = ""
        self._conflict_apply_all: bool = False

        # Chunk-based progress tracking
        self._total_chunks: int = 0
        self._done_chunks: int = 0

    # ----- Cancellation -----

    def cancel(self) -> None:
        """Request best-effort cancellation of the current run."""
        self._cancel.set()
        try:
            self._conflict_event.set()
        except Exception:
            pass

    def _is_cancelled(self) -> bool:
        """Single cancellation gate: internal flag or QThread interruption."""
        if self._cancel.is_set():
            return True
        try:
            return bool(QtCore.QThread.currentThread().isInterruptionRequested())
        except Exception:
            return False

    def _ensure_not_cancelled(self) -> None:
        if self._is_cancelled():
            raise _Cancelled()

    # ----- Progress helpers -----

    @staticmethod
    def _estimate_chunks(duration_s: float | None, chunk_len_s: int, stride_len_s: int) -> int:
        """
        Estimate number of chunks based on duration.
        This is used ONLY for global progress calibration.
        """
        if duration_s is None or duration_s <= 0:
            return 1

        chunk_len_s = max(1, int(chunk_len_s))
        stride_len_s = max(0, int(stride_len_s))
        step_s = max(1, chunk_len_s - stride_len_s)

        if duration_s <= chunk_len_s:
            return 1

        remaining = max(0.0, duration_s - chunk_len_s)
        return int(math.ceil(remaining / step_s)) + 1

    def _bump_global_progress(self) -> None:
        if self._total_chunks <= 0:
            return
        pct = int(self._done_chunks * 100 / self._total_chunks)
        pct = max(0, min(100, pct))
        self.progress.emit(pct)

    # ----- Main entrypoint -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        processed_any = False

        trans_cfg = Config.transcription_settings()
        keep_downloaded_files: bool = bool(trans_cfg.get("keep_downloaded_files", True))
        keep_wav_temp: bool = bool(trans_cfg.get("keep_wav_temp", False))

        out_ext = str(Config.transcript_default_ext() or "txt").lower().strip().lstrip(".") or "txt"
        timestamps_output: bool = bool(trans_cfg.get("timestamps_output", False))
        want_timestamped_output = bool(timestamps_output or out_ext == "srt")

        try:
            self.log.emit(tr("log.start"))

            # ----- Prepare temp area -----
            try:
                if Config.INPUT_TMP_DIR.exists():
                    shutil.rmtree(Config.INPUT_TMP_DIR, ignore_errors=True)
                Config.INPUT_TMP_DIR.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.log.emit(tr("log.temp_init_failed", detail=str(e)))

            # ----- Plan session (lazy creation on first write) -----
            try:
                planned = FileManager.plan_session()
                self.log.emit(tr("log.session.plan", path=str(planned)))
            except Exception as e:
                self.log.emit(tr("log.session_plan_failed", detail=str(e)))

            # ----- Build work list -----
            work_items: List[WorkItem] = []
            for entry in self._raw_entries:
                if self._is_cancelled():
                    break
                try:
                    items = self._materialize_entry(entry)
                    work_items.extend(items)
                except _Cancelled:
                    break
                except Exception as e:
                    self.log.emit(tr("log.entry_prep_error", entry=str(entry), detail=str(e)))

            if not work_items:
                if self._is_cancelled():
                    self.log.emit(tr("log.cancelled"))
                else:
                    self.log.emit(tr("log.no_items"))
                self.progress.emit(0)
                return

            # ----- Model settings -----
            model_cfg = Config.model_settings()
            chunk_len = int(model_cfg.get("chunk_length_s", 30))
            stride_len = int(model_cfg.get("stride_length_s", 5))

            return_ts_model = bool(model_cfg.get("return_timestamps", True))
            return_ts = bool(return_ts_model or want_timestamped_output)

            ignore_warn = bool(model_cfg.get("ignore_warning", True))
            task = "transcribe"
            default_lang = model_cfg.get("default_language")

            # Prevent long-form requirement when timestamps are disabled
            if not return_ts and chunk_len > 29:
                chunk_len = 29

            # ----- Calibrate global progress by estimating chunk count -----
            self._done_chunks = 0
            self._total_chunks = 0
            for _key, path, _forced_stem in work_items:
                try:
                    dur = AudioExtractor.probe_duration(path)
                except Exception:
                    dur = None
                self._total_chunks += self._estimate_chunks(dur, chunk_len, stride_len)

            self._total_chunks = max(1, int(self._total_chunks))
            self._bump_global_progress()

            # ----- Process items -----
            for key, path, forced_stem in work_items:
                if self._is_cancelled():
                    break

                stem = sanitize_filename(forced_stem) if forced_stem else sanitize_filename(path.stem)

                self.item_status.emit(key, tr("status.prep"))
                self.item_progress.emit(key, 0)

                # ----- Resolve output folder conflicts -----
                out_dir: Optional[Path] = None
                existing_str = ConflictService.existing_dir(stem)
                existing = Path(existing_str) if existing_str else None
                if existing is not None:
                    if not self._conflict_apply_all:
                        self._conflict_action = None
                        self._conflict_new_stem = ""
                        self._conflict_event.clear()
                        self.conflict_check.emit(stem, str(existing))
                        self._conflict_event.wait()
                        if self._is_cancelled():
                            break
                    action = (self._conflict_action or "skip").strip().lower()

                    if action == "skip":
                        self.item_status.emit(key, tr("status.skipped"))
                        continue
                    if action == "overwrite":
                        out_dir = existing
                    elif action == "new":
                        new_stem = sanitize_filename(self._conflict_new_stem) or f"{stem} (2)"
                        out_dir = FileManager.ensure_output(new_stem)
                        stem = new_stem
                    else:
                        self.item_status.emit(key, tr("status.skipped"))
                        continue
                else:
                    out_dir = FileManager.ensure_output(stem)

                if not out_dir:
                    self.item_status.emit(key, tr("status.error"))
                    continue

                self.item_status.emit(key, tr("status.proc"))

                # ----- Prepare temp WAV -----
                tmp_wav: Path | None = None
                try:
                    wav_path = FileManager.ensure_tmp_wav(
                        path,
                        log=lambda m: self.log.emit(str(m)),
                        cancel_check=self._is_cancelled,
                    )
                    tmp_wav = wav_path if wav_path != path else None
                except Exception as e:
                    self.log.emit(tr("log.audio_prep_failed", name=str(path.name), detail=str(e)))
                    self.item_status.emit(key, tr("status.error"))
                    continue

                if self._is_cancelled():
                    break

                # ----- Transcribe (chunked) -----
                try:
                    with wave.open(str(wav_path), "rb") as wf:
                        sr = wf.getframerate()
                        n_channels = wf.getnchannels()
                        sampwidth = wf.getsampwidth()
                        n_frames = wf.getnframes()

                        if n_channels != 1:
                            raise RuntimeError(f"expected-mono; got {n_channels}")

                        duration_s = n_frames / float(sr) if sr else 0.0

                        chunk_len_s = max(1, int(chunk_len))
                        stride_len_s = max(0, int(stride_len))
                        step_s = max(1, chunk_len_s - stride_len_s)

                        chunk_len_frames = int(chunk_len_s * sr)
                        step_frames = int(step_s * sr)

                        segments: List[Dict[str, Any]] = []
                        merged_text = ""

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

                            generate_kwargs: Dict[str, Any] = {"task": task}
                            if default_lang:
                                generate_kwargs["language"] = default_lang

                            result = self._call_pipe_safe(
                                audio,
                                sr,
                                return_timestamps=return_ts,
                                generate_kwargs=generate_kwargs,
                                ignore_warning=ignore_warn,
                            )

                            text = str(result.get("text", "") or "")
                            merged_text = self._merge_text(merged_text, text)

                            chunk_segments = self._extract_segments(result, offset_s=offset_s)
                            segments.extend(chunk_segments)

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

                        # ----- Write outputs -----
                        self._write_outputs(
                            key=key,
                            stem=stem,
                            out_dir=out_dir,
                            merged_text=merged_text,
                            segments=segments,
                            out_ext=out_ext,
                            timestamps_output=timestamps_output,
                        )

                except _Cancelled:
                    break
                except Exception as e:
                    self.log.emit(tr("log.transcription_failed", name=str(path.name), detail=str(e)))
                    self.item_status.emit(key, tr("status.error"))
                    continue
                finally:
                    # ----- Cleanup temp WAV -----
                    if tmp_wav is not None and tmp_wav.exists() and not keep_wav_temp:
                        try:
                            tmp_wav.unlink(missing_ok=True)  # type: ignore[call-arg]
                        except Exception:
                            pass

                # ----- Mark done -----
                self.item_progress.emit(key, 100)
                self.item_status.emit(key, tr("status.done"))
                processed_any = True

                # ----- Cleanup downloaded file -----
                if path in self._downloaded:
                    if keep_downloaded_files:
                        try:
                            final_path = FileManager.move_to_downloads(path, desired_stem=stem)
                            if final_path != path:
                                self.item_path_update.emit(str(path), str(final_path))
                                self._downloaded.discard(path)
                                self._downloaded.add(final_path)
                                path = final_path
                        except Exception:
                            pass
                    else:
                        try:
                            path.unlink(missing_ok=True)  # type: ignore[call-arg]
                        except Exception:
                            pass

            if self._is_cancelled():
                self.log.emit(tr("log.cancelled"))
            elif processed_any:
                self.log.emit(tr("log.done"))

        except Exception as e:
            self.log.emit(tr("log.worker_error", detail=str(e)))

        finally:
            # ----- Cleanup temp dir -----
            try:
                if Config.INPUT_TMP_DIR.exists():
                    shutil.rmtree(Config.INPUT_TMP_DIR, ignore_errors=True)
                    self.log.emit(tr("log.tmp.cleaned"))
            except Exception as e:
                self.log.emit(tr("log.temp_cleanup_issue", detail=str(e)))

            try:
                FileManager.rollback_session_if_empty()
            except Exception:
                pass
            finally:
                FileManager.end_session()

            if not processed_any and self._is_cancelled():
                self.progress.emit(0)
            else:
                self.progress.emit(100 if processed_any else 0)

            self.finished.emit()

    # ----- Entry materialization -----

    def _materialize_entry(self, entry: GUIEntry) -> List[WorkItem]:
        """
        Convert a GUI entry into a list of concrete work items.

        Entry formats:
          - str: treated as path or URL
          - dict: expects keys used by panels (url/path, title, audio_lang, etc.)
        """
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
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr(ex.key, **getattr(ex, "params", {})))
            return []

        if self._is_cancelled():
            raise _Cancelled()

        title_probe = str(info.get("title") or "").strip()
        if not title:
            title = title_probe or "download"

        safe_stem = sanitize_filename(title) or "download"

        trans_cfg = Config.transcription_settings()
        download_audio_only = bool(trans_cfg.get("download_audio_only", True))

        kind = "audio" if download_audio_only else "video"

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
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr(ex.key, **getattr(ex, "params", {})))
            return []
        except Exception as e:
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr("error.down.download_failed", detail=str(e)))
            return []

        if not out_path:
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
            self.item_status.emit(key, tr("status.error"))
            self.log.emit(tr("log.path_not_found", path=str(p)))
            return []

        title = str(meta.get("title") or "").strip()
        forced_stem = sanitize_filename(title) if title else None

        self.item_status.emit(key, tr("status.prep"))
        self.item_progress.emit(key, 0)
        return [(key, p, forced_stem)]

    # ----- Pipeline helpers -----

    @staticmethod
    def _pcm_bytes_to_float32(raw: bytes, *, sampwidth: int) -> np.ndarray:
        """
        Convert PCM bytes read from wave into float32 mono array in [-1,1].

        Supports 16-bit and 32-bit PCM.
        """
        if not raw:
            return np.array([], dtype=np.float32)

        if sampwidth == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            return data / 32768.0

        if sampwidth == 4:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32)
            return data / 2147483648.0

        return np.array([], dtype=np.float32)

    def _call_pipe_safe(
        self,
        audio: np.ndarray,
        sr: int,
        *,
        return_timestamps: bool,
        generate_kwargs: Dict[str, Any],
        ignore_warning: bool,
    ) -> Dict[str, Any]:
        """
        Call transformers pipeline with defensive error handling.
        """
        self._ensure_not_cancelled()

        if self._pipe is None:
            raise RuntimeError("pipe-not-ready")

        try:
            payload = {"array": audio, "sampling_rate": sr}

            try:
                result = self._pipe(
                    payload,
                    return_timestamps=return_timestamps,
                    generate_kwargs=generate_kwargs,
                )
            except TypeError:
                # Some pipeline versions don't accept all kwargs (e.g. return_timestamps)
                result = self._pipe(payload, generate_kwargs=generate_kwargs)
            if isinstance(result, dict):
                return result
            return {"text": str(result)}
        except Exception as e:
            if ignore_warning:
                try:
                    self.log.emit(tr("log.transcription_failed", name="ASR pipeline", detail=str(e)))
                except Exception:
                    pass
                return {"text": "", "chunks": []}
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
        """
        Normalize segments from pipeline output.
        """
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
    ) -> None:
        """
        Write transcript output to target file and emit UI notifications.
        """
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
            self.log.emit(tr("log.transcript.save_failed", name=stem, detail=str(e)))
            self.item_status.emit(key, tr("status.error"))
            return

        self.transcript_ready.emit(key, str(out_path))
        # UI renders a clickable link once transcript_ready is received.

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

    # ----- Conflict callbacks from UI -----

    @QtCore.pyqtSlot(str, str, bool)
    def set_conflict_action(self, action: str, new_name: str = "", apply_all: bool = False) -> None:
        """
        Called by UI to resolve output folder naming conflicts.
        """
        self._conflict_action = str(action or "").strip().lower()
        self._conflict_new_stem = str(new_name or "").strip()
        self._conflict_apply_all = bool(apply_all)
        try:
            self._conflict_event.set()
        except Exception:
            pass