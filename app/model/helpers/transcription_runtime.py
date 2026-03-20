# app/model/helpers/transcription_runtime.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch

from app.model.io.media_probe import is_url_source
from app.model.helpers.string_utils import sanitize_url_for_log


# ----- Audio signal helpers -----
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


# ----- Language normalization -----
def normalize_detected_language(lang: str) -> str:
    """Normalize detected language labels to a stable short code."""
    lang = str(lang or "").strip().lower().replace("_", "-")
    lang = lang.split("-", 1)[0]
    try:
        from transformers.models.whisper import tokenization_whisper

        languages = getattr(tokenization_whisper, "LANGUAGES", {})
        inv = {str(value).lower(): str(key) for key, value in dict(languages).items()}
        return inv.get(lang, lang)
    except Exception:
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


# ----- Whisper runtime helpers -----
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
            except Exception:
                continue
        if resolved:
            return resolved

    try:
        vocab = tokenizer.get_vocab()
    except Exception:
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
        except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
        best_lang = ""
        best_score = None
        for code, token_id in lang_to_id.items():
            try:
                score = float(logits[0, int(token_id)].item())
            except Exception:
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
    except Exception:
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
    except Exception:
        return ""


# ----- Logging helpers -----
def debug_source_key(value: str) -> str:
    """Return a log-safe, user-neutral source label."""
    text = str(value or "").strip()
    if not text:
        return ""
    if is_url_source(text):
        return sanitize_url_for_log(text)
    return Path(text).name or text


# ----- Public runtime API -----
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
    except Exception:
        return ""
