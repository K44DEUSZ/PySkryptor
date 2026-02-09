# model/services/transcription_service.py
from __future__ import annotations

import math
import re
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
from model.services.translation_service import TranslationService


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
ItemErrorFn = Callable[[str, str], None]
ItemOutputDirFn = Callable[[str, str], None]
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



@dataclass
class _ItemPlan:
    has_download: bool
    has_translate: bool
    # stage -> pct (0..100)
    stage_pct: Dict[str, int]


class _ProgressTracker:
    """Compute a global progress value from per-item stage percentages."""

    _BASE_WEIGHTS: Dict[str, int] = {
        "download": 20,
        "transcribe": 60,
        "translate": 15,
        "save": 5,
    }

    def __init__(self) -> None:
        self._items: Dict[str, _ItemPlan] = {}

    def register(self, key: str, *, has_download: bool, has_translate: bool) -> None:
        self._items[str(key)] = _ItemPlan(
            has_download=bool(has_download),
            has_translate=bool(has_translate),
            stage_pct={"download": 0, "transcribe": 0, "translate": 0, "save": 0},
        )

    def rename_key(self, old_key: str, new_key: str) -> None:
        old = str(old_key)
        new = str(new_key)
        if old == new:
            return
        plan = self._items.pop(old, None)
        if plan is not None:
            self._items[new] = plan

    def set_stage(self, key: str, stage: str, pct: int) -> None:
        plan = self._items.get(str(key))
        if not plan:
            return
        stage = str(stage)
        pct = max(0, min(100, int(pct)))
        if stage in plan.stage_pct:
            plan.stage_pct[stage] = pct

    def _weights_for(self, plan: _ItemPlan) -> Dict[str, float]:
        active = {"transcribe", "save"}
        if plan.has_download:
            active.add("download")
        if plan.has_translate:
            active.add("translate")

        total = sum(self._BASE_WEIGHTS[s] for s in active) or 1
        return {s: (self._BASE_WEIGHTS[s] / float(total)) for s in active}

    def item_total(self, key: str) -> int:
        plan = self._items.get(str(key))
        if not plan:
            return 0
        weights = self._weights_for(plan)
        total = 0.0
        for stage, w in weights.items():
            total += w * float(plan.stage_pct.get(stage, 0))
        return int(max(0, min(100, round(total))))

    def global_total(self) -> int:
        if not self._items:
            return 0
        vals = [self.item_total(k) for k in self._items.keys()]
        if not vals:
            return 0
        return int(max(0, min(100, round(sum(vals) / float(len(vals))))))

