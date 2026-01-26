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

    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal()

    conflict_check = QtCore.pyqtSignal(str, str)      # stem, existing_dir

    item_status = QtCore.pyqtSignal(str, str)         # key, status label
    item_progress = QtCore.pyqtSignal(str, int)       # key, percent
    item_path_update = QtCore.pyqtSignal(str, str)    # old_key, new_local_path
    transcript_ready = QtCore.pyqtSignal(str, str)    # key, transcript_path

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

        # Chunk-based progress tracking
        self._total_chunks: int = 0
        self._done_chunks: int = 0

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

    @QtCore.pyqtSlot()
    def run(self) -> None:
        processed_any = False

        trans_cfg = Config.transcription_settings()
        keep_downloaded_files: bool = bool(trans_cfg.get("keep_downloaded_files", True))
        keep_wav_temp: bool = bool(trans_cfg.get("keep_wav_temp", False))

        out_ext = str(trans_cfg.get("output_ext", "txt")).lower().strip().lstrip(".") or "txt"
        timestamps_output: bool = bool(trans_cfg.get("timestamps_output", False))
        want_timestamped_output = bool(timestamps_output or out_ext == "srt")

        try:
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

            model_cfg = Config.model_settings()
            chunk_len = int(model_cfg.get("chunk_length_s", 30))
            stride_len = int(model_cfg.get("stride_length_s", 5))
            return_ts_model = bool(model_cfg.get("return_timestamps", True))
            # Output format may require timestamps even if the model setting disables them.
            return_ts = bool(return_ts_model or want_timestamped_output)
            ignore_warn = bool(model_cfg.get("ignore_warning", True))
            task = "transcribe"
            default_lang = model_cfg.get("default_language")

            # Prevent long-form requirements when user disabled timestamps:
            # Whisper long-form triggers at > ~30s and requires return_timestamps=True.
            if not return_ts and chunk_len > 29:
                chunk_len = 29

            total_files = len(work_items)

            # ----- Calibrate global progress by estimating chunk count -----
            # We estimate based on ffprobe duration, before WAV conversion.
            self._done_chunks = 0
            self._total_chunks = 0
            for key, path, _forced_stem in work_items:
                try:
                    dur = AudioExtractor.probe_duration(path)
                except Exception:
                    dur = None
                self._total_chunks += self._estimate_chunks(dur, chunk_len, stride_len)

            # Fallback: never let it be zero
            self._total_chunks = max(1, int(self._total_chunks))
            self._bump_global_progress()

            for file_idx, (key, path, forced_stem) in enumerate(work_items, start=1):
                if self._is_cancelled():
                    break

                out_dir = None
                write_into_existing = False

                stem = sanitize_filename(forced_stem) if forced_stem else sanitize_filename(path.stem)

                existing_str = ConflictService.existing_dir(stem)
                existing = Path(existing_str) if existing_str else None
                if existing is not None:
                    out_dir = existing
                    write_into_existing = True

                if out_dir is None:
                    out_dir = FileManager.output_dir_for(stem)

                if existing is not None:
                    try:
                        self._conflict_event.clear()
                        self.conflict_check.emit(stem, str(existing))
                        self._wait_for_conflict_decision()
                    except _Cancelled:
                        raise
                    except Exception as e:
                        self.log.emit(tr("log.conflict_dialog_error", detail=str(e)))
                        self._set_conflict_decision("skip", "")

                    if self._conflict_action == "skip":
                        self.item_status.emit(key, tr("status.skipped"))
                        self.item_progress.emit(key, 0)
                        # Move progress at least slightly for skipped items
                        self._done_chunks += 1
                        self._bump_global_progress()
                        continue
                    elif self._conflict_action == "new":
                        if self._conflict_new_stem:
                            stem = sanitize_filename(self._conflict_new_stem)
                            out_dir = FileManager.output_dir_for(stem)
                    elif self._conflict_action == "overwrite":
                        out_dir = existing
                        write_into_existing = True

                # ----- Prepare model input (force WAV for cancellable chunking) -----
                try:
                    if self._is_cancelled():
                        break
                    self.item_status.emit(key, tr("status.prep"))
                    self.item_progress.emit(key, 0)

                    model_input = FileManager.ensure_tmp_wav(
                        path,
                        cancel_check=self._is_cancelled,
                    )
                except Exception as e:
                    if self._is_cancelled():
                        raise _Cancelled()
                    self.item_status.emit(key, tr("status.error"))
                    self.item_progress.emit(key, 0)
                    self.log.emit(tr("log.audio_prep_failed", name=path.name, detail=str(e)))
                    continue

                if self._is_cancelled():
                    break

                # ----- Run ASR (chunked, cancellable) -----
                self.item_status.emit(key, tr("status.proc"))
                try:
                    generate_kwargs: Dict[str, Any] = {"task": task}
                    if default_lang:
                        generate_kwargs["language"] = default_lang

                    merged_text, segments = self._transcribe_wav_chunked(
                        item_key=key,
                        wav_path=model_input,
                        chunk_length_s=chunk_len,
                        stride_length_s=stride_len,
                        return_timestamps=return_ts,
                        collect_segments=want_timestamped_output,
                        generate_kwargs=generate_kwargs,
                        ignore_warning=ignore_warn,
                    )

                    text = self._render_transcript(
                        merged_text=merged_text,
                        segments=segments,
                        out_ext=out_ext,
                        timestamps_output=timestamps_output,
                    )

                except _Cancelled:
                    raise
                except Exception as e:
                    self.item_status.emit(key, tr("status.error"))
                    self.item_progress.emit(key, 0)
                    self.log.emit(tr("log.transcription_failed", name=path.name, detail=str(e)))
                    continue

                # ----- Ensure session directory exists -----
                try:
                    if not write_into_existing:
                        FileManager.ensure_session()
                except Exception as e:
                    self.item_status.emit(key, tr("status.error"))
                    self.item_progress.emit(key, 0)
                    self.log.emit(tr("log.session_dir_failed", detail=str(e)))
                    continue

                # ----- Save transcript -----
                created_dir = False
                try:
                    if not out_dir.exists():
                        out_dir.mkdir(parents=True, exist_ok=True)
                        created_dir = True

                    base_name = tr("files.transcript.default_name")
                    out_txt = FileManager.transcript_path(stem, base_name=base_name)
                    out_txt.write_text(text, encoding="utf-8")

                    self.item_status.emit(key, tr("status.done"))
                    self.item_progress.emit(key, 100)
                    self.transcript_ready.emit(key, str(out_txt))
                    processed_any = True

                except Exception as e:
                    self.item_status.emit(key, tr("status.error"))
                    self.item_progress.emit(key, 0)
                    self.log.emit(tr("log.transcript.save_failed", name=path.name, detail=str(e)))
                    if created_dir:
                        FileManager.remove_dir_if_empty(out_dir)

                # ----- Optional cleanup of downloaded originals -----
                try:
                    if (not keep_downloaded_files) and (path in self._downloaded):
                        path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass

                # In case the file was tiny (1 chunk), ensure global progress moves
                if file_idx == total_files:
                    self.progress.emit(100)

        except _Cancelled:
            pass
        except Exception as e:
            self.log.emit(tr("log.worker_error", detail=str(e)))
        finally:
            try:
                if (not keep_wav_temp) and Config.INPUT_TMP_DIR.exists():
                    shutil.rmtree(Config.INPUT_TMP_DIR, ignore_errors=True)
                    self.log.emit(tr("log.tmp.cleaned"))
            except Exception as e:
                self.log.emit(tr("log.temp_cleanup_issue", detail=str(e)))

            if not processed_any:
                FileManager.rollback_session_if_empty()

            FileManager.end_session()
            self.finished.emit()

    # ----- Conflict decision rendezvous -----

    @QtCore.pyqtSlot(str, str)
    def on_conflict_decided(self, action: str, new_stem: str = "") -> None:
        self._set_conflict_decision(action, new_stem)

    def _set_conflict_decision(self, action: str, new_stem: str) -> None:
        self._conflict_action = action
        self._conflict_new_stem = new_stem
        self._conflict_event.set()

    # ----- Cancellation / waiting helpers -----

    def _ensure_not_cancelled(self) -> None:
        if self._is_cancelled():
            raise _Cancelled()

    def _wait_for_conflict_decision(self) -> None:
        """Wait for GUI conflict decision, but stay cancellable."""
        while True:
            self._ensure_not_cancelled()
            if self._conflict_event.wait(timeout=0.05):
                return

    # ----- ASR helpers -----

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

    @staticmethod
    def _shift_segments(segments: List[Dict[str, Any]], offset_s: float) -> List[Dict[str, Any]]:
        if not segments:
            return []
        out: List[Dict[str, Any]] = []
        for seg in segments:
            try:
                start = float(seg.get("start", 0.0) or 0.0) + float(offset_s)
            except Exception:
                start = float(offset_s)
            try:
                end = float(seg.get("end", start) or start) + float(offset_s)
            except Exception:
                end = start

            out.append({"start": start, "end": end, "text": seg.get("text", "")})
        return out

    @staticmethod
    def _render_transcript(
        *,
        merged_text: str,
        segments: List[Dict[str, Any]],
        out_ext: str,
        timestamps_output: bool,
    ) -> str:
        out_ext = (out_ext or "txt").lower().strip().lstrip(".") or "txt"

        if out_ext == "srt":
            return TextPostprocessor.to_srt(segments)
        if out_ext == "txt" and timestamps_output:
            return TextPostprocessor.to_timestamped_plain(segments)

        # Keep the legacy continuous output for plain TXT.
        merged = TextPostprocessor.clean(merged_text)
        if merged:
            return merged
        return TextPostprocessor.to_plain(segments)

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
        Call transformers pipeline with a fallback for Whisper long-form mode.
        If the model complains about long-form requiring timestamps, retry with return_timestamps=True.
        """
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
                result = self._pipe(
                    payload,
                    return_timestamps=return_timestamps,
                    generate_kwargs=generate_kwargs,
                )
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
                        result = self._pipe(
                            payload,
                            return_timestamps=True,
                            generate_kwargs=generate_kwargs,
                        )
                    return result if isinstance(result, dict) else {"text": str(result)}
                except Exception:
                    raise e
            raise

    def _transcribe_wav_chunked(
        self,
        *,
        item_key: str,
        wav_path: Path,
        chunk_length_s: int,
        stride_length_s: int,
        return_timestamps: bool,
        collect_segments: bool,
        generate_kwargs: Dict[str, Any],
        ignore_warning: bool,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        out_text = ""
        all_segments: List[Dict[str, Any]] = []
        last_end_s = -1e9
        eps_s = 0.25

        with wave.open(str(wav_path), "rb") as wf:
            sr = int(wf.getframerate())
            n_channels = int(wf.getnchannels())
            sampwidth = int(wf.getsampwidth())
            n_frames = int(wf.getnframes())

            chunk_frames = max(1, int(chunk_length_s) * sr)
            stride_frames = max(0, int(stride_length_s) * sr)
            step = max(1, chunk_frames - stride_frames)

            # Exact chunk count for this file (for item_progress)
            if n_frames <= chunk_frames:
                chunks_in_file = 1
            else:
                chunks_in_file = int(math.ceil((n_frames - chunk_frames) / step)) + 1
            chunks_in_file = max(1, chunks_in_file)

            pos = 0
            chunk_idx = 0

            while pos < n_frames:
                self._ensure_not_cancelled()

                wf.setpos(pos)
                raw = wf.readframes(min(chunk_frames, n_frames - pos))

                if sampwidth == 2:
                    arr = np.frombuffer(raw, dtype=np.int16)
                    scale = 32768.0
                elif sampwidth == 4:
                    arr = np.frombuffer(raw, dtype=np.int32)
                    scale = float(2 ** 31)
                else:
                    arr = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
                    scale = 128.0

                if n_channels > 1 and arr.size:
                    arr = arr.reshape(-1, n_channels).mean(axis=1)

                audio = arr.astype(np.float32) / float(scale)

                result = self._call_pipe_safe(
                    audio,
                    sr,
                    return_timestamps=return_timestamps,
                    generate_kwargs=generate_kwargs,
                    ignore_warning=ignore_warning,
                )

                txt = result.get("text", "")
                txt = TextPostprocessor.clean(str(txt))
                out_text = self._merge_text(out_text, txt)

                if collect_segments:
                    # Collect segments for timestamped outputs.
                    chunk_offset_s = float(pos) / float(sr)
                    segments = TextPostprocessor.segments_from_result(result)
                    segments = self._shift_segments(segments, chunk_offset_s)

                    for seg in segments:
                        text = TextPostprocessor.clean(str(seg.get("text", "")))
                        if not text:
                            continue

                        try:
                            start = float(seg.get("start", 0.0) or 0.0)
                        except Exception:
                            start = 0.0
                        try:
                            end = float(seg.get("end", start) or start)
                        except Exception:
                            end = start

                        if start < (last_end_s - eps_s):
                            continue

                        all_segments.append({"start": start, "end": end, "text": text})
                        last_end_s = max(last_end_s, end)

                # ----- Update progress -----
                chunk_idx += 1
                local_pct = int(chunk_idx * 100 / chunks_in_file)
                local_pct = max(0, min(100, local_pct))
                self.item_progress.emit(item_key, local_pct)

                self._done_chunks += 1
                self._bump_global_progress()

                pos += step

        return out_text, all_segments

    # ----- Entry helpers -----

    def _normalize_entry(self, raw: GUIEntry) -> Tuple[str, str]:
        if isinstance(raw, dict):
            t = str(raw.get("type", "") or "").strip().lower()
            v = raw.get("value", "")
            v = str(v) if not isinstance(v, str) else v
            return v.strip(), t
        s = str(raw).strip()
        if s.startswith("[URL]"):
            return s[5:].strip(), "url"
        return s, ""

    def _materialize_entry(self, raw: GUIEntry) -> List[WorkItem]:
        s, t = self._normalize_entry(raw)

        if t == "url" or is_url(s):
            key = s
            self.item_status.emit(key, tr("status.prep"))
            self.item_progress.emit(key, 0)

            trans_cfg = Config.transcription_settings()
            download_audio_only: bool = bool(trans_cfg.get("download_audio_only", True))
            keep_downloaded_files: bool = bool(trans_cfg.get("keep_downloaded_files", True))

            try:
                meta = self._download.probe(s, log=lambda m: None)
            except DownloadError as de:
                self.item_status.emit(key, tr("status.error"))
                self.log.emit(tr(de.key, **de.params))
                return []
            except Exception as e:
                self.item_status.emit(key, tr("status.error"))
                self.log.emit(tr("error.down.probe_failed", detail=str(e)))
                return []

            if self._is_cancelled():
                return []

            title = meta.get("title") or "file"
            predicted_stem = sanitize_filename(Path(title).stem)
            existing_str = ConflictService.existing_dir(predicted_stem)
            existing = Path(existing_str) if existing_str else None
            forced_stem: Optional[str] = None

            if existing is not None:
                try:
                    self._conflict_event.clear()
                    self.conflict_check.emit(predicted_stem, str(existing))
                    self._wait_for_conflict_decision()
                except _Cancelled:
                    raise
                except Exception as e:
                    self.log.emit(tr("log.conflict_dialog_error", detail=str(e)))
                    self._set_conflict_decision("skip", "")

                if self._conflict_action == "skip":
                    self.item_status.emit(key, tr("status.skipped"))
                    self.item_progress.emit(key, 0)
                    return []
                elif self._conflict_action == "new":
                    if self._conflict_new_stem:
                        forced_stem = sanitize_filename(self._conflict_new_stem)

            kind = "audio" if download_audio_only else "video"
            ext = "m4a" if kind == "audio" else "mp4"
            out_dir = Config.DOWNLOADS_DIR if keep_downloaded_files else Config.INPUT_TMP_DIR

            try:
                local = self._download.download(
                    url=s,
                    kind=kind,
                    quality="auto",
                    ext=ext,
                    out_dir=out_dir,
                    progress_cb=lambda *_: None,
                    log=lambda *_: None,
                    cancel_check=self._is_cancelled,
                )
                if local:
                    self._downloaded.add(local)
                    self.item_path_update.emit(key, str(local))
                    new_key = str(local)
                    return [(new_key, local, forced_stem)]

                self.item_status.emit(key, tr("status.error"))
                self.log.emit(tr("error.down.download_failed", detail=tr("error.down.no_output_file")))
                return []

            except DownloadCancelled:
                raise _Cancelled()

            except DownloadError as de:
                self.item_status.emit(key, tr("status.error"))
                self.log.emit(tr(de.key, **de.params))
                return []
            except Exception as e:
                self.item_status.emit(key, tr("status.error"))
                self.log.emit(tr("error.down.download_failed", detail=str(e)))
                return []

        p = Path(s)
        if p.is_dir():
            allowed = {
                e.lower() if e.startswith(".") else f".{e.lower()}"
                for e in Config.audio_extensions()
            } | {
                e.lower() if e.startswith(".") else f".{e.lower()}"
                for e in Config.video_extensions()
            }
            files = [x for x in p.rglob("*") if x.is_file() and x.suffix.lower() in allowed]
            if not files:
                self.log.emit(tr("log.no_supported_files_in_folder", path=str(p)))
            return [(str(f), f, None) for f in sorted(files)]

        if p.is_file():
            return [(str(p), p, None)]

        self.log.emit(tr("log.path_not_found", path=str(p)))
        return []