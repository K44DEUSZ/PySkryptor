# model/services/transcription_service.py
from __future__ import annotations

import math
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import numpy as np

from model.config.app_config import AppConfig as Config
from model.io.audio_extractor import AudioExtractor
from model.io.file_manager import FileManager
from model.io.text import TextPostprocessor, is_url, sanitize_filename
from model.services.conflict_service import ConflictService
from model.services.download_service import DownloadCancelled, DownloadError, DownloadService
from model.services.model_loader import ModelLoader


GUIEntry = Union[str, Dict[str, Any]]
WorkItem = Tuple[str, Path, Optional[str]]

TranslateFn = Callable[[str], str]
TranslateKwFn = Callable[[str], str]
LogFn = Callable[[str], None]
ProgressFn = Callable[[int], None]
ItemStatusFn = Callable[[str, str], None]
ItemProgressFn = Callable[[str, int], None]
ItemPathUpdateFn = Callable[[str, str], None]
TranscriptReadyFn = Callable[[str, str], None]
CancelCheckFn = Callable[[], bool]
ConflictResolverFn = Callable[[str, str], Tuple[str, str, bool]]


class _Cancelled(RuntimeError):
    pass


class _FatalPipeError(RuntimeError):
    """A pipeline error that should stop the whole session (config/model mismatch)."""


@dataclass(frozen=True)
class SessionResult:
    session_dir: str
    processed_any: bool
    had_errors: bool
    was_cancelled: bool