class TranscriptionService:
    """Facade for ASR pipeline build + transcription session execution."""

    def __init__(self, backend: Optional[ModelLoader] = None) -> None:
        self._loader: ModelLoader = backend or ModelLoader()
        self._pipe: Optional[Any] = None

    @property
    def pipeline(self) -> Any:
        return self._pipe

    def build(self, log: Callable[[str], None]) -> None:
        self._loader.load_transcription(log=log)
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
        item_error: Optional[ItemErrorFn] = None,
        item_output_dir: Optional[ItemOutputDirFn] = None,
        conflict_resolver: ConflictResolverFn,
        cancel_check: CancelCheckFn,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> SessionResult:
        processed_any = False
        had_errors = False

        download = DownloadService()
        downloaded: Set[Path] = set()

        total_chunks = 0
        done_chunks = 0

        keep_intermediate_files = False

        def _emit_item_error(key: str, msg: str) -> None:
            try:
                if item_error is not None:
                    item_error(str(key), str(msg))
            except Exception:
                pass

        def _emit_item_output_dir(key: str, out_dir: str) -> None:
            try:
                if item_output_dir is not None:
                    item_output_dir(str(key), str(out_dir))
            except Exception:
                pass

        def _ensure_not_cancelled() -> None:
            if cancel_check():
                raise _Cancelled()

        def _bump_global_progress() -> None:
            _emit_global()

        def _bump_chunk_done() -> None:
            _bump_global_progress()

        try:
            FileManager.plan_session()

            model_cfg = Config.SETTINGS.model.get('transcription_model', {})
            trans_cfg = Config.SETTINGS.transcription

            task = "transcribe"

            chunk_len = int(model_cfg.get("chunk_length_s", 30))
            stride_len = int(model_cfg.get("stride_length_s", 5))
            ignore_warn = bool(model_cfg.get("ignore_warning", False))
            quality_preset = str(model_cfg.get("quality_preset", "balanced") or "balanced").strip().lower()
            if quality_preset not in ("fast", "balanced", "accurate"):
                quality_preset = "balanced"
            text_consistency = bool(model_cfg.get("text_consistency", True))
            override_src = (overrides or {}).get("source_language")
            override_src = str(override_src or "").strip().lower() if override_src is not None else ""
            default_lang = override_src if override_src and override_src not in ("auto", "none") else None
            output_mode_ids = list(trans_cfg.get('output_formats') or ('txt',))

            override_translate = (overrides or {}).get("translate_after_transcription")
            translate_enabled = bool(override_translate) if override_translate is not None else bool(trans_cfg.get("translate_after_transcription", False))
            mdl = Config.SETTINGS.model.get('translation_model', {})
            tr_engine = str(mdl.get("engine_name", "none") or "none").strip().lower()
            translate_enabled = bool(translate_enabled and tr_engine and tr_engine not in ("none", "off", "disabled"))

            override_tgt = (overrides or {}).get("target_language")
            target_language = str(override_tgt or "auto").strip().lower() or "auto"

            translator = TranslationService() if translate_enabled else None

            tracker = _ProgressTracker()
            has_translate = bool(translate_enabled and target_language and target_language not in ("none","off","disabled"))

            def _register_initial_entry(e: GUIEntry) -> None:
                try:
                    if isinstance(e, str):
                        k = str(e).strip()
                        if not k:
                            return
                        tracker.register(k, has_download=is_url(k), has_translate=has_translate)
                        return
                    if isinstance(e, dict):
                        k = str(e.get("url") or e.get("link") or e.get("value") or e.get("path") or "").strip()
                        if not k:
                            return
                        tracker.register(k, has_download=is_url(k), has_translate=has_translate)
                        return
                except Exception:
                    return



            keep_intermediate_files = bool(
                trans_cfg.get("keep_intermediate_files", trans_cfg.get("keep_wav_temp", False))
            )

            want_timestamped_output = bool(
                ("srt" in output_mode_ids)
                or ("txt_ts" in output_mode_ids)
                or ("timestamps" in output_mode_ids)
            )
            return_ts_base = bool(want_timestamped_output)

            # ----- Materialize entries into work items -----

            # Pre-register original entry keys so the global progress also covers downloads.
            for _e in list(entries):
                _register_initial_entry(_e)

            def _emit_global() -> None:
                try:
                    progress(int(tracker.global_total()))
                except Exception:
                    pass

            def _norm_pct(p: Any) -> int:
                try:
                    v = float(p)
                except Exception:
                    return 0
                if 0.0 <= v <= 1.0:
                    v *= 100.0
                return max(0, min(100, int(round(v))))


            def _item_progress_download(k: str, pct: int) -> None:
                tracker.set_stage(str(k), "download", _norm_pct(pct))
                try:
                    item_progress(str(k), _norm_pct(pct))
                except Exception:
                    pass
                _emit_global()

            def _item_path_update_track(old: str, new: str) -> None:
                tracker.rename_key(str(old), str(new))
                try:
                    item_path_update(str(old), str(new))
                except Exception:
                    pass
                _emit_global()

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
                            item_progress=_item_progress_download,
                            item_path_update=_item_path_update_track,
                            log=log,
                            translate=translate,
                            cancel_check=cancel_check,
                            item_error_cb=_emit_item_error,
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
            _bump_global_progress()

            # apply-all conflict policy cache
            apply_all_action: Optional[str] = None
            apply_all_new_stem: str = ""
            apply_all_enabled = False


            def _item_progress_transcribe(k: str, pct: int) -> None:
                tracker.set_stage(str(k), "transcribe", _norm_pct(pct))
                try:
                    item_progress(str(k), _norm_pct(pct))
                except Exception:
                    pass
                _emit_global()

            def _item_progress_translate(k: str, pct: int) -> None:
                tracker.set_stage(str(k), "translate", _norm_pct(pct))
                try:
                    item_progress(str(k), _norm_pct(pct))
                except Exception:
                    pass
                _emit_global()

            # ----- Process items -----
            for key, path, forced_stem in work_items:
                if cancel_check():
                    break

                stem = sanitize_filename(forced_stem) if forced_stem else sanitize_filename(path.stem)

                item_status(key, translate("status.preparing"))
                item_progress(key, 0)

                overwrite_mode = False

                # ----- Resolve output folder conflicts -----
                out_dir, new_stem, apply_all_action, apply_all_new_stem, apply_all_enabled, overwrite_mode = self._resolve_output_dir(
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
                item_status(key, translate("status.downloading"))

                audio_asset_name = str(translate("asset.audio") or "").strip() or "Audio"
                video_asset_name = str(translate("asset.video") or "").strip() or "Video"

                tmp_wav: Optional[Path] = None
                wav_path: Optional[Path] = None

                try:
                    wav_path = FileManager.ensure_tmp_wav(
                        source=path,
                        log=lambda m: log(str(m)),
                        cancel_check=cancel_check,
                    )
                    tmp_wav = wav_path if wav_path != path else None

                    _ensure_not_cancelled()

                    item_status(key, translate("status.processing"))
                    tracker.set_stage(key, "transcribe", 0)
                    _emit_global()

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
                        quality_preset=quality_preset,
                        text_consistency=text_consistency,
                        translate=translate,
                        cancel_check=cancel_check,
                        item_progress=_item_progress_transcribe,
                        bump_chunk_done=_bump_chunk_done,
                    )

                    translated_text = ""
                    translated_segments: Optional[List[Dict[str, Any]]] = None

                    if translate_enabled and translator is not None and target_language not in ("none", "off", "disabled"):
                        tracker.set_stage(key, "translate", 0)
                        _emit_global()
                        item_status(key, translate("status.translating"))
                        src_lang = self._pick_source_language(default_lang=default_lang, merged_text=merged_text)

                        if want_timestamped_output and segments:
                            translated_segments = []
                            total = max(1, len(segments))
                            for i, seg in enumerate(segments):
                                if cancel_check():
                                    raise _Cancelled()
                                txt = str(seg.get("text") or "")
                                out = translator.translate(txt, src_lang=src_lang, tgt_lang=target_language, log=lambda m: log(str(m)))
                                seg2 = dict(seg)
                                seg2["text"] = out
                                translated_segments.append(seg2)
                                _item_progress_translate(key, int(((i + 1) * 100.0) / float(total)))
                        else:
                            # Translate in chunks to provide meaningful progress updates.
                            raw = str(merged_text or "").strip()
                            if raw:
                                parts = [p for p in re.split(r"\n\s*\n", raw) if p.strip()]
                            else:
                                parts = []
                            if not parts:
                                translated_text = ""
                                _item_progress_translate(key, 100)
                            else:
                                out_parts: List[str] = []
                                total = max(1, len(parts))
                                for i, part in enumerate(parts):
                                    if cancel_check():
                                        raise _Cancelled()
                                    out = translator.translate(part, src_lang=src_lang, tgt_lang=target_language, log=lambda m: log(str(m)))
                                    out_parts.append(str(out or ""))
                                    _item_progress_translate(key, int(((i + 1) * 100.0) / float(total)))
                                translated_text = "\n\n".join(out_parts)

                    tracker.set_stage(key, "save", 0)
                    _emit_global()
                    item_status(key, translate("status.saving"))
                    transcript_path = self._write_outputs(
                        key=key,
                        stem=stem,
                        out_dir=out_dir,
                        merged_text=merged_text,
                        translated_text=translated_text,
                        translated_segments=translated_segments,
                        segments=segments,
                        output_mode_ids=output_mode_ids,
                        translate=translate,
                        log=log,
                        item_status=item_status,
                        transcript_ready=transcript_ready,
                        item_output_dir_cb=item_output_dir,
                        item_error_cb=_emit_item_error,
                        cancel_check=cancel_check,
                    )
                    if transcript_path is None:
                        had_errors = True
                        item_status(key, translate("status.error"))
                        continue

                    tracker.set_stage(key, "save", 100)
                    _emit_global()

                    is_url_source = path in downloaded

                    # Keep intermediate file (replacement behavior):
                    # - URL sources: keep downloaded media (audio or video)
                    # - local sources: keep processed audio WAV used for ASR
                    if keep_intermediate_files and is_url_source:
                        try:
                            ext = str(path.suffix or "").lower()
                            video_exts = {
                                ".mp4",
                                ".mkv",
                                ".webm",
                                ".avi",
                                ".mov",
                                ".wmv",
                                ".flv",
                                ".m4v",
                            }
                            base = video_asset_name if ext in video_exts else audio_asset_name
                            src_target = FileManager.source_media_path(stem, src_ext=ext, base_name=base)
                            try:
                                src_target.unlink(missing_ok=True)  # type: ignore[call-arg]
                            except Exception:
                                pass
                            shutil.copy2(str(path), str(src_target))
                        except Exception as e:
                            had_errors = True
                            log(translate("log.worker_error", detail=str(e)))

                    if keep_intermediate_files and (not is_url_source) and wav_path is not None:
                        try:
                            self._persist_wav_asset(
                                stem=stem,
                                wav_path=wav_path,
                                tmp_wav=tmp_wav,
                                audio_filename=audio_asset_name,
                            )
                            if tmp_wav is not None and FileManager.audio_wav_path(
                                stem, filename=audio_asset_name
                            ).exists():
                                tmp_wav = None
                        except Exception as e:
                            had_errors = True
                            log(translate("log.worker_error", detail=str(e)))

                    item_progress(key, 100)
                    item_status(key, translate("status.done"))
                    processed_any = True

                except _FatalPipeError as e:
                    had_errors = True
                    msg = translate("log.transcription_failed", name="ASR pipeline", detail=str(e))
                    log(msg)
                    _emit_item_error(key, msg)
                    item_status(key, translate("status.error"))
                    break
                except _Cancelled:
                    break
                except Exception as e:
                    had_errors = True
                    msg = translate("log.transcription_failed", name=str(path.name), detail=str(e))
                    log(msg)
                    _emit_item_error(key, msg)
                    item_status(key, translate("status.error"))
                    continue
                finally:
                    if tmp_wav is not None and tmp_wav.exists():
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
                FileManager.delete_output_dir(existing)
                out = FileManager.ensure_output(stem)
                return out, out.name, apply_all_action, apply_all_new_stem, apply_all_enabled, False
            if action == "skip":
                return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled, False
            if action == "new":
                new_stem = sanitize_filename(apply_all_new_stem) or f"{stem} (2)"
                out = FileManager.ensure_output(new_stem)
                return out, out.name, apply_all_action, apply_all_new_stem, apply_all_enabled, False
            return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled, False

        if cancel_check():
            return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled, False

        action, new_name, apply_all = conflict_resolver(stem, str(existing))
        if cancel_check():
            return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled, False

        action_n = (action or "skip").strip().lower()
        if apply_all:
            apply_all_action = action_n
            apply_all_new_stem = str(new_name or "")
            apply_all_enabled = True

        if action_n == "skip":
            return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled, False
        if action_n == "overwrite":
            FileManager.delete_output_dir(existing)
            out = FileManager.ensure_output(stem)
            return out, out.name, apply_all_action, apply_all_new_stem, apply_all_enabled, False
        if action_n == "new":
            new_stem = sanitize_filename(str(new_name or "")) or f"{stem} (2)"
            out = FileManager.ensure_output(new_stem)
            return out, out.name, apply_all_action, apply_all_new_stem, apply_all_enabled, False

        return None, stem, apply_all_action, apply_all_new_stem, apply_all_enabled, False

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
        item_error_cb: Optional[ItemErrorFn] = None,
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
                    item_error_cb=item_error_cb,
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
                    item_error_cb=item_error_cb,
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
                    item_error_cb=item_error_cb,
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
                    item_error_cb=item_error_cb,
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
        item_error_cb: Optional[ItemErrorFn] = None,
    ) -> List[WorkItem]:
        if cancel_check():
            raise _Cancelled()

        key = url
        item_status(key, translate("status.preparing"))

        audio_lang = meta.get("audio_lang")
        title = meta.get("title") or None

        def _cancel_check() -> bool:
            return cancel_check()

        try:
            info = download.probe(url, log=log)
        except DownloadError as ex:
            item_status(key, translate("status.error"))
            msg = translate(ex.key, **getattr(ex, "params", {}))
            log(msg)
            if item_error_cb is not None:
                item_error_cb(key, msg)
            return []

        if cancel_check():
            raise _Cancelled()

        title_probe = str(info.get("title") or "").strip()
        if not title:
            title = title_probe or "download"

        safe_stem = sanitize_filename(title) or "download"

        kind = "audio" if bool(Config.SETTINGS.transcription.get("download_audio_only", True)) else "video"
        item_status(key, translate("status.downloading"))
        item_progress(key, 0)

        _stage_last: Dict[str, Optional[str]] = {"v": None}

        def _progress_hook(d: Dict[str, Any]) -> None:
            if cancel_check():
                raise DownloadCancelled()
            st = d.get("status")
            if st == "downloading":
                if _stage_last["v"] != "downloading":
                    _stage_last["v"] = "downloading"
                    item_status(key, translate("status.downloading"))
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded_bytes = d.get("downloaded_bytes") or 0
                if total:
                    pct = int(downloaded_bytes * 100 / total)
                    item_progress(key, max(0, min(100, pct)))
            elif st == "finished":
                _stage_last["v"] = "finished"
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
            msg = translate(ex.key, **getattr(ex, "params", {}))
            log(msg)
            if item_error_cb is not None:
                item_error_cb(key, msg)
            return []
        except Exception as e:
            item_status(key, translate("status.error"))
            msg = translate("error.down.download_failed", detail=str(e))
            log(msg)
            if item_error_cb is not None:
                item_error_cb(key, msg)
            return []

        if not out_path:
            item_status(key, translate("status.error"))
            msg = translate("error.down.no_output_file")
            log(msg)
            if item_error_cb is not None:
                item_error_cb(key, msg)
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
        item_error_cb: Optional[ItemErrorFn] = None,
    ) -> List[WorkItem]:
        if cancel_check():
            raise _Cancelled()

        p = Path(path)
        key = str(p)
        if not p.exists():
            item_status(key, translate("status.error"))
            msg = translate("log.path_not_found", path=str(p))
            log(msg)
            if item_error_cb is not None:
                item_error_cb(key, msg)
            return []

        title = str(meta.get("title") or "").strip()
        forced_stem = sanitize_filename(title) if title else None

        item_status(key, translate("status.preparing"))
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
        quality_preset: str,
        text_consistency: bool,
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

            n_chunks = 1
            if duration_s > 0 and step_s > 0:
                n_chunks = max(1, int(math.ceil(duration_s / float(step_s))))

            for idx in range(n_chunks):
                if cancel_check():
                    raise _Cancelled()

                start = idx * step_frames
                wf.setpos(min(start, n_frames))

                frames = wf.readframes(min(chunk_len_frames, max(0, n_frames - start)))
                if not frames:
                    bump_chunk_done()
                    continue

                audio = np.frombuffer(frames, dtype=np.int16 if sampwidth == 2 else np.int8).astype(np.float32)
                if sampwidth == 2:
                    audio /= 32768.0
                else:
                    audio /= 128.0

                gen_kwargs: Dict[str, Any] = {}
                if default_lang:
                    gen_kwargs["language"] = default_lang
                if str(quality_preset or "").lower() == "balanced":
                    gen_kwargs["num_beams"] = 3
                elif str(quality_preset or "").lower() == "accurate":
                    gen_kwargs["num_beams"] = 5
                else:
                    gen_kwargs["num_beams"] = 1
                if text_consistency:
                    gen_kwargs["condition_on_prev_tokens"] = True

                try:
                    out = pipe(
                        audio,
                        chunk_length_s=chunk_len_s,
                        stride_length_s=stride_len_s,
                        task=task,
                        return_timestamps=True if return_ts_base else False,
                        generate_kwargs=gen_kwargs,
                    )
                except Exception as e:
                    raise _FatalPipeError(str(e)) from e

                text_piece = str(out.get("text") or "")
                if text_piece:
                    merged_text = (merged_text + " " + text_piece).strip()

                segs = self._extract_segments(out, offset_s=float(idx * step_s))
                segments.extend(segs)

                pct = int(((idx + 1) * 100.0) / float(n_chunks))
                item_progress(key, max(0, min(100, pct)))

                bump_chunk_done()

        if not merged_text.strip() and not ignore_warn:
            raise RuntimeError(translate("log.transcription_empty"))

        return merged_text, segments

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
        translated_text: str = "",
        translated_segments: Optional[List[Dict[str, Any]]] = None,
        segments: List[Dict[str, Any]],
        output_mode_ids: List[str],
        translate: Callable[[str, Any], str],
        log: LogFn,
        item_status: ItemStatusFn,
        transcript_ready: TranscriptReadyFn,
        item_output_dir_cb: Optional[ItemOutputDirFn] = None,
        item_error_cb: Optional[ItemErrorFn] = None,
        cancel_check: CancelCheckFn,
        overwrite_mode: bool = False,
    ) -> Optional[Path]:
        if cancel_check():
            raise _Cancelled()

        primary_path: Optional[Path] = None
        for mode_id in output_mode_ids:
            mode = Config.get_transcription_output_mode(str(mode_id))
            out_text = self._render_transcript(
                merged_text=merged_text,
                translated_text=translated_text,
                translated_segments=translated_segments,
                segments=segments,
                mode=mode,
                translate=translate,
            )

            filename = FileManager.transcript_filename(str(mode_id))
            out_path = out_dir / filename
            if not overwrite_mode:
                out_path = FileManager.ensure_unique_path(out_path)

            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path.write_text(out_text, encoding="utf-8")
            except Exception as e:
                msg = translate("log.transcript.save_failed", name=stem, detail=str(e))
                log(msg)
                item_status(key, translate("status.error"))
                if item_error_cb is not None:
                    item_error_cb(key, msg)
                return None

            if primary_path is None:
                primary_path = out_path
                transcript_ready(key, str(out_path))
                if item_output_dir_cb is not None:
                    item_output_dir_cb(key, str(out_dir))

        return primary_path

    def _pick_source_language(self, *, default_lang: Optional[str], merged_text: str) -> str:
        def _norm(code: Optional[str]) -> str:
            return str(code or "").strip().lower().replace("_", "-").split("-", 1)[0]

        src = _norm(default_lang)
        if src and src != "auto":
            return src

        t = str(merged_text or "").strip()
        if not t:
            return ""

        # Fast, dependency-free heuristic. We prefer a conservative guess to avoid feeding M2M100
        # the wrong src_lang (which often produces "translated" text that is mostly unchanged).
        if any(ch in t for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"):
            return "pl"

        sample = re.sub(r"[^a-zA-Z\s]", " ", t.lower())
        words = [w for w in sample.split() if w]
        if not words:
            return ""

        common_pl = {
            "nie", "sie", "ze", "na", "jest", "dla", "oraz", "ale", "tak", "to", "w", "z", "do",
            "jak", "mozna", "ktory", "ktore", "ktora", "przez", "bardzo", "juz", "tutaj",
            "wlasnie", "ponizej", "ponad", "zostala", "zostal", "zostaly", "bedzie", "byc",
        }
        score = sum(1 for w in words[:500] if w in common_pl)

        # Polish without diacritics tends to contain many "rz/sz/cz/dz/ch" sequences.
        seq_score = 0
        s = " ".join(words[:500])
        for seq in ("rz", "sz", "cz", "dz", "ch"):
            seq_score += s.count(seq)

        if score >= 3 or seq_score >= 8:
            return "pl"

        return ""

    def _translate_segments(
        self,
        *,
        segments: List[Dict[str, Any]],
        translator: TranslationService,
        src_lang: str,
        tgt_lang: str,
        log: LogFn,
    ) -> Optional[List[Dict[str, Any]]]:
        out: List[Dict[str, Any]] = []
        any_ok = False

        for seg in segments:
            s = float(seg.get("start", 0.0) or 0.0)
            e = float(seg.get("end", 0.0) or 0.0)
            txt = str(seg.get("text", "") or "").strip()
            if not txt:
                out.append({"start": s, "end": e, "text": ""})
                continue

            tr_txt = translator.translate(txt, src_lang=src_lang, tgt_lang=tgt_lang, log=lambda m: log(str(m)))
            tr_txt = str(tr_txt or "").strip()
            if tr_txt:
                any_ok = True
                out.append({"start": s, "end": e, "text": tr_txt})
            else:
                out.append({"start": s, "end": e, "text": txt})

        return out if any_ok else None

    def _render_transcript(
        self,
        *,
        merged_text: str,
        translated_text: str = "",
        translated_segments: Optional[List[Dict[str, Any]]] = None,
        segments: List[Dict[str, Any]],
        mode: Dict[str, Any],
        translate: Callable[[str, Any], str],
    ) -> str:
        """Render a single transcript output according to a selected output mode."""

        out_ext = str(mode.get("ext", "txt") or "txt").strip().lower().lstrip(".") or "txt"
        timestamps_output = bool(mode.get("timestamps", False))

        if out_ext not in ("txt", "srt", "sub"):
            out_ext = "txt"

        if out_ext == "srt":
            use = translated_segments if translated_segments else segments
            return TextPostprocessor.to_srt(use)

        if out_ext == "txt" and timestamps_output:
            use = translated_segments if translated_segments else segments
            return TextPostprocessor.to_timestamped_plain(use)

        merged = TextPostprocessor.clean(merged_text)

        if out_ext == "txt":
            if translated_text and translated_text.strip():
                return TextPostprocessor.clean(translated_text)
            if translated_segments:
                return TextPostprocessor.to_plain(translated_segments)

        if merged:
            return merged
        return TextPostprocessor.to_plain(segments)

    def _persist_wav_asset(self, *, stem: str, wav_path: Path, tmp_wav: Optional[Path], audio_filename: str) -> None:
        target = FileManager.audio_wav_path(stem, filename=audio_filename)

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
