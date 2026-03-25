# app/model/helpers/transcription_runtime.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import torch

from app.model.io.media_probe import is_url_source
from app.model.helpers.string_utils import sanitize_url_for_log

SIGNAL_NONE = "none"
SIGNAL_WEAK = "weak"
SIGNAL_SOLID = "solid"

_MERGE_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def audio_rms_level(audio: Any) -> float:
    """Return RMS level for a mono audio buffer."""
    try:
        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return 0.0
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(arr), dtype=np.float64)))


def _audio_signal_profile(audio: Any, *, sr: int, floor: float) -> tuple[float, float, float]:
    try:
        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0
    if arr.size == 0:
        return 0.0, 0.0, 0.0

    abs_arr = np.abs(arr, dtype=np.float32)
    active = abs_arr >= max(0.0, float(floor))
    active_ratio = float(np.mean(active, dtype=np.float64))
    active_ms = 0.0 if sr <= 0 else (float(np.count_nonzero(active)) / float(sr)) * 1000.0
    return audio_rms_level(arr), active_ratio, active_ms


def audio_has_meaningful_signal(
    audio: Any,
    *,
    sr: int,
    rms_min: float,
    activity_floor: float,
    active_ratio_min: float,
    active_ms_min: float,
) -> bool:
    """Return True when the audio crosses the configured activity thresholds."""
    rms, active_ratio, active_ms = _audio_signal_profile(audio, sr=sr, floor=activity_floor)
    return bool(
        rms >= float(rms_min)
        and active_ratio >= float(active_ratio_min)
        and active_ms >= float(active_ms_min)
    )


def classify_audio_signal(audio: Any, *, sr: int, profile: dict[str, Any]) -> str:
    """Classify an audio chunk into none/weak/solid using the resolved runtime profile."""
    if not audio_has_meaningful_signal(
        audio,
        sr=sr,
        rms_min=float(profile.get("weak_rms_threshold", 0.0115)),
        activity_floor=float(profile.get("weak_activity_floor", 0.0055)),
        active_ratio_min=float(profile.get("weak_active_ratio_threshold", 0.012)),
        active_ms_min=float(profile.get("weak_active_ms_threshold", 55.0)),
    ):
        return SIGNAL_NONE

    if audio_has_meaningful_signal(
        audio,
        sr=sr,
        rms_min=float(profile.get("solid_rms_threshold", 0.0145)),
        activity_floor=float(profile.get("solid_activity_floor", 0.0065)),
        active_ratio_min=float(profile.get("solid_active_ratio_threshold", 0.022)),
        active_ms_min=float(profile.get("solid_active_ms_threshold", 85.0)),
    ):
        return SIGNAL_SOLID
    return SIGNAL_WEAK


def can_detect_language_from_audio(audio: Any, *, sr: int, signal_kind: str, profile: dict[str, Any]) -> bool:
    """Return True when the chunk is reliable enough for language detection."""
    if signal_kind == SIGNAL_SOLID:
        return True
    if signal_kind == SIGNAL_WEAK and not bool(profile.get("allow_weak_language_detection", False)):
        return False
    return audio_has_meaningful_signal(
        audio,
        sr=sr,
        rms_min=float(profile.get("language_detect_rms_threshold", 0.03)),
        activity_floor=float(profile.get("language_detect_activity_floor", 0.009)),
        active_ratio_min=float(profile.get("language_detect_active_ratio_threshold", 0.07)),
        active_ms_min=float(profile.get("language_detect_active_ms_threshold", 160.0)),
    )


def should_accept_detected_language(*, signal_kind: str, profile: dict[str, Any]) -> bool:
    """Return True when language evidence from the current chunk may update the stable language state."""
    return signal_kind == SIGNAL_SOLID or bool(profile.get("allow_weak_language_detection", False))


def should_use_prompt(*, signal_kind: str, profile: dict[str, Any]) -> bool:
    """Return True when the current chunk should reuse prior context as prompt text."""
    if not bool(profile.get("use_prompt", True)):
        return False
    if signal_kind == SIGNAL_WEAK and not bool(profile.get("prompt_on_weak_signal", False)):
        return False
    return signal_kind != SIGNAL_NONE


def build_whisper_generate_kwargs(
    *,
    profile: dict[str, Any],
    source_language: str = "",
    prompt_ids: Any = None,
    signal_kind: str = SIGNAL_SOLID,
) -> dict[str, Any]:
    """Build Whisper decoding kwargs from the resolved runtime profile."""
    kwargs: dict[str, Any] = {"task": "transcribe"}
    if source_language:
        kwargs["language"] = str(source_language).strip().lower()

    condition_on_prev_tokens = bool(profile.get("condition_on_prev_tokens", True))
    if signal_kind == SIGNAL_WEAK and not bool(profile.get("prompt_on_weak_signal", False)):
        condition_on_prev_tokens = False
    kwargs["condition_on_prev_tokens"] = condition_on_prev_tokens

    if prompt_ids is not None and should_use_prompt(signal_kind=signal_kind, profile=profile):
        kwargs["prompt_ids"] = prompt_ids

    for key in ("whisper_no_speech_threshold", "whisper_logprob_threshold", "whisper_compression_ratio_threshold"):
        value = profile.get(key)
        if value in (None, ""):
            continue
        mapped = {
            "whisper_no_speech_threshold": "no_speech_threshold",
            "whisper_logprob_threshold": "logprob_threshold",
            "whisper_compression_ratio_threshold": "compression_ratio_threshold",
        }[key]
        try:
            kwargs[mapped] = float(value)
        except (TypeError, ValueError):
            continue

    temperatures = profile.get("whisper_temperatures")
    if isinstance(temperatures, (list, tuple)):
        normalized_temperatures: list[float] = []
        for item in temperatures:
            try:
                normalized_temperatures.append(float(item))
            except (TypeError, ValueError):
                continue
        if normalized_temperatures:
            kwargs["temperature"] = tuple(normalized_temperatures)
    elif temperatures not in (None, ""):
        try:
            kwargs["temperature"] = float(temperatures)
        except (TypeError, ValueError):
            pass
    return kwargs


def _normalized_merge_tokens(text: str) -> list[str]:
    return [tok for tok in _MERGE_TOKEN_RE.findall(str(text or "").lower()) if tok]


def word_count(text: str) -> int:
    return len(_normalized_merge_tokens(text))


def has_terminal_punctuation(text: str) -> bool:
    return str(text or "").rstrip().endswith((".", "!", "?", ";", ":", "...", "\N{HORIZONTAL ELLIPSIS}"))


def _shared_prefix_token_count(left: str, right: str) -> int:
    left_tokens = _normalized_merge_tokens(left)
    right_tokens = _normalized_merge_tokens(right)
    limit = min(len(left_tokens), len(right_tokens))
    idx = 0
    while idx < limit and left_tokens[idx] == right_tokens[idx]:
        idx += 1
    return idx


def relates_to_reference_texts(text: str, references: Iterable[str], *, prefix_ratio: float = 0.62) -> bool:
    current = str(text or "").strip()
    if not current:
        return False
    current_tokens = _normalized_merge_tokens(current)
    if not current_tokens:
        return False
    for ref in references:
        reference = str(ref or "").strip()
        if not reference:
            continue
        ref_tokens = _normalized_merge_tokens(reference)
        if not ref_tokens:
            continue
        shorter_len = min(len(ref_tokens), len(current_tokens))
        if shorter_len <= 0:
            continue
        shared_prefix = _shared_prefix_token_count(reference, current)
        if shared_prefix == shorter_len:
            return True
        min_prefix = 3 if shorter_len >= 4 else max(1, shorter_len - 1)
        if shared_prefix >= min_prefix and shared_prefix >= int(shorter_len * max(0.5, float(prefix_ratio))):
            return True
        reverse_prefix = _shared_prefix_token_count(current, reference)
        if reverse_prefix == shorter_len:
            return True
        if reverse_prefix >= min_prefix and reverse_prefix >= int(shorter_len * max(0.5, float(prefix_ratio))):
            return True
    return False


def filter_asr_text(
    text: str,
    *,
    clean_fn,
    signal_kind: str,
    profile: dict[str, Any],
    reference_texts: Iterable[str] = (),
    from_tail: bool = False,
) -> str:
    """Filter short ASR artifacts while keeping plausible continuations."""
    text = clean_fn(str(text or ""))
    if not text:
        return ""

    words = word_count(text)
    if words <= 0:
        return ""

    relates = relates_to_reference_texts(
        text,
        reference_texts,
        prefix_ratio=float(profile.get("stream_replace_prefix_ratio", 0.62)),
    )

    if len(text) < int(profile.get("artifact_min_chars", 3)) and not relates:
        return ""

    if words < int(profile.get("artifact_min_words", 2)) and signal_kind == SIGNAL_WEAK and not relates:
        return ""

    if signal_kind == SIGNAL_WEAK or from_tail:
        if words <= 1 and not relates:
            return ""
        if (
            words <= int(profile.get("artifact_tail_max_words", 2))
            and len(text) <= int(profile.get("artifact_tail_max_chars", 14))
            and (not has_terminal_punctuation(text))
            and (not relates)
        ):
            return ""

    return text