class TranscriptionService:
    """Facade for ASR pipeline build + transcription session execution."""

    def __init__(self, backend: Optional[ModelLoader] = None) -> None:
        self._loader: ModelLoader = backend or ModelLoader()
        self._pipe: Optional[Any] = None

    @property
    def pipeline(self) -> Any:
        return self._pipe

    def build(self, log: Callable[[str], None]) -> None:
        self._loader.load(log=log)
        self._pipe = self._loader.pipeline

    # ----- High-level session API -----

    def run_session(
        self,
        *,
        pipe: Any,
        entries: List[GUIEntry],
        translate: Callable[[str, Any], str],
        log: LogFn,
        progress: ProgressFn,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        transcript_ready: TranscriptReadyFn,
        conflict_resolver: ConflictResolverFn,
        cancel_check: CancelCheckFn,
    ) -> SessionResult:
        processed_any = False
        had_errors = False

        download = DownloadService()
        downloaded: Set[Path] = set()

        total_chunks = 0
        done_chunks = 0

        keep_wav_with_transcript = False

        def _ensure_not_cancelled() -> None:
            if cancel_check():
                raise _Cancelled()

        def _bump_global_progress() -> None:
            if total_chunks <= 0:
                progress(0)
                return
            pct = int((done_chunks * 100.0) / float(total_chunks))
            progress(max(0, min(100, pct)))


        def _bump_chunk_done() -> None:
            nonlocal done_chunks
            done_chunks += 1
            _bump_global_progress()

        try:
            FileManager.plan_session()

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

            keep_wav_with_transcript = bool(trans_cfg.get("keep_wav_temp", False))

            want_timestamped_output = bool(out_ext == "srt" or timestamps_output)
            return_ts_base = bool(want_timestamped_output)

            # ----- Materialize entries into work items -----
            work_items: List[WorkItem] = []
            for entry in list(entries):
                if cancel_check():
                    break
                try:
                    work_items.extend(
                        self._materialize_entry(
                            entry=entry,
                            download=download,
                            downloaded=downloaded,
                            item_status=item_status,
                            item_progress=item_progress,
                            item_path_update=item_path_update,
                            log=log,
                            translate=translate,
                            cancel_check=cancel_check,
                        )
                    )
                except _Cancelled:
                    break
                except Exception as e:
                    had_errors = True
                    log(translate("log.worker_error", detail=str(e)))

            _ensure_not_cancelled()

            if not work_items:
                progress(0)
                return SessionResult(session_dir="", processed_any=False, had_errors=had_errors, was_cancelled=False)

            # ----- Global progress estimation -----
            total_dur = 0.0
            for _key, p, _forced in work_items:
                try:
                    d = AudioExtractor.probe_duration(p)
                    total_dur += float(d or 0.0)
                except Exception:
                    pass

            total_chunks = max(1, int(self._estimate_chunks(total_dur, chunk_len, stride_len)))
            _bump_global_progress()

            # apply-all conflict policy cache
            apply_all_action: Optional[str] = None
            apply_all_new_stem: str = ""
            apply_all_enabled = False

            # ----- Process items -----
            for key, path, forced_stem in work_items:
                if cancel_check():
                    break

                stem = sanitize_filename(forced_stem) if forced_stem else sanitize_filename(path.stem)

                item_status(key, translate("status.prep"))
                item_progress(key, 0)

                # ----- Resolve output folder conflicts -----
                out_dir, new_stem, apply_all_action, apply_all_new_stem, apply_all_enabled = self._resolve_output_dir(
                    stem=stem,
                    translate=translate,
                    conflict_resolver=conflict_resolver,
                    cancel_check=cancel_check,
                    apply_all_action=apply_all_action,
                    apply_all_new_stem=apply_all_new_stem,
                    apply_all_enabled=apply_all_enabled,
                )
                if out_dir is None:
                    had_errors = True
                    item_status(key, translate("status.error"))
                    continue

                stem = new_stem
                item_status(key, translate("status.proc"))

                tmp_wav: Optional[Path] = None
                wav_path: Optional[Path] = None

                try:
                    wav_path = FileManager.ensure_tmp_wav(
                        path,
                        log=lambda m: log(str(m)),
                        cancel_check=cancel_check,
                    )
                    tmp_wav = wav_path if wav_path != path else None

                    _ensure_not_cancelled()

                    merged_text, segments = self._transcribe_wav(
                        pipe=pipe,
                        key=key,
                        wav_path=wav_path,
                        task=task,
                        chunk_len_s=chunk_len,
                        stride_len_s=stride_len,
                        default_lang=default_lang,
                        return_ts_base=return_ts_base,
                        ignore_warn=ignore_warn,
                        translate=translate,
                        cancel_check=cancel_check,
                        item_progress=item_progress,
                        bump_chunk_done=_bump_chunk_done,
                    )

                    transcript_path = self._write_outputs(
                        key=key,
                        stem=stem,
                        out_dir=out_dir,
                        merged_text=merged_text,
                        segments=segments,
                        out_ext=out_ext,
                        timestamps_output=timestamps_output,
                        translate=translate,
                        log=log,
                        item_status=item_status,
                        transcript_ready=transcript_ready,
                        cancel_check=cancel_check,
                    )
                    if transcript_path is None:
                        had_errors = True
                        item_status(key, translate("status.error"))
                        continue

                    if keep_wav_with_transcript and wav_path is not None:
                        try:
                            self._persist_wav_asset(
                                stem=stem,
                                wav_path=wav_path,
                                tmp_wav=tmp_wav,
                            )
                            if tmp_wav is not None and FileManager.audio_wav_path(stem).exists():
                                tmp_wav = None
                        except Exception as e:
                            had_errors = True
                            log(translate("log.worker_error", detail=str(e)))

                    item_progress(key, 100)
                    item_status(key, translate("status.done"))
                    processed_any = True

                except _FatalPipeError as e:
                    had_errors = True
                    log(translate("log.transcription_failed", name="ASR pipeline", detail=str(e)))
                    item_status(key, translate("status.error"))
                    break
                except _Cancelled:
                    break
                except Exception as e:
                    had_errors = True
                    log(translate("log.transcription_failed", name=str(path.name), detail=str(e)))
                    item_status(key, translate("status.error"))
                    continue
                finally:
                    if tmp_wav is not None and tmp_wav.exists() and not keep_wav_with_transcript:
                        try:
                            tmp_wav.unlink(missing_ok=True)  # type: ignore[call-arg]
                        except Exception:
                            pass

                    try:
                        if path in downloaded:
                            try:
                                path.unlink(missing_ok=True)  # type: ignore[call-arg]
                            except Exception:
                                pass
                            downloaded.discard(path)
                    except Exception:
                        pass

            if cancel_check():
                log(translate("log.cancelled"))
            elif had_errors:
                log(translate("log.finished_with_errors"))
            elif processed_any:
                log(translate("log.done"))

        except _Cancelled:
            pass
        except Exception as e:
            had_errors = True
            log(translate("log.worker_error", detail=str(e)))

        finally:
            session_dir_str = ""
            try:
                session_dir_str = str(FileManager.session_dir())
            except Exception:
                session_dir_str = ""

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

            was_cancelled = bool(cancel_check())

            if not processed_any and was_cancelled:
                progress(0)
            else:
                progress(100 if processed_any else 0)

            return SessionResult(
                session_dir=session_dir_str,
                processed_any=bool(processed_any),
                had_errors=bool(had_errors),
                was_cancelled=bool(was_cancelled),
            )

    # ----- Conflicts / output dir -----

    def _resolve_output_dir(
        self,
        *,
        stem: str,
        translate: Callable[[str, Any], str],
        conflict_resolver: ConflictResolverFn,
        cancel_check: CancelCheckFn,
        apply_all_action: Optional[str],
        apply_all_new_stem: str,
        apply_all_enabled: bool,
    ) -> Tuple[Optional[Path], str, Optional[str], str, bool]:
        existing_str = ConflictService.existing_dir(stem)
        existing = Path(existing_str) if existing_str else None

        if existing is None:
            return FileManager.ensure_output(stem), stem, apply_all_action, apply_all_new_stem, apply_all_enabled

        if apply_all_enabled:
            action = (apply_all_action or "skip").strip().lower()
            if action == "overwrite":
                return existing, existing.name, apply_all_action, apply_all_new_stem, apply_all_enabled
            if action == "skip":
                return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled
            if action == "new":
                new_stem = sanitize_filename(apply_all_new_stem) or f"{stem} (2)"
                out = FileManager.ensure_output(new_stem)
                return out, out.name, apply_all_action, apply_all_new_stem, apply_all_enabled
            return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled

        if cancel_check():
            return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled

        action, new_name, apply_all = conflict_resolver(stem, str(existing))
        if cancel_check():
            return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled

        action_n = (action or "skip").strip().lower()
        if apply_all:
            apply_all_action = action_n
            apply_all_new_stem = str(new_name or "")
            apply_all_enabled = True

        if action_n == "skip":
            return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled
        if action_n == "overwrite":
            return existing, existing.name, apply_all_action, apply_all_new_stem, apply_all_enabled
        if action_n == "new":
            new_stem = sanitize_filename(str(new_name or "")) or f"{stem} (2)"
            out = FileManager.ensure_output(new_stem)
            return out, out.name, apply_all_action, apply_all_new_stem, apply_all_enabled

        return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled

    # ----- Entry materialization -----

    def _materialize_entry(
        self,
        *,
        entry: GUIEntry,
        download: DownloadService,
        downloaded: Set[Path],
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        log: LogFn,
        translate: Callable[[str, Any], str],
        cancel_check: CancelCheckFn,
    ) -> List[WorkItem]:
        if cancel_check():
            raise _Cancelled()

        if isinstance(entry, str):
            raw = entry.strip()
            if not raw:
                return []
            if is_url(raw):
                return self._materialize_url(
                    url=raw,
                    meta={},
                    download=download,
                    downloaded=downloaded,
                    item_status=item_status,
                    item_progress=item_progress,
                    item_path_update=item_path_update,
                    log=log,
                    translate=translate,
                    cancel_check=cancel_check,
                )
            return self._materialize_path(
                path=Path(raw),
                meta={},
                item_status=item_status,
                item_progress=item_progress,
                log=log,
                translate=translate,
                cancel_check=cancel_check,
            )

        if isinstance(entry, dict):
            url = (entry.get("url") or entry.get("link") or "").strip()
            path_val = entry.get("path") or entry.get("file_path")
            title = str(entry.get("title") or entry.get("name") or "").strip()
            audio_lang = entry.get("audio_lang") or entry.get("lang") or None

            meta = {"title": title, "audio_lang": audio_lang}

            if url:
                return self._materialize_url(
                    url=url,
                    meta=meta,
                    download=download,
                    downloaded=downloaded,
                    item_status=item_status,
                    item_progress=item_progress,
                    item_path_update=item_path_update,
                    log=log,
                    translate=translate,
                    cancel_check=cancel_check,
                )

            if path_val:
                return self._materialize_path(
                    path=Path(str(path_val)),
                    meta=meta,
                    item_status=item_status,
                    item_progress=item_progress,
                    log=log,
                    translate=translate,
                    cancel_check=cancel_check,
                )

            raw = str(entry.get("value") or "").strip()
            if raw:
                if is_url(raw):
                    return self._materialize_url(
                        url=raw,
                        meta=meta,
                        download=download,
                        downloaded=downloaded,
                        item_status=item_status,
                        item_progress=item_progress,
                        item_path_update=item_path_update,
                        log=log,
                        translate=translate,
                        cancel_check=cancel_check,
                    )
                return self._materialize_path(
                    path=Path(raw),
                    meta=meta,
                    item_status=item_status,
                    item_progress=item_progress,
                    log=log,
                    translate=translate,
                    cancel_check=cancel_check,
                )

        return []

    def _materialize_url(
        self,
        *,
        url: str,
        meta: Dict[str, Any],
        download: DownloadService,
        downloaded: Set[Path],
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        log: LogFn,
        translate: Callable[[str, Any], str],
        cancel_check: CancelCheckFn,
    ) -> List[WorkItem]:
        if cancel_check():
            raise _Cancelled()

        key = url
        item_status(key, translate("status.prep"))

        audio_lang = meta.get("audio_lang")
        title = meta.get("title") or None

        def _cancel_check() -> bool:
            return cancel_check()

        try:
            info = download.probe(url, log=log)
        except DownloadError as ex:
            item_status(key, translate("status.error"))
            log(translate(ex.key, **getattr(ex, "params", {})))
            return []

        if cancel_check():
            raise _Cancelled()

        title_probe = str(info.get("title") or "").strip()
        if not title:
            title = title_probe or "download"

        safe_stem = sanitize_filename(title) or "download"

        kind = "audio" if bool(Config.transcription_settings().get("download_audio_only", True)) else "video"
        item_status(key, translate("status.proc"))

        def _progress_hook(d: Dict[str, Any]) -> None:
            if cancel_check():
                raise DownloadCancelled()
            st = d.get("status")
            if st == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded_bytes = d.get("downloaded_bytes") or 0
                if total:
                    pct = int(downloaded_bytes * 100 / total)
                    item_progress(key, max(0, min(100, pct)))
            elif st == "finished":
                item_progress(key, 100)

        try:
            out_path = download.download(
                url=url,
                kind=kind,
                quality="auto",
                ext="m4a" if kind == "audio" else "mp4",
                out_dir=FileManager.url_tmp_dir(),
                progress_cb=_progress_hook,
                log=log,
                audio_lang=audio_lang,
                file_stem=safe_stem,
                cancel_check=_cancel_check,
            )
        except DownloadCancelled:
            raise _Cancelled()
        except DownloadError as ex:
            item_status(key, translate("status.error"))
            log(translate(ex.key, **getattr(ex, "params", {})))
            return []
        except Exception as e:
            item_status(key, translate("status.error"))
            log(translate("error.down.download_failed", detail=str(e)))
            return []

        if not out_path:
            item_status(key, translate("status.error"))
            log(translate("error.down.no_output_file"))
            return []

        downloaded.add(out_path)
        new_key = str(out_path)
        item_path_update(key, new_key)
        return [(new_key, out_path, safe_stem)]

    def _materialize_path(
        self,
        *,
        path: Path,
        meta: Dict[str, Any],
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        log: LogFn,
        translate: Callable[[str, Any], str],
        cancel_check: CancelCheckFn,
    ) -> List[WorkItem]:
        if cancel_check():
            raise _Cancelled()

        p = Path(path)
        key = str(p)
        if not p.exists():
            item_status(key, translate("status.error"))
            log(translate("log.path_not_found", path=str(p)))
            return []

        title = str(meta.get("title") or "").strip()
        forced_stem = sanitize_filename(title) if title else None

        item_status(key, translate("status.prep"))
        item_progress(key, 0)
        return [(key, p, forced_stem)]

    # ----- Transcription core -----

    def _transcribe_wav(
        self,
        *,
        pipe: Any,
        key: str,
        wav_path: Path,
        task: str,
        chunk_len_s: int,
        stride_len_s: int,
        default_lang: Optional[str],
        return_ts_base: bool,
        ignore_warn: bool,
        translate: Callable[[str, Any], str],
        cancel_check: CancelCheckFn,
        item_progress: ItemProgressFn,
        bump_chunk_done: Callable[[], None],
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
                if cancel_check():
                    raise _Cancelled()

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

                return_ts_effective = bool(return_ts_base or (chunk_seconds > 30.0))

                result = self._call_pipe_safe(
                    pipe,
                    audio,
                    sr,
                    return_timestamps=return_ts_effective,
                    generate_kwargs=generate_kwargs,
                    ignore_warning=ignore_warn,
                )

                text = str(result.get("text", "") or "")
                merged_text = self._merge_text(merged_text, text)

                segments.extend(self._extract_segments(result, offset_s=offset_s))

                done_s = min(duration_s, offset_s + chunk_len_s)
                pct_item = int((done_s * 100.0) / duration_s) if duration_s > 0 else 100
                item_progress(key, max(0, min(100, pct_item)))

                bump_chunk_done()

                if start_frame + chunk_len_frames >= n_frames:
                    break
                start_frame += step_frames

        return merged_text, segments

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
        return "long-form generation" in msg and "return_timestamps=true" in msg and "timestamp tokens" in msg

    def _call_pipe_safe(
        self,
        pipe: Any,
        audio: np.ndarray,
        sr: int,
        *,
        return_timestamps: bool,
        generate_kwargs: Dict[str, Any],
        ignore_warning: bool,
    ) -> Dict[str, Any]:
        if pipe is None:
            raise _FatalPipeError("pipe-not-ready")

        payload = {"array": audio, "sampling_rate": sr}

        try:
            try:
                result = pipe(payload, return_timestamps=return_timestamps, generate_kwargs=generate_kwargs)
            except TypeError:
                result = pipe(payload, generate_kwargs=generate_kwargs)

            if isinstance(result, dict):
                return result
            return {"text": str(result)}

        except Exception as e:
            if self._is_longform_timestamp_requirement(e) and not return_timestamps:
                try:
                    result = pipe(payload, return_timestamps=True, generate_kwargs=generate_kwargs)
                    if isinstance(result, dict):
                        return result
                    return {"text": str(result)}
                except Exception as e2:
                    raise _FatalPipeError(str(e2)) from e2

            if ignore_warning:
                raise _FatalPipeError(str(e)) from e
            raise

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
        translate: Callable[[str, Any], str],
        log: LogFn,
        item_status: ItemStatusFn,
        transcript_ready: TranscriptReadyFn,
        cancel_check: CancelCheckFn,
    ) -> Optional[Path]:
        if cancel_check():
            raise _Cancelled()

        out_text = self._render_transcript(
            merged_text=merged_text,
            segments=segments,
            out_ext=out_ext,
            timestamps_output=timestamps_output,
        )

        base_name = translate("files.transcript.default_name")
        base_name = sanitize_filename(str(base_name)) or "Transcript"
        out_path = FileManager.transcript_path(stem, base_name=base_name)

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_text(out_text, encoding="utf-8")
        except Exception as e:
            log(translate("log.transcript.save_failed", name=stem, detail=str(e)))
            item_status(key, translate("status.error"))
            return None

        transcript_ready(key, str(out_path))
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

    def _persist_wav_asset(self, *, stem: str, wav_path: Path, tmp_wav: Optional[Path]) -> None:
        target = FileManager.audio_wav_path(stem)

        try:
            if target.exists():
                target.unlink(missing_ok=True)  # type: ignore[call-arg]
        except Exception:
            pass

        if tmp_wav is not None and tmp_wav.exists():
            try:
                shutil.move(str(tmp_wav), str(target))
                return
            except Exception:
                pass

        if wav_path.exists():
            shutil.copy2(str(wav_path), str(target))

    @staticmethod
    def _estimate_chunks(total_dur_s: float, chunk_len: int, stride_len: int) -> int:
        if total_dur_s <= 0:
            return 1
        step = max(1, int(chunk_len) - int(stride_len))
        return max(1, int(math.ceil(total_dur_s / float(step))))
