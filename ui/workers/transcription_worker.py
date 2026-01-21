# ui/workers/transcription_worker.py
from __future__ import annotations

import shutil
import wave
import threading
from pathlib import Path
from typing import List, Optional, Tuple, Union, Dict, Any, Set

from PyQt5 import QtCore

import numpy as np

from core.config.app_config import AppConfig as Config
from core.io.file_manager import FileManager
from core.services.download_service import DownloadService, DownloadError, DownloadCancelled
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

    @QtCore.pyqtSlot()
    def run(self) -> None:
        processed_any = False

        trans_cfg = Config.transcription_settings()
        keep_downloaded_files: bool = bool(trans_cfg.get("keep_downloaded_files", True))
        keep_wav_temp: bool = bool(trans_cfg.get("keep_wav_temp", False))

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

            total = len(work_items)

            model_cfg = Config.model_settings()
            chunk_len = int(model_cfg.get("chunk_length_s", 30))
            stride_len = int(model_cfg.get("stride_length_s", 5))
            return_ts = bool(model_cfg.get("return_timestamps", True))
            ignore_warn = bool(model_cfg.get("ignore_warning", True))
            task = str(model_cfg.get("pipeline_task", "transcribe"))
            default_lang = model_cfg.get("default_language")

            # Prevent long-form requirements when user disabled timestamps:
            # Whisper long-form triggers at > ~30s, then requires return_timestamps=True.
            if not return_ts and chunk_len > 29:
                chunk_len = 29

            for idx, (key, path, forced_stem) in enumerate(work_items, start=1):
                if self._is_cancelled():
                    break

                out_dir = None
                write_into_existing = False

                stem = sanitize_filename(forced_stem) if forced_stem else sanitize_filename(path.stem)
                existing = FileManager.find_existing_output(stem)
                if existing is not None:
                    out_dir = Path(existing)
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
                        self.progress.emit(int(idx * 100 / total))
                        continue
                    elif self._conflict_action == "new":
                        if self._conflict_new_stem:
                            stem = sanitize_filename(self._conflict_new_stem)
                            out_dir = FileManager.output_dir_for(stem)
                    elif self._conflict_action == "overwrite":
                        out_dir = Path(existing)
                        write_into_existing = True

                # ----- Prepare model input (force WAV for cancellable chunking) -----
                try:
                    if self._is_cancelled():
                        break
                    self.item_status.emit(key, tr("status.prep"))
                    model_input = FileManager.ensure_tmp_wav(
                        path,
                        cancel_check=self._is_cancelled,
                    )
                except Exception as e:
                    if self._is_cancelled():
                        raise _Cancelled()
                    self.item_status.emit(key, tr("status.error"))
                    self.log.emit(tr("log.audio_prep_failed", name=path.name, detail=str(e)))
                    self.progress.emit(int(idx * 100 / total))
                    continue

                if self._is_cancelled():
                    break

                # ----- Run ASR (chunked, cancellable) -----
                self.item_status.emit(key, tr("status.proc"))
                try:
                    generate_kwargs: Dict[str, Any] = {"task": task}
                    if default_lang:
                        generate_kwargs["language"] = default_lang

                    text = self._transcribe_wav_chunked(
                        model_input,
                        chunk_length_s=chunk_len,
                        stride_length_s=stride_len,
                        return_timestamps=return_ts,
                        generate_kwargs=generate_kwargs,
                        ignore_warning=ignore_warn,
                    )

                except _Cancelled:
                    raise
                except Exception as e:
                    self.item_status.emit(key, tr("status.error"))
                    self.log.emit(tr("log.transcription_failed", name=path.name, detail=str(e)))
                    self.progress.emit(int(idx * 100 / total))
                    continue

                # ----- Ensure session directory exists -----
                try:
                    if not write_into_existing:
                        FileManager.ensure_session()
                except Exception as e:
                    self.item_status.emit(key, tr("status.error"))
                    self.log.emit(tr("log.session_dir_failed", detail=str(e)))
                    self.progress.emit(int(idx * 100 / total))
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
                    self.transcript_ready.emit(key, str(out_txt))
                    processed_any = True

                except Exception as e:
                    self.item_status.emit(key, tr("status.error"))
                    self.log.emit(tr("log.transcript.save_failed", name=path.name, detail=str(e)))
                    if created_dir:
                        FileManager.remove_dir_if_empty(out_dir)

                # ----- Optional cleanup of downloaded originals -----
                try:
                    if (not keep_downloaded_files) and (path in self._downloaded):
                        path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass

                self.progress.emit(int(idx * 100 / total))

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
                # Some pipeline versions don't accept ignore_warning
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
                # Retry with timestamps enabled (doesn't harm; we still read only "text")
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
        wav_path: Path,
        *,
        chunk_length_s: int,
        stride_length_s: int,
        return_timestamps: bool,
        generate_kwargs: Dict[str, Any],
        ignore_warning: bool,
    ) -> str:
        out_text = ""

        with wave.open(str(wav_path), "rb") as wf:
            sr = int(wf.getframerate())
            n_channels = int(wf.getnchannels())
            sampwidth = int(wf.getsampwidth())
            n_frames = int(wf.getnframes())

            chunk_frames = max(1, int(chunk_length_s) * sr)
            stride_frames = max(0, int(stride_length_s) * sr)
            step = max(1, chunk_frames - stride_frames)

            pos = 0
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

                seg = arr.astype(np.float32) / float(scale)

                result = self._call_pipe_safe(
                    seg,
                    sr,
                    return_timestamps=return_timestamps,
                    generate_kwargs=generate_kwargs,
                    ignore_warning=ignore_warning,
                )

                txt = result.get("text", "")
                txt = TextPostprocessor.clean(str(txt))
                out_text = self._merge_text(out_text, txt)

                pos += step

        return out_text

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
            existing = FileManager.find_existing_output(predicted_stem)
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