def normalize_detected_language(lang: str) -> str:
    """Normalize detected language labels to a stable short code."""
    lang = str(lang or "").strip().lower().replace("_", "-")
    lang = lang.split("-", 1)[0]
    try:
        from transformers.models.whisper import tokenization_whisper

        languages = getattr(tokenization_whisper, "LANGUAGES", {})
        inv = {str(value).lower(): str(key) for key, value in dict(languages).items()}
        return inv.get(lang, lang)
    except (ImportError, AttributeError, TypeError, ValueError):
        return lang


def _normalize_whisper_language_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if key.startswith("<|") and key.endswith("|>"):
        key = key[2:-2]
    key = key.replace("_", "-").split("-", 1)[0]
    return normalize_detected_language(key)


def extract_detected_language_from_result(out: dict[str, Any]) -> str:
    """Extract a normalized language code from ASR output payloads."""
    lang = str(out.get("language") or "").strip().lower()
    if lang:
        return normalize_detected_language(lang)

    chunks = out.get("chunks")
    if isinstance(chunks, list) and chunks:
        lang = str(chunks[0].get("language") or "").strip().lower()
        if lang:
            return normalize_detected_language(lang)

    return ""


def _resolve_whisper_runtime(pipe: Any) -> tuple[Any, Any, Any]:
    fe = getattr(pipe, "feature_extractor", None) or getattr(getattr(pipe, "processor", None), "feature_extractor", None)
    tok = getattr(pipe, "tokenizer", None) or getattr(getattr(pipe, "processor", None), "tokenizer", None)
    model = getattr(pipe, "model", None)
    return fe, tok, model


def _resolve_whisper_language_token_map(tokenizer: Any) -> dict[str, int]:
    lang_to_id = getattr(tokenizer, "lang_to_id", None)
    resolved: dict[str, int] = {}

    if isinstance(lang_to_id, dict) and lang_to_id:
        for code, token_id in lang_to_id.items():
            norm = _normalize_whisper_language_key(code)
            if not norm:
                continue
            try:
                resolved[norm] = int(token_id)
            except (TypeError, ValueError):
                continue
        if resolved:
            return resolved

    try:
        vocab = tokenizer.get_vocab()
    except (AttributeError, TypeError, ValueError):
        vocab = getattr(tokenizer, "vocab", {}) or {}

    if not isinstance(vocab, dict):
        return resolved

    for token, token_id in vocab.items():
        if not isinstance(token, str):
            continue
        norm = _normalize_whisper_language_key(token)
        if not norm:
            continue
        if not norm.isalpha() or not (2 <= len(norm) <= 5):
            continue
        try:
            resolved[norm] = int(token_id)
        except (TypeError, ValueError):
            continue
    return resolved


def _build_whisper_input_features(*, feature_extractor: Any, model: Any, audio: Any, sr: int) -> Any:
    inputs = feature_extractor(audio, sampling_rate=int(sr), return_tensors="pt")
    input_features = inputs.get("input_features")
    if input_features is None:
        return None

    device = getattr(model, "device", None)
    try:
        dtype = next(model.parameters()).dtype
    except (AttributeError, StopIteration, RuntimeError, TypeError):
        dtype = None

    if device is None and dtype is None:
        return input_features

    try:
        move_kwargs: dict[str, Any] = {}
        if device is not None:
            move_kwargs["device"] = device
        if dtype is not None and torch.is_floating_point(input_features):
            move_kwargs["dtype"] = dtype
        return input_features.to(**move_kwargs) if move_kwargs else input_features
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return input_features


def _select_language_from_logits(logits: Any, *, lang_to_id: dict[str, int]) -> str:
    if logits is None or not lang_to_id:
        return ""

    try:
        ids = torch.tensor(list(lang_to_id.values()), device=logits.device)
        if ids.numel() <= 0:
            return ""
        scores = logits.index_select(-1, ids)
        best_idx = int(torch.argmax(scores, dim=-1).item())
        best_id = int(ids[best_idx].item())
        inv = {int(token_id): str(code) for code, token_id in lang_to_id.items()}
        return inv.get(best_id, "")
    except (RuntimeError, TypeError, ValueError, IndexError):
        best_lang = ""
        best_score = None
        for code, token_id in lang_to_id.items():
            try:
                score = float(logits[0, int(token_id)].item())
            except (RuntimeError, TypeError, ValueError, IndexError):
                continue
            if best_score is None or score > best_score:
                best_lang = str(code)
                best_score = score
        return best_lang


