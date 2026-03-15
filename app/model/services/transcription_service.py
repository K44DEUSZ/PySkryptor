# app/model/services/transcription_service.py
from __future__ import annotations

import logging
import math
import re
import shutil
import time
import wave

import torch
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from app.controller.platform.logging import sanitize_url_for_log
from app.model.config.app_config import AppConfig as Config
from app.model.helpers.errors import AppError, OperationCancelled
from app.model.helpers.chunking import (
    estimate_chunks,
    iter_wav_mono_chunks,
    normalize_chunk_params,
    pcm16le_bytes_to_float32,
    seconds_to_frames,
)
from app.model.helpers.output_resolver import OutputResolver
from app.model.helpers.string_utils import sanitize_filename
from app.model.io.audio_extractor import AudioExtractor
from app.model.io.file_manager import FileManager
from app.model.io.media_probe import is_url_source
from app.model.io.transcript_writer import TextPostprocessor
from app.model.services.download_service import DownloadService
from app.model.services.translation_service import TranslationService

_LOG = logging.getLogger(__name__)


# ----- Errors -----

class TranscriptionError(AppError):
    """Key-based error used for i18n-friendly transcription failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key=str(key), params=dict(params or {}))

SourceEntry = Union[str, Dict[str, Any]]
WorkItem = Tuple[str, Path, Optional[str]]

ProgressFn = Callable[[int], None]
CancelCheckFn = Callable[[], bool]
ItemStatusFn = Callable[[str, str], None]
ItemProgressFn = Callable[[str, int], None]
ItemPathUpdateFn = Callable[[str, str], None]
TranscriptReadyFn = Callable[[str, str], None]
ItemErrorFn = Callable[[str, str, dict], None]
ItemOutputDirFn = Callable[[str, str], None]
ConflictResolverFn = Callable[[str, str], Tuple[str, str, bool]]


# ----- Results -----

@dataclass(frozen=True)
class SessionResult:
    """Outcome of a transcription session."""
    session_dir: str
    processed_any: bool
    had_errors: bool
    was_cancelled: bool


@dataclass(frozen=True)
class _SessionOptions:
    """Resolved session options reused across all work items."""
    output_mode_ids: List[str]
    translate_requested: bool
    want_translate: bool
    want_timestamps: bool
    tgt_lang: str
    default_lang: str
    chunk_len_s: int
    stride_len_s: int
    ignore_warning: bool


@dataclass(frozen=True)
class _MaterializeBatchResult:
    """Materialized work items and control flags for the current session."""
    work: List[WorkItem]
    had_errors: bool
    was_cancelled: bool


@dataclass(frozen=True)
class _ItemProcessResult:
    """Outcome of processing a single materialized transcription item."""
    processed_any: bool
    had_errors: bool
    was_cancelled: bool
    apply_all: Optional[Tuple[str, str]]


# ----- Live session helpers -----
@dataclass(frozen=True)
class LiveUpdate:
    """Incremental update produced by live transcription."""
    detected_language: str
    source_text: str
    target_text: str

_MERGE_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _normalize_detected_language(lang: str) -> str:
    lang = str(lang or "").strip().lower().replace("_", "-")
    lang = lang.split("-", 1)[0]
    try:
        from transformers.models.whisper.tokenization_whisper import LANGUAGES  # type: ignore

        inv = {v.lower(): k for k, v in LANGUAGES.items()}
        return inv.get(lang, lang)
    except Exception:
        return lang


def _extract_detected_language_from_result(out: Dict[str, Any]) -> str:
    lang = str(out.get("language") or "").strip().lower()
    if lang:
        return _normalize_detected_language(lang)

    chunks = out.get("chunks")
    if isinstance(chunks, list) and chunks:
        lang = str(chunks[0].get("language") or "").strip().lower()
        if lang:
            return _normalize_detected_language(lang)

    return ""


def _debug_source_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if is_url_source(text):
        return sanitize_url_for_log(text)
    try:
        return Path(text).name or text
    except Exception:
        return text


def _detect_language_from_pipe_runtime(*, pipe: Any, audio: Any, sr: int) -> str:
    """Detect language from Whisper logits when the pipeline output omits it."""
    try:
        fe = getattr(pipe, "feature_extractor", None) or getattr(getattr(pipe, "processor", None), "feature_extractor", None)
        tok = getattr(pipe, "tokenizer", None) or getattr(getattr(pipe, "processor", None), "tokenizer", None)
        model = getattr(pipe, "model", None)
        if fe is None or tok is None or model is None:
            return ""

        lang_to_id = getattr(tok, "lang_to_id", None)
        if not isinstance(lang_to_id, dict) or not lang_to_id:
            vocab = {}
            try:
                vocab = tok.get_vocab()
            except Exception:
                vocab = getattr(tok, "vocab", {}) or {}
            if isinstance(vocab, dict):
                lang_to_id = {}
                for token, token_id in vocab.items():
                    if not isinstance(token, str):
                        continue
                    if token.startswith("<|") and token.endswith("|>"):
                        code = token[2:-2].strip().lower().replace("_", "-").split("-", 1)[0]
                        if code.isalpha() and 2 <= len(code) <= 5:
                            try:
                                lang_to_id[code] = int(token_id)
                            except Exception:
                                continue

        if not isinstance(lang_to_id, dict) or not lang_to_id:
            return ""

        inputs = fe(audio, sampling_rate=int(sr), return_tensors="pt")
        input_features = inputs.get("input_features")
        if input_features is None:
            return ""

        device = getattr(model, "device", torch.device("cpu"))
        try:
            dtype = next(model.parameters()).dtype
        except Exception:
            dtype = torch.float32
        input_features = input_features.to(device=device, dtype=dtype)

        sot_id = tok.convert_tokens_to_ids("<|startoftranscript|>")
        decoder_input_ids = torch.tensor([[int(sot_id)]], device=device)

        with torch.no_grad():
            out = model(input_features=input_features, decoder_input_ids=decoder_input_ids)
            logits = out.logits[:, -1, :]

        ids = torch.tensor(list(lang_to_id.values()), device=logits.device)
        scores = logits.index_select(-1, ids)
        best_idx = int(torch.argmax(scores, dim=-1).item())
        best_id = int(ids[best_idx].item())
        inv = {value: key for key, value in lang_to_id.items()}
        return _normalize_detected_language(inv.get(best_id, ""))
    except Exception:
        return ""


class LiveSession:
    """Stateful live transcription session fed with PCM16 audio bytes."""

    def __init__(
        self,
        *,
        pipe: Any,
        source_language: str,
        target_language: str,
        translate_enabled: bool,
        include_source_in_translate: bool,
        cancel_check: CancelCheckFn,
        chunk_length_s: int | None = None,
        stride_length_s: int | None = None,
    ) -> None:
        self._pipe = pipe
        self._cancel_check = cancel_check

        self._src_lang = Config.normalize_policy_value(source_language)
        if Config.is_auto_language_value(self._src_lang):
            self._src_lang = ""

        self._tgt_lang = Config.normalize_policy_value(target_language)
        self._translate = bool(translate_enabled) and bool(self._tgt_lang) and not Config.is_auto_language_value(self._tgt_lang)
        self._include_source = bool(include_source_in_translate)

        snap = Config.SETTINGS
        model_cfg = getattr(snap, "model", {}) if snap is not None else {}
        if not isinstance(model_cfg, dict):
            model_cfg = {}
        tcfg = model_cfg.get("transcription_model", {})
        if not isinstance(tcfg, dict):
            tcfg = {}

        self._sr = Config.ASR_SAMPLE_RATE
        chunk_len_s = int(chunk_length_s) if chunk_length_s is not None else int(tcfg["chunk_length_s"])
        stride_len_s = int(stride_length_s) if stride_length_s is not None else int(tcfg["stride_length_s"])

        chunk_len_s = max(1, min(chunk_len_s, 30))
        stride_len_s = max(0, min(stride_len_s, max(0, chunk_len_s - 1)))
        self._ignore_warning = bool(tcfg["ignore_warning"])
        self._text_consistency = bool(tcfg.get("text_consistency", True))

        self._chunk_f, self._stride_f, self._step_f = seconds_to_frames(self._sr, chunk_len_s, stride_len_s)
        self._buf = bytearray()
        self._silence_level_threshold = 0.04
        self._silence_reset_after_s = max(0.8, float(chunk_len_s - stride_len_s) * 0.75)
        self._silent_run_s = 0.0
        self._stability_passes = 2 if self._text_consistency else 1

        self._post = TextPostprocessor()
        self._translator = TranslationService()

        self._detected_lang = ""
        self._merged_source = ""
        self._merged_target = ""
        self._pending_source = ""
        self._pending_target = ""
        self._pending_hits = 0

    @staticmethod
    def _normalized_merge_tokens(text: str) -> List[str]:
        return [tok for tok in _MERGE_TOKEN_RE.findall(str(text or "").lower()) if tok]

    @classmethod
    def _merge_text(cls, prev: str, cur: str) -> str:
        if not prev:
            return cur
        if not cur:
            return prev

        prev_norm = cls._normalized_merge_tokens(prev)
        cur_norm = cls._normalized_merge_tokens(cur)
        if not prev_norm:
            return cur
        if not cur_norm:
            return prev
        if prev_norm == cur_norm:
            return prev if len(prev) >= len(cur) else cur

        prev_words = prev.split()
        cur_words = cur.split()

        max_k = min(18, len(prev_norm), len(cur_norm), len(prev_words), len(cur_words))
        for k in range(max_k, 0, -1):
            if prev_norm[-k:] == cur_norm[:k]:
                cur_words = cur_words[k:]
                break

        if not cur_words:
            return prev

        tail_size = min(len(prev_norm), max(len(cur_norm), 6))
        prev_tail = " ".join(prev_norm[-tail_size:])
        cur_text = " ".join(cur_norm)
        if prev_tail and cur_text and SequenceMatcher(None, prev_tail, cur_text).ratio() >= 0.9:
            return prev

        return (prev + " " + " ".join(cur_words)).strip()

    @classmethod
    def _normalized_similarity_text(cls, text: str) -> str:
        return " ".join(cls._normalized_merge_tokens(text))

    @classmethod
    def _text_similarity(cls, left: str, right: str) -> float:
        left_norm = cls._normalized_similarity_text(left)
        right_norm = cls._normalized_similarity_text(right)
        if not left_norm and not right_norm:
            return 1.0
        if not left_norm or not right_norm:
            return 0.0
        if left_norm == right_norm:
            return 1.0
        return float(SequenceMatcher(None, left_norm, right_norm).ratio())

    @classmethod
    def _choose_preferred_text(cls, prev: str, cur: str) -> str:
        prev = str(prev or "").strip()
        cur = str(cur or "").strip()
        if not cur:
            return prev
        if not prev:
            return cur
        if cls._text_similarity(prev, cur) >= 0.92:
            return cur if len(cur) >= len(prev) else prev
        return cur

    @classmethod
    def _is_same_live_hypothesis(cls, left: str, right: str) -> bool:
        left = str(left or "").strip()
        right = str(right or "").strip()
        if not left or not right:
            return False
        if cls._normalized_similarity_text(left) == cls._normalized_similarity_text(right):
            return True
        return cls._text_similarity(left, right) >= 0.84

    def _clear_pending(self) -> None:
        self._pending_source = ""
        self._pending_target = ""
        self._pending_hits = 0

    def _commit_live_text(self, source_text: str, target_text: str) -> bool:
        prev_source = self._merged_source
        prev_target = self._merged_target

        if source_text:
            self._merged_source = self._merge_text(self._merged_source, source_text)
        if target_text:
            self._merged_target = self._merge_text(self._merged_target, target_text)

        return (self._merged_source != prev_source) or (self._merged_target != prev_target)

    def _commit_pending(self) -> bool:
        if not (self._pending_source or self._pending_target):
            return False
        changed = self._commit_live_text(self._pending_source, self._pending_target)
        self._clear_pending()
        return changed

    @classmethod
    def _compose_display_text(cls, committed: str, pending: str) -> str:
        committed = str(committed or "").strip()
        pending = str(pending or "").strip()
        if not pending:
            return committed
        if not committed:
            return pending
        return cls._merge_text(committed, pending)

    def _build_update(self, *, include_pending: bool = False) -> LiveUpdate:
        source_text = self._merged_source
        target_text = self._merged_target
        if include_pending:
            source_text = self._compose_display_text(source_text, self._pending_source)
            target_text = self._compose_display_text(target_text, self._pending_target)
        return LiveUpdate(
            detected_language=self._detected_lang,
            source_text=source_text,
            target_text=target_text,
        )

    def _stage_stable_update(self, source_text: str, target_text: str) -> bool:
        source_text = str(source_text or "").strip()
        target_text = str(target_text or "").strip()
        if not (source_text or target_text):
            return False

        if not self._text_consistency:
            return self._commit_live_text(source_text, target_text)

        pending_key = self._pending_source or self._pending_target
        current_key = source_text or target_text

        if not pending_key:
            self._pending_source = source_text
            self._pending_target = target_text
            self._pending_hits = 1
        elif self._is_same_live_hypothesis(pending_key, current_key):
            self._pending_hits += 1
            self._pending_source = self._choose_preferred_text(self._pending_source, source_text)
            self._pending_target = self._choose_preferred_text(self._pending_target, target_text)
        else:
            self._pending_source = source_text
            self._pending_target = target_text
            self._pending_hits = 1

        if self._pending_hits >= self._stability_passes:
            return self._commit_pending()
        return False

    def push_pcm16(self, data: bytes, *, level: float | None = None) -> List[LiveUpdate]:
        if not data or self._cancel_check():
            return []

        out: List[LiveUpdate] = []

        if self._text_consistency:
            chunk_seconds = float(len(data)) / float(max(1, self._sr * 2))
            level_f = max(0.0, float(level or 0.0))
            if level_f <= self._silence_level_threshold:
                self._silent_run_s += chunk_seconds
                if self._silent_run_s >= self._silence_reset_after_s:
                    if self._commit_pending():
                        out.append(self._build_update())
                    self._buf.clear()
                    self._clear_pending()
                return out
            if self._silent_run_s >= self._silence_reset_after_s:
                if self._commit_pending():
                    out.append(self._build_update())
                self._buf.clear()
                self._clear_pending()
            self._silent_run_s = 0.0

        self._buf.extend(data)
        bytes_per_frame = 2

        while len(self._buf) >= self._chunk_f * bytes_per_frame:
            if self._cancel_check():
                break

            chunk = bytes(self._buf[: self._chunk_f * bytes_per_frame])
            del self._buf[: self._step_f * bytes_per_frame]

            audio = pcm16le_bytes_to_float32(chunk)
            payload = {"array": audio, "sampling_rate": self._sr}

            generate_kwargs: Dict[str, Any] = {"task": "transcribe"}
            if self._src_lang:
                generate_kwargs["language"] = self._src_lang

            try:
                try:
                    result = self._pipe(
                        payload,
                        return_language=True,
                        return_timestamps=True,
                        generate_kwargs=generate_kwargs,
                        ignore_warning=self._ignore_warning,
                    )
                except TypeError:
                    result = self._pipe(
                        payload,
                        return_language=True,
                        return_timestamps=True,
                        generate_kwargs=generate_kwargs,
                    )
            except Exception as exc:
                _LOG.exception("ASR pipeline call failed.")
                raise TranscriptionError("error.transcription.asr_failed") from exc

            if not isinstance(result, dict):
                result = {"text": str(result)}

            language_changed = False
            if not self._detected_lang:
                detected = _extract_detected_language_from_result(result)
                if not detected and (self._translate or not self._src_lang):
                    detected = _detect_language_from_pipe_runtime(
                        pipe=self._pipe,
                        audio=audio,
                        sr=self._sr,
                    )
                    if detected:
                        result["language"] = detected
                if detected:
                    self._detected_lang = detected
                    language_changed = True

            cur_source = self._post.clean(str(result.get("text") or ""))

            cur_target = ""
            if self._translate and cur_source:
                src_lang = self._detected_lang or self._src_lang
                if not src_lang:
                    try:
                        cfg_src = str(Config.translation_cfg_dict().get("source_language") or "").strip().lower()
                    except Exception:
                        cfg_src = ""
                    if cfg_src and not Config.is_translation_source_deferred_value(cfg_src):
                        src_lang = cfg_src
                if src_lang:
                    src_lang = src_lang.replace("_", "-")
                    if "-" in src_lang:
                        src_lang = src_lang.split("-", 1)[0]
                    cur_target = self._translator.translate(
                        cur_source,
                        src_lang=src_lang,
                        tgt_lang=self._tgt_lang,
                    )

            text_changed = self._stage_stable_update(cur_source, cur_target)
            if text_changed:
                out.append(self._build_update())
            elif cur_source or cur_target:
                out.append(self._build_update(include_pending=True))
            elif language_changed:
                out.append(self._build_update())

        return out


# ----- Progress tracking -----

@dataclass
class _ItemPlan:
    has_download: bool
    has_translate: bool
    weight: float
    stage_pct: Dict[str, int]


class _ProgressTracker:
    """Tracks global progress across multiple items and stages."""
    _STAGES = ("download", "preprocess", "transcribe", "translate", "save")
    _BASE_WEIGHTS = {"download": 0.10, "preprocess": 0.05, "transcribe": 0.60, "translate": 0.20, "save": 0.05}

    def __init__(self, progress_cb: ProgressFn) -> None:
        self._cb = progress_cb
        self._plans: Dict[str, _ItemPlan] = {}
        self._last_pct = 0

    def register(self, key: str, *, has_download: bool, has_translate: bool, weight: float = 1.0) -> None:
        self._plans[str(key)] = _ItemPlan(
            has_download=bool(has_download),
            has_translate=bool(has_translate),
            weight=float(max(0.0001, weight)),
            stage_pct={s: 0 for s in self._STAGES},
        )

    def set_weight(self, key: str, *, weight: float) -> None:
        k = str(key)
        if k in self._plans:
            self._plans[k].weight = float(max(0.0001, weight))

    def rename_key(self, old_key: str, new_key: str) -> None:
        """Moves an existing item plan to a new key without changing its progress."""
        old_k = str(old_key)
        new_k = str(new_key)
        if old_k == new_k:
            return
        plan = self._plans.pop(old_k, None)
        if plan is None:
            return
        self._plans[new_k] = plan

    def update(self, key: str, stage: str, pct: int) -> None:
        k = str(key)
        if k not in self._plans:
            return
        stage = str(stage)
        if stage not in self._STAGES:
            return
        self._plans[k].stage_pct[stage] = int(max(0, min(100, pct)))
        self._emit()

    def mark_done(self, key: str) -> None:
        k = str(key)
        if k in self._plans:
            for s in self._STAGES:
                self._plans[k].stage_pct[s] = 100
        self._emit()

    def _emit(self) -> None:
        if not self._plans:
            self._cb(0)
            return

        total_w = 0.0
        total_p = 0.0

        for p in self._plans.values():
            w = dict(self._BASE_WEIGHTS)
            if not p.has_download:
                w["download"] = 0.0
            if not p.has_translate:
                w["translate"] = 0.0

            norm = sum(w.values()) or 1.0
            for k in w:
                w[k] = w[k] / norm

            item_p = 0.0
            for s, ww in w.items():
                item_p += (p.stage_pct.get(s, 0) / 100.0) * ww

            total_w += p.weight
            total_p += item_p * p.weight

        pct = int(round((total_p / max(0.0001, total_w)) * 100))
        pct = max(0, min(100, pct))
        if pct < self._last_pct:
            pct = self._last_pct
        self._last_pct = pct
        self._cb(pct)


# ----- Transcription service -----

class TranscriptionService:
    """Runs transcription sessions using an already-built ASR pipeline."""

    # ----- Planning / session setup -----

    def __init__(self) -> None:
        self._download = DownloadService()
        self._translator = TranslationService()
        self._post = TextPostprocessor()

    def _estimate_item_weight(self, key: str) -> float:
        p = str(key or "")
        if not p or is_url_source(p):
            return 15.0
        try:
            path = Path(p)
        except Exception:
            return 1.0
        if not path.exists() or not path.is_file():
            return 1.0
        dur = AudioExtractor.probe_duration(path)
        if isinstance(dur, (int, float)) and dur > 0:
            return float(max(15.0, min(3600.0, float(dur))))
        try:
            size = path.stat().st_size
        except Exception:
            size = 0
        mb = float(size) / (1024.0 * 1024.0) if size else 0.0
        if mb > 0:
            return float(max(15.0, min(3600.0, mb * 10.0)))
        return 1.0

    @staticmethod
    def _entry_source_key(entry: SourceEntry) -> str:
        return str(entry.get("src") if isinstance(entry, dict) else entry)

    def _build_session_options(self, *, overrides: Optional[Dict[str, Any]]) -> _SessionOptions:
        tr_cfg = Config.SETTINGS.transcription
        model_cfg = Config.SETTINGS.model["transcription_model"]
        tl_cfg = Config.SETTINGS.translation

        ov = dict(overrides or {})
        ov_src_lang = str(ov.get("source_language") or "").strip().lower()
        ov_tgt_lang = str(ov.get("target_language") or "").strip().lower()
        ov_translate_after = ov.get("translate_after_transcription")

        output_mode_ids = list(tr_cfg["output_formats"] or [])
        output_modes = [Config.get_transcription_output_mode(str(mode_id)) for mode_id in output_mode_ids]
        want_timestamps = any(
            bool(mode.get("timestamps", False)) or str(mode.get("ext", "")).strip().lower() == "srt"
            for mode in output_modes
        )

        translate_requested = bool(ov_translate_after) if ov_translate_after is not None else bool(tr_cfg["translate_after_transcription"])
        tgt_lang = Config.normalize_policy_value(ov_tgt_lang or tl_cfg["target_language"] or Config.LANGUAGE_AUTO_VALUE)
        want_translate = bool(translate_requested and tgt_lang and not Config.is_auto_language_value(tgt_lang))

        default_lang = Config.normalize_policy_value(
            ov_src_lang
            or model_cfg["default_language"]
            or tl_cfg["source_language"]
            or Config.LANGUAGE_AUTO_VALUE
        )

        return _SessionOptions(
            output_mode_ids=output_mode_ids,
            translate_requested=translate_requested,
            want_translate=want_translate,
            want_timestamps=want_timestamps,
            tgt_lang=tgt_lang,
            default_lang=default_lang,
            chunk_len_s=int(model_cfg["chunk_length_s"]),
            stride_len_s=int(model_cfg["stride_length_s"]),
            ignore_warning=bool(model_cfg["ignore_warning"]),
        )

    def _count_session_sources(self, *, entries: List[SourceEntry]) -> Tuple[int, int]:
        local_count = 0
        url_count = 0
        for entry in entries:
            key = self._entry_source_key(entry)
            if is_url_source(key):
                url_count += 1
            else:
                local_count += 1
        return local_count, url_count

    def _register_session_tracker(self, *, tracker: _ProgressTracker, entries: List[SourceEntry], want_translate: bool) -> None:
        for entry in entries:
            key = self._entry_source_key(entry)
            tracker.register(
                key,
                has_download=is_url_source(key),
                has_translate=want_translate,
                weight=self._estimate_item_weight(key),
            )

    def _materialize_work_items(
        self,
        *,
        entries: List[SourceEntry],
        cancel_check: CancelCheckFn,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        item_error: ItemErrorFn,
        tracker: _ProgressTracker,
        downloaded_to_delete: Set[Path],
        session_id: str,
    ) -> _MaterializeBatchResult:
        work: List[WorkItem] = []
        had_errors = False
        was_cancelled = False

        for entry in entries:
            if cancel_check():
                was_cancelled = True
                break
            try:
                work.append(
                    self._materialize_entry(
                        entry=entry,
                        cancel_check=cancel_check,
                        item_status=item_status,
                        item_progress=item_progress,
                        item_path_update=item_path_update,
                        tracker=tracker,
                        downloaded_to_delete=downloaded_to_delete,
                    )
                )
            except OperationCancelled:
                was_cancelled = True
                break
            except Exception as ex:
                had_errors = True
                key = self._entry_source_key(entry)
                _LOG.debug(
                    "Transcription materialize failed. session_id=%s source_key=%s detail=%s",
                    session_id,
                    _debug_source_key(key),
                    str(ex),
                )
                item_error(key, "error.generic", {"detail": str(ex)})

        return _MaterializeBatchResult(work=work, had_errors=had_errors, was_cancelled=was_cancelled)

    # ----- Session execution -----

    def run_session(
        self,
        *,
        pipe: Any,
        entries: List[SourceEntry],
        overrides: Optional[Dict[str, Any]] = None,
        progress: ProgressFn,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        transcript_ready: TranscriptReadyFn,
        item_error: ItemErrorFn,
        item_output_dir: ItemOutputDirFn,
        conflict_resolver: ConflictResolverFn,
        cancel_check: CancelCheckFn,
    ) -> SessionResult:
        session_dir = FileManager.plan_session()
        processed_any = False
        had_errors = False
        was_cancelled = False

        options = self._build_session_options(overrides=overrides)
        downloaded_to_delete: Set[Path] = set()
        tracker = _ProgressTracker(progress)

        entries = list(entries or [])
        session_id = Path(session_dir).name
        local_count, url_count = self._count_session_sources(entries=entries)

        _LOG.info("Transcription session started. items=%d", len(entries))
        _LOG.debug(
            "Transcription session planned. session_id=%s items=%s local_count=%s url_count=%s translate_requested=%s translate_effective=%s source_language=%s target_language=%s output_modes=%s",
            session_id,
            len(entries),
            local_count,
            url_count,
            bool(options.translate_requested),
            bool(options.want_translate),
            options.default_lang or Config.LANGUAGE_AUTO_VALUE,
            options.tgt_lang or Config.LANGUAGE_AUTO_VALUE,
            ",".join(options.output_mode_ids),
        )

        self._register_session_tracker(
            tracker=tracker,
            entries=entries,
            want_translate=options.want_translate,
        )

        materialized = self._materialize_work_items(
            entries=entries,
            cancel_check=cancel_check,
            item_status=item_status,
            item_progress=item_progress,
            item_path_update=item_path_update,
            item_error=item_error,
            tracker=tracker,
            downloaded_to_delete=downloaded_to_delete,
            session_id=session_id,
        )
        had_errors = bool(materialized.had_errors)
        was_cancelled = bool(materialized.was_cancelled)

        if was_cancelled or not materialized.work:
            _LOG.debug(
                "Transcription session ended early. session_id=%s cancelled=%s work_items=%s errors=%s",
                session_id,
                bool(was_cancelled),
                len(materialized.work),
                bool(had_errors),
            )
            self._cleanup_downloaded_sources(downloaded_to_delete=downloaded_to_delete)
            FileManager.rollback_session_if_empty()
            FileManager.end_session()
            return SessionResult(str(session_dir), False, had_errors, was_cancelled)

        apply_all: Optional[Tuple[str, str]] = None
        for key, src_path, forced_stem in materialized.work:
            item_result = self._process_work_item(
                session_id=session_id,
                pipe=pipe,
                key=key,
                src_path=src_path,
                forced_stem=forced_stem,
                apply_all=apply_all,
                options=options,
                conflict_resolver=conflict_resolver,
                tracker=tracker,
                item_status=item_status,
                item_progress=item_progress,
                transcript_ready=transcript_ready,
                item_error=item_error,
                item_output_dir=item_output_dir,
                cancel_check=cancel_check,
            )
            processed_any = bool(processed_any or item_result.processed_any)
            had_errors = bool(had_errors or item_result.had_errors)
            apply_all = item_result.apply_all
            if item_result.was_cancelled:
                was_cancelled = True
                break

        self._cleanup_downloaded_sources(downloaded_to_delete=downloaded_to_delete)

        if not processed_any:
            FileManager.rollback_session_if_empty()

        FileManager.end_session()
        _LOG.info(
            "Transcription session finished. processed=%s errors=%s cancelled=%s",
            processed_any,
            had_errors,
            was_cancelled,
        )
        _LOG.debug(
            "Transcription session summary. session_id=%s processed=%s errors=%s cancelled=%s cleanup_downloads=%s",
            session_id,
            bool(processed_any),
            bool(had_errors),
            bool(was_cancelled),
            len(downloaded_to_delete),
        )
        return SessionResult(str(session_dir), processed_any, had_errors, was_cancelled)

    def _process_work_item(
        self,
        *,
        session_id: str,
        pipe: Any,
        key: str,
        src_path: Path,
        forced_stem: Optional[str],
        apply_all: Optional[Tuple[str, str]],
        options: _SessionOptions,
        conflict_resolver: ConflictResolverFn,
        tracker: _ProgressTracker,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        transcript_ready: TranscriptReadyFn,
        item_error: ItemErrorFn,
        item_output_dir: ItemOutputDirFn,
        cancel_check: CancelCheckFn,
    ) -> _ItemProcessResult:
        if cancel_check():
            return _ItemProcessResult(False, False, True, apply_all)

        stem = sanitize_filename(forced_stem or src_path.stem)
        resolved = self._resolve_output_dir(
            stem=stem,
            conflict_resolver=conflict_resolver,
            apply_all=apply_all,
        )
        if resolved is None:
            _LOG.debug(
                "Transcription output conflict resolved. session_id=%s source_key=%s action=skip",
                session_id,
                _debug_source_key(key),
            )
            item_status(key, "status.skipped")
            tracker.mark_done(key)
            return _ItemProcessResult(False, False, False, apply_all)

        out_dir, stem, apply_all = resolved
        _LOG.debug(
            "Transcription output directory resolved. session_id=%s source_key=%s out_dir=%s stem=%s",
            session_id,
            _debug_source_key(key),
            Path(out_dir).name,
            stem,
        )
        item_output_dir(key, str(out_dir))

        tmp_wav: Optional[Path] = None
        try:
            item_status(key, "status.processing")
            tracker.update(key, "preprocess", 0)
            preprocess_started = time.perf_counter()
            tmp_wav = FileManager.ensure_tmp_wav(src_path, cancel_check=cancel_check)
            tracker.update(key, "preprocess", 100)

            with wave.open(str(tmp_wav), "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                dur_s = (float(frames) / float(rate)) if rate > 0 else 0.0

            tracker.set_weight(key, weight=float(max(15.0, min(3600.0, dur_s))))
            _LOG.debug(
                "Transcription stage finished. session_id=%s source_key=%s stage=preprocess duration_ms=%s tmp_name=%s duration_s=%s",
                session_id,
                _debug_source_key(key),
                int((time.perf_counter() - preprocess_started) * 1000.0),
                tmp_wav.name if tmp_wav is not None else "",
                round(dur_s, 2),
            )

            item_status(key, "status.transcribing")
            transcribe_started = time.perf_counter()
            merged_text, segments, detected_lang = self._transcribe_wav(
                pipe=pipe,
                wav_path=tmp_wav,
                key=key,
                chunk_len_s=options.chunk_len_s,
                stride_len_s=options.stride_len_s,
                want_timestamps=options.want_timestamps,
                ignore_warning=options.ignore_warning,
                tracker=tracker,
                item_progress=item_progress,
                cancel_check=cancel_check,
                require_language=options.want_translate,
            )
            _LOG.debug(
                "Transcription stage finished. session_id=%s source_key=%s stage=transcribe duration_ms=%s text_chars=%s segments=%s detected_lang=%s",
                session_id,
                _debug_source_key(key),
                int((time.perf_counter() - transcribe_started) * 1000.0),
                len(merged_text),
                len(segments),
                detected_lang or "",
            )

            translated_text, translated_segments, translate_had_errors = self._translate_item_if_needed(
                session_id=session_id,
                key=key,
                merged_text=merged_text,
                segments=segments,
                detected_lang=detected_lang,
                options=options,
                tracker=tracker,
                item_status=item_status,
                item_progress=item_progress,
                item_error=item_error,
                cancel_check=cancel_check,
            )

            item_status(key, "status.saving")
            save_started = time.perf_counter()
            primary = self._write_outputs(
                key=key,
                stem=stem,
                out_dir=Path(out_dir),
                merged_text=merged_text,
                translated_text=translated_text,
                translated_segments=translated_segments,
                segments=segments,
                output_mode_ids=options.output_mode_ids,
                transcript_ready=transcript_ready,
                item_output_dir_cb=item_output_dir,
                item_error_cb=item_error,
                cancel_check=cancel_check,
            )
            tracker.update(key, "save", 100)
            tracker.mark_done(key)
            _LOG.debug(
                "Transcription stage finished. session_id=%s source_key=%s stage=save duration_ms=%s output_dir=%s primary_saved=%s",
                session_id,
                _debug_source_key(key),
                int((time.perf_counter() - save_started) * 1000.0),
                Path(out_dir).name,
                bool(primary is not None),
            )

            if primary is not None:
                item_status(key, "status.done")
                return _ItemProcessResult(True, bool(translate_had_errors), False, apply_all)

            item_status(key, "status.error")
            return _ItemProcessResult(False, True, False, apply_all)
        except OperationCancelled:
            return _ItemProcessResult(False, False, True, apply_all)
        except Exception as ex:
            err_key = getattr(ex, "key", None)
            err_params = getattr(ex, "params", None)
            if err_key:
                item_error(key, str(err_key), dict(err_params or {}))
            else:
                item_error(key, "error.generic", {"detail": str(ex)})
            item_status(key, "status.error")
            return _ItemProcessResult(False, True, False, apply_all)
        finally:
            self._cleanup_tmp_wav(tmp_wav=tmp_wav, src_path=src_path)

    def _translate_item_if_needed(
        self,
        *,
        session_id: str,
        key: str,
        merged_text: str,
        segments: List[Dict[str, Any]],
        detected_lang: str,
        options: _SessionOptions,
        tracker: _ProgressTracker,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_error: ItemErrorFn,
        cancel_check: CancelCheckFn,
    ) -> Tuple[str, Optional[List[Dict[str, Any]]], bool]:
        translated_text = ""
        translated_segments: Optional[List[Dict[str, Any]]] = None
        had_errors = False

        if not options.want_translate:
            tracker.update(key, "translate", 100)
            item_progress(key, 100)
            return translated_text, translated_segments, had_errors

        item_status(key, "status.translating")
        translate_started = time.perf_counter()
        src_lang = self._pick_source_language(default_lang=options.default_lang, detected_lang=detected_lang)
        _LOG.debug(
            "Translation source language resolved. session_id=%s source_key=%s default_lang=%s detected_lang=%s resolved=%s",
            session_id,
            _debug_source_key(key),
            options.default_lang or "",
            detected_lang or "",
            src_lang or "",
        )

        if not src_lang:
            had_errors = True
            item_error(key, "error.translation.missing_source_language", {})
        else:
            try:
                translated_text = self._translator.translate(
                    merged_text,
                    src_lang=src_lang,
                    tgt_lang=options.tgt_lang,
                    log=None,
                )
            except AppError as ex:
                had_errors = True
                item_error(key, str(getattr(ex, "key", "error.generic")), dict(getattr(ex, "params", {}) or {}))
                translated_text = ""
            else:
                if options.want_timestamps and segments:
                    translated_segments = self._translate_segments(
                        segments=segments,
                        src_lang=src_lang,
                        tgt_lang=options.tgt_lang,
                        cancel_check=cancel_check,
                        progress_cb=lambda pct: (tracker.update(key, "translate", pct), item_progress(key, int(pct))),
                    )

        tracker.update(key, "translate", 100)
        item_progress(key, 100)
        _LOG.debug(
            "Transcription stage finished. session_id=%s source_key=%s stage=translate duration_ms=%s text_chars=%s segments=%s",
            session_id,
            _debug_source_key(key),
            int((time.perf_counter() - translate_started) * 1000.0),
            len(translated_text),
            len(translated_segments or []),
        )
        return translated_text, translated_segments, had_errors

    @staticmethod
    def _cleanup_tmp_wav(*, tmp_wav: Optional[Path], src_path: Path) -> None:
        try:
            if tmp_wav is not None and tmp_wav != src_path and tmp_wav.suffix.lower() == ".wav":
                tmp_wav.unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _cleanup_downloaded_sources(*, downloaded_to_delete: Set[Path]) -> None:
        for path in downloaded_to_delete:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

            try:
                parent = path.parent
                tmp_root = Config.DOWNLOADS_TMP_DIR.resolve()
                if parent != tmp_root and tmp_root in parent.resolve().parents:
                    shutil.rmtree(parent, ignore_errors=True)
            except Exception:
                pass

    # ----- Cleanup / materialization -----

    def _resolve_output_dir(
        self,
        *,
        stem: str,
        conflict_resolver: ConflictResolverFn,
        apply_all: Optional[Tuple[str, str]],
    ) -> Optional[Tuple[str, str, Optional[Tuple[str, str]]]]:
        stem = sanitize_filename(stem)
        existing = OutputResolver.existing_dir(stem)

        if not existing:
            out_dir = FileManager.ensure_output(stem)
            return str(out_dir), stem, apply_all

        if apply_all is None:
            action, new_stem, set_all = conflict_resolver(stem, str(existing))
            action = str(action or "skip").strip().lower()
            new_stem = sanitize_filename(new_stem or "")
            if set_all:
                apply_all = (action, new_stem)
        else:
            action, new_stem = apply_all

        if action == "skip":
            return None

        if action == "overwrite":
            FileManager.delete_output_dir(Path(existing))
            out_dir = FileManager.ensure_output(stem)
            return str(out_dir), stem, apply_all

        if action == "new":
            candidate = sanitize_filename(new_stem or f"{stem}-copy")
            out_dir = FileManager.ensure_output(candidate)
            return str(out_dir), candidate, apply_all

        return None

    def _materialize_entry(
        self,
        *,
        entry: SourceEntry,
        cancel_check: CancelCheckFn,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        tracker: _ProgressTracker,
        downloaded_to_delete: Set[Path],
    ) -> WorkItem:
        if isinstance(entry, dict):
            src = str(entry.get("src") or "")
            stem = str(entry.get("stem") or "").strip() or None
            audio_lang = str(entry.get("audio_lang") or "").strip() or None
            if (audio_lang or "").lower() in set(Config.DOWNLOAD_AUDIO_LANG_AUTO_VALUES):
                audio_lang = None
        else:
            src = str(entry or "")
            stem = None
            audio_lang = None

        if is_url_source(src):
            return self._materialize_url(
                url=src,
                forced_stem=stem,
                audio_lang=audio_lang,
                cancel_check=cancel_check,
                item_status=item_status,
                item_progress=item_progress,
                item_path_update=item_path_update,
                tracker=tracker,
                downloaded_to_delete=downloaded_to_delete,
            )

        p = Path(src).expanduser()
        if not p.exists():
            raise TranscriptionError("error.input.file_not_found", path=str(p))
        return src, p, stem

    def _materialize_url(
        self,
        *,
        url: str,
        forced_stem: Optional[str],
        audio_lang: Optional[str],
        cancel_check: CancelCheckFn,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        tracker: _ProgressTracker,
        downloaded_to_delete: Set[Path],
    ) -> WorkItem:
        old_key = str(url)
        safe_url = sanitize_url_for_log(url)

        item_status(old_key, "status.processing")
        download_started = time.perf_counter()
        meta = self._download.probe(url)
        dur = meta.get("duration")
        if isinstance(dur, (int, float)) and dur > 0:
            tracker.set_weight(old_key, weight=max(15.0, min(3600.0, float(dur))))

        title = sanitize_filename(str(meta.get("title") or "").strip())
        stem = forced_stem or title or Config.DOWNLOAD_DEFAULT_STEM

        tr_cfg = Config.SETTINGS.transcription
        download_audio_only = bool(tr_cfg["download_audio_only"])
        quality = Config.URL_DOWNLOAD_DEFAULT_QUALITY
        if download_audio_only:
            kind = "audio"
            ext = str(tr_cfg.get("url_audio_ext") or "")
            keep = bool(tr_cfg.get("url_keep_audio"))
        else:
            kind = "video"
            ext = str(tr_cfg.get("url_video_ext") or "")
            keep = bool(tr_cfg.get("url_keep_video"))

        _LOG.debug("Transcription URL materialization started. source_key=%s kind=%s ext=%s keep_download=%s audio_lang=%s", safe_url, kind, ext, bool(keep), audio_lang or "")

        def on_dl(pct: int, _stage: str = "") -> None:
            tracker.update(old_key, "download", pct)
            item_progress(old_key, pct)

        item_status(old_key, "status.downloading")
        out_dir = FileManager.downloads_dir() if keep else FileManager.url_tmp_dir()
        dst = self._download.download(
            url=url,
            kind=kind,
            quality=quality,
            ext=ext,
            out_dir=out_dir,
            progress_cb=on_dl,
            audio_lang=audio_lang,
            file_stem=stem,
            cancel_check=cancel_check,
            purpose=Config.DOWNLOAD_PURPOSE_TRANSCRIPTION,
            keep_output=keep,
            meta=meta,
        )
        if not dst:
            raise AppError(key="error.down.download_failed", params={"detail": "download returned no file path"})
        if not keep:
            downloaded_to_delete.add(dst)

        new_key = str(dst)
        item_path_update(old_key, new_key)
        tracker.rename_key(old_key, new_key)
        tracker.update(new_key, "download", 100)
        item_progress(new_key, 100)
        _LOG.debug("Transcription URL materialization finished. source_key=%s new_key=%s duration_ms=%s", safe_url, _debug_source_key(new_key), int((time.perf_counter() - download_started) * 1000.0))
        return new_key, dst, stem

    # ----- Transcription / translation -----

    def _transcribe_wav(
        self,
        *,
        pipe: Any,
        wav_path: Path,
        key: str,
        chunk_len_s: int,
        stride_len_s: int,
        want_timestamps: bool,
        ignore_warning: bool,
        tracker: _ProgressTracker,
        item_progress: ItemProgressFn,
        cancel_check: CancelCheckFn,
        require_language: bool,
    ) -> Tuple[str, List[Dict[str, Any]], str]:
        with wave.open(str(wav_path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            dur_s = 0.0 if rate <= 0 else float(frames) / float(rate)
        chunk_len_s, stride_len_s, step_s = normalize_chunk_params(chunk_len_s, stride_len_s)
        n_chunks = estimate_chunks(dur_s, chunk_len_s, stride_len_s)
        _LOG.debug("Transcription wav started. source_key=%s duration_s=%s chunks=%s require_language=%s timestamps=%s", _debug_source_key(key), round(dur_s, 2), n_chunks, bool(require_language), bool(want_timestamps))

        merged_parts: List[str] = []
        segments: List[Dict[str, Any]] = []
        detected_lang = ""

        for i, ch in enumerate(iter_wav_mono_chunks(wav_path, chunk_len_s=chunk_len_s, stride_len_s=stride_len_s), start=1):
            if cancel_check():
                raise OperationCancelled()

            if n_chunks <= 1 and i == 1:
                tracker.update(key, "transcribe", 5)
                item_progress(key, 5)

            out = self._pipe_call(pipe=pipe, audio=ch.audio, sr=ch.sr, ignore_warning=ignore_warning, require_language=require_language)

            if not detected_lang:
                detected_lang = _extract_detected_language_from_result(out)

            text = self._post.plain_from_result(out)
            if text:
                merged_parts.append(text)

            if want_timestamps:
                segments.extend(self._extract_segments(out, offset_s=ch.offset_s))

            pct = int(round((i / float(n_chunks)) * 100))
            if n_chunks <= 1:
                pct = min(95, max(0, pct))
            tracker.update(key, "transcribe", pct)
            item_progress(key, pct)

        merged_text = "\n".join([p for p in merged_parts if p]).strip()
        if not merged_text and not bool(Config.SETTINGS.model["transcription_model"]["ignore_warning"]):
            raise TranscriptionError("error.transcription.empty_result")

        tracker.update(key, "transcribe", 100)
        _LOG.debug("Transcription wav finished. source_key=%s text_chars=%s segments=%s detected_lang=%s", _debug_source_key(key), len(merged_text), len(segments), detected_lang or "")
        return merged_text, segments, detected_lang

    def _pipe_call(self, *, pipe: Any, audio: Any, sr: int, ignore_warning: bool, require_language: bool) -> Dict[str, Any]:
        try:
            payload = {"array": audio, "sampling_rate": int(sr)}
            try:
                out = pipe(
                    payload,
                    return_language=True,
                    return_timestamps=True,
                    ignore_warning=bool(ignore_warning),
                )
            except TypeError as ex:
                msg = str(ex)
                if "return_language" in msg and bool(require_language):
                    raise TranscriptionError("error.transcription.language_detection_unsupported") from ex
                if "return_timestamps" in msg:
                    raise TranscriptionError("error.transcription.timestamps_unsupported") from ex
                out = pipe(
                    payload,
                    return_timestamps=True,
                    ignore_warning=bool(ignore_warning),
                )
        except Exception as exc:
            _LOG.exception("ASR pipeline call failed.")
            raise TranscriptionError("error.transcription.asr_failed") from exc

        if not isinstance(out, dict):
            out = {"text": str(out)}

        if bool(require_language):
            lang = _extract_detected_language_from_result(out)
            if not lang:
                lang = _detect_language_from_pipe_runtime(pipe=pipe, audio=audio, sr=sr)
                if lang:
                    out["language"] = lang
            if not lang:
                raise TranscriptionError("error.transcription.language_detection_failed")

        return out

    def _pick_source_language(self, *, default_lang: Optional[str], detected_lang: str) -> str:
        src = str(default_lang or "").strip().lower().replace("_", "-")
        src = src.split("-", 1)[0]
        if src and not Config.is_auto_language_value(src):
            return src
        return _normalize_detected_language(detected_lang)

    @staticmethod
    def _extract_segments(result: Dict[str, Any], *, offset_s: float) -> List[Dict[str, Any]]:
        raw = TextPostprocessor.segments_from_result(result)
        if not raw:
            return []
        out: List[Dict[str, Any]] = []
        for seg in raw:
            try:
                start = float(seg.get("start", 0.0) or 0.0) + float(offset_s)
            except Exception:
                start = float(offset_s)
            try:
                end = float(seg.get("end", start) or start) + float(offset_s)
            except Exception:
                end = start
            out.append(
                {
                    "start": start,
                    "end": end,
                    "text": str(seg.get("text") or "").strip(),
                }
            )
        return out

    def _translate_segments(
        self,
        *,
        segments: List[Dict[str, Any]],
        src_lang: str,
        tgt_lang: str,
        cancel_check: CancelCheckFn,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        total = max(1, len(segments))
        out: List[Dict[str, Any]] = []

        for i, seg in enumerate(segments, start=1):
            if cancel_check():
                raise OperationCancelled()
            text = str(seg.get("text") or "")
            try:
                translated = self._translator.translate(text, src_lang=src_lang, tgt_lang=tgt_lang, log=None)
            except AppError:
                return None
            if not translated:
                return None
            out.append({"start": seg["start"], "end": seg["end"], "text": translated})

            if progress_cb is not None:
                progress_cb(int(round((i / float(total)) * 100)))

        return out

    # ----- Output rendering / writeback -----

    def _render_transcript(
        self,
        *,
        merged_text: str,
        translated_text: str,
        translated_segments: Optional[List[Dict[str, Any]]],
        segments: List[Dict[str, Any]],
        mode: Dict[str, Any],
    ) -> str:
        """Render a single transcript output for a selected output mode."""
        out_ext = str(mode.get("ext", Config.TRANSCRIPT_DEFAULT_EXT) or Config.TRANSCRIPT_DEFAULT_EXT).strip().lower().lstrip(".")
        if not out_ext:
            out_ext = Config.TRANSCRIPT_DEFAULT_EXT
        timestamps_output = bool(mode.get("timestamps", False))

        if out_ext not in ("txt", "srt", "sub"):
            out_ext = Config.TRANSCRIPT_DEFAULT_EXT

        if out_ext == "srt":
            use = translated_segments if translated_segments else segments
            return TextPostprocessor.to_srt(use)

        if out_ext == Config.TRANSCRIPT_DEFAULT_EXT and timestamps_output:
            use = translated_segments if translated_segments else segments
            return TextPostprocessor.to_timestamped_plain(use)

        if translated_text and translated_text.strip():
            return TextPostprocessor.clean(translated_text)

        merged = TextPostprocessor.clean(merged_text)
        if merged:
            return merged

        use = translated_segments if translated_segments else segments
        return TextPostprocessor.to_plain(use)

    def _write_outputs(
        self,
        *,
        key: str,
        stem: str,
        out_dir: Path,
        merged_text: str,
        translated_text: str,
        translated_segments: Optional[List[Dict[str, Any]]],
        segments: List[Dict[str, Any]],
        output_mode_ids: List[str],
        transcript_ready: TranscriptReadyFn,
        item_output_dir_cb: Optional[ItemOutputDirFn],
        item_error_cb: Optional[ItemErrorFn],
        cancel_check: CancelCheckFn,
    ) -> Optional[Path]:
        if cancel_check():
            raise OperationCancelled()

        primary_path: Optional[Path] = None
        total_modes = max(1, len(output_mode_ids))

        for i, mode_id in enumerate(output_mode_ids):
            mode = Config.get_transcription_output_mode(str(mode_id))
            out_text = self._render_transcript(
                merged_text=merged_text,
                translated_text=translated_text,
                translated_segments=translated_segments,
                segments=segments,
                mode=mode,
            )

            filename = FileManager.transcript_filename(str(mode_id))
            out_path = out_dir / filename
            out_path = FileManager.ensure_unique_path(out_path)

            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path.write_text(out_text, encoding="utf-8")
                _LOG.debug("Transcript output saved. source_key=%s mode=%s file_name=%s", _debug_source_key(key), mode_id, out_path.name)
            except Exception as e:
                _LOG.error("Transcript save failed. name=%s detail=%s", stem, str(e))
                if item_error_cb is not None:
                    item_error_cb(key, "error.transcription.save_failed", {"name": stem, "detail": str(e)})
                return None

            if primary_path is None:
                primary_path = out_path
                transcript_ready(key, str(out_path))
                if item_output_dir_cb is not None:
                    item_output_dir_cb(key, str(out_dir))

        return primary_path
