# app/model/translation/runtime_request.py
from __future__ import annotations

from dataclasses import dataclass

from app.model.core.config.config import AppConfig, ConfigError
from app.model.core.config.profiles import RuntimeProfiles
from app.model.engines.types import TranslateTextRequest


def _dtype_name(dtype_name: str) -> str:
    name = str(dtype_name or "float32").strip().lower()
    if name in ("float16", "fp16", "half"):
        return "float16"
    if name in ("bfloat16", "bf16"):
        return "bfloat16"
    return "float32"


@dataclass(frozen=True)
class TranslationRuntimeConfig:
    """Effective runtime parameters resolved from translation settings."""

    model_ref: str
    device: str
    dtype: str
    low_cpu_mem_usage: bool
    max_new_tokens: int
    chunk_max_chars: int
    num_beams: int
    no_repeat_ngram_size: int


def resolve_translation_runtime_config() -> TranslationRuntimeConfig:
    """Resolve effective translation runtime parameters from current settings."""

    if AppConfig.SETTINGS is None:
        raise ConfigError("error.runtime.settings_not_initialized")

    model_cfg = AppConfig.translation_model_raw_cfg_dict()
    if not model_cfg:
        raise ConfigError("error.runtime.settings_not_initialized")

    model_path = AppConfig.PATHS.TRANSLATION_ENGINE_DIR
    if not (model_path.exists() and model_path.is_dir()):
        raise ConfigError("error.model.translation_missing", path=str(model_path))

    advanced = model_cfg.get("advanced") if isinstance(model_cfg.get("advanced"), dict) else {}
    runtime = RuntimeProfiles.resolve_translation_runtime(
        profile=model_cfg.get("profile", RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE),
        overrides=advanced,
    )
    return TranslationRuntimeConfig(
        model_ref=str(model_path),
        device=str(AppConfig.DEVICE_ID),
        dtype=_dtype_name(str(AppConfig.DTYPE_ID)),
        low_cpu_mem_usage=bool(AppConfig.engine_low_cpu_mem_usage()),
        max_new_tokens=int(model_cfg["max_new_tokens"]),
        chunk_max_chars=int(model_cfg["chunk_max_chars"]),
        num_beams=int(runtime["num_beams"]),
        no_repeat_ngram_size=int(runtime["no_repeat_ngram_size"]),
    )


def build_translation_request(
    *,
    text: str,
    src_lang: str,
    tgt_lang: str,
    runtime: TranslationRuntimeConfig | None = None,
) -> TranslateTextRequest:
    """Build a normalized engine-host translation request from effective runtime settings."""

    resolved = runtime or resolve_translation_runtime_config()
    return TranslateTextRequest(
        text=str(text or ""),
        src_lang=str(src_lang or ""),
        tgt_lang=str(tgt_lang or ""),
        model_ref=str(resolved.model_ref),
        device=str(resolved.device),
        dtype=str(resolved.dtype),
        low_cpu_mem_usage=bool(resolved.low_cpu_mem_usage),
        max_new_tokens=int(resolved.max_new_tokens),
        num_beams=int(resolved.num_beams),
        no_repeat_ngram_size=int(resolved.no_repeat_ngram_size),
    )