def _detect_language_from_decoder_runtime(*, model: Any, tokenizer: Any, input_features: Any, lang_to_id: dict[str, int]) -> str:
    try:
        sot_id = tokenizer.convert_tokens_to_ids("<|startoftranscript|>")
        if sot_id is None:
            return ""
        decoder_input_ids = torch.tensor([[int(sot_id)]], device=input_features.device)
        with torch.no_grad():
            out = model(input_features=input_features, decoder_input_ids=decoder_input_ids)
            logits = getattr(out, "logits", None)
        if logits is None:
            return ""
        return _select_language_from_logits(logits[:, -1, :], lang_to_id=lang_to_id)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _detect_language_from_encoder_runtime(*, model: Any, input_features: Any, lang_to_id: dict[str, int]) -> str:
    try:
        encoder = model.get_encoder()
        proj_out = getattr(model, "proj_out", None)
        if encoder is None or proj_out is None:
            return ""
        with torch.no_grad():
            enc = encoder(input_features)
            hidden = enc.last_hidden_state if hasattr(enc, "last_hidden_state") else enc
            logits = proj_out(hidden)
        return _select_language_from_logits(logits[:, 0, :], lang_to_id=lang_to_id)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def debug_source_key(value: str) -> str:
    """Return a log-safe, user-neutral source label."""
    text = str(value or "").strip()
    if not text:
        return ""
    if is_url_source(text):
        return sanitize_url_for_log(text)
    return Path(text).name or text


def detect_language_from_pipe_runtime(*, pipe: Any, audio: Any, sr: int) -> str:
    """Detect language from Whisper logits when the pipeline output omits it."""
    try:
        fe, tok, model = _resolve_whisper_runtime(pipe)
        if fe is None or tok is None or model is None:
            return ""

        lang_to_id = _resolve_whisper_language_token_map(tok)
        if not lang_to_id:
            return ""

        input_features = _build_whisper_input_features(
            feature_extractor=fe,
            model=model,
            audio=audio,
            sr=sr,
        )
        if input_features is None:
            return ""

        detected = _detect_language_from_decoder_runtime(
            model=model,
            tokenizer=tok,
            input_features=input_features,
            lang_to_id=lang_to_id,
        )
        if not detected:
            detected = _detect_language_from_encoder_runtime(
                model=model,
                input_features=input_features,
                lang_to_id=lang_to_id,
            )
        return normalize_detected_language(detected)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _normalize_whisper_prompt_ids(prompt_ids: Any, *, pipe: Any) -> Any:
    """Return Whisper prompt ids as a rank-1 torch.LongTensor when possible."""
    if prompt_ids is None:
        return None
    try:
        if isinstance(prompt_ids, torch.Tensor):
            tensor = prompt_ids.to(dtype=torch.long)
        elif isinstance(prompt_ids, np.ndarray):
            tensor = torch.as_tensor(prompt_ids, dtype=torch.long)
        elif isinstance(prompt_ids, (list, tuple)):
            tensor = torch.as_tensor(prompt_ids, dtype=torch.long)
        else:
            return prompt_ids

        if tensor.ndim == 0:
            tensor = tensor.reshape(1)
        elif tensor.ndim > 1:
            tensor = tensor.reshape(-1)

        _fe, _tok, model = _resolve_whisper_runtime(pipe)
        device = getattr(model, "device", None) if model is not None else None
        if device is not None:
            try:
                tensor = tensor.to(device=device)
            except (RuntimeError, TypeError, ValueError):
                return tensor
        return tensor
    except (RuntimeError, TypeError, ValueError):
        return prompt_ids


def whisper_prompt_ids_from_text(*, pipe: Any, text: str, max_chars: int = 240) -> Any:
    """Return Whisper prompt ids for a short text prefix when supported by the runtime."""
    prompt_text = str(text or "").strip()
    if not prompt_text:
        return None
    if max_chars > 0 and len(prompt_text) > int(max_chars):
        prompt_text = prompt_text[-int(max_chars):].strip()
    _fe, tok, _model = _resolve_whisper_runtime(pipe)
    if tok is None:
        return None

    get_prompt_ids = getattr(tok, "get_prompt_ids", None)
    if not callable(get_prompt_ids):
        return None

    try:
        return _normalize_whisper_prompt_ids(get_prompt_ids(prompt_text), pipe=pipe)
    except TypeError:
        try:
            return _normalize_whisper_prompt_ids(get_prompt_ids(prompt_text, return_tensors="pt"), pipe=pipe)
        except (RuntimeError, TypeError, ValueError):
            return None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
