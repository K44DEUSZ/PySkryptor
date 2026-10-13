# app/features/translation/host_runtime.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from app.core.config.config import AppConfig
from app.core.utils.string_utils import normalize_lang_code
from app.features.translation.errors import TranslationError
from app.runtime.inference_probe import probe_translation
from app.runtime.runtime_config import resolve_torch_device, resolve_torch_dtype, use_cpu_runtime


def _normalize_language(code: str | None) -> str:
  return normalize_lang_code(code, drop_region=True)


@dataclass
class _LoadedTranslationRuntime:
  """Cached translation runtime tuple reused across host requests."""

  tokenizer: Any
  model: Any
  device: torch.device


class TranslationHostRuntime:
  """Dedicated translation-model runtime owned by the engine host."""

  def __init__(self) -> None:
    self._loaded: _LoadedTranslationRuntime | None = None
    self._smoke_passed = False
    self._model_ref: str = ""
    self._device_id: str = ""
    self._dtype_name: str = ""

  @staticmethod
  def _translation_error(key: str, **params: Any) -> TranslationError:
    return TranslationError(key, **params)

  @staticmethod
  def _load_model(
    *,
    model_path: Path,
    dtype: Any,
  ) -> Any:
    from transformers import M2M100ForConditionalGeneration

    load_kwargs: dict[str, Any] = {
      "local_files_only": True,
      "dtype": dtype,
      "weights_only": True,
      "trust_remote_code": False,
    }
    return M2M100ForConditionalGeneration.from_pretrained(str(model_path), **load_kwargs)

  def _load_runtime(
    self,
    *,
    model_ref: str,
    device_id: str,
    dtype_name: str,
  ) -> _LoadedTranslationRuntime:
    if (
      self._loaded is not None
      and self._model_ref == model_ref
      and self._device_id == device_id
      and self._dtype_name == dtype_name
    ):
      return self._loaded

    model_path = Path(str(model_ref or "")).expanduser()
    if not model_path.exists() or not model_path.is_dir():
      raise self._translation_error("error.model.translation_missing", path=str(model_path))

    from transformers import M2M100Tokenizer

    device = resolve_torch_device(device_id)
    dtype = resolve_torch_dtype(dtype_name, device)

    tokenizer = M2M100Tokenizer.from_pretrained(str(model_path), local_files_only=True)
    model = self._load_model(
      model_path=model_path,
      dtype=dtype,
    )
    try:
      model.to(device)
    except (RuntimeError, TypeError, ValueError):
      device = torch.device("cpu")
      model.to(device=device, dtype=torch.float32)
      AppConfig.DEVICE_ID = "cpu"
      AppConfig.DTYPE_ID = "float32"
      AppConfig.DEVICE_KIND = "CPU"
      AppConfig.DEVICE_FRIENDLY_NAME = "CPU"
    model.eval()

    self._model_ref = str(model_path)
    self._device_id = str(device)
    self._dtype_name = str(AppConfig.DTYPE_ID)
    self._loaded = _LoadedTranslationRuntime(tokenizer=tokenizer, model=model, device=device)
    return self._loaded

  def warmup(self) -> None:
    self._smoke_passed = False
    loaded = self._load_runtime(
      model_ref=str(AppConfig.PATHS.TRANSLATION_ENGINE_DIR),
      device_id=str(AppConfig.DEVICE_ID),
      dtype_name=str(AppConfig.DTYPE_ID),
    )
    fallback = False
    try:
      probe_translation(loaded.tokenizer, loaded.model, loaded.device)
    except RuntimeError:
      if getattr(loaded.device, 'type', 'cpu') != 'cuda':
        raise
      fallback = True
    if fallback:
      self._loaded = None
      loaded = None
      use_cpu_runtime()
      loaded = self._load_runtime(
        model_ref=str(AppConfig.PATHS.TRANSLATION_ENGINE_DIR),
        device_id=str(AppConfig.DEVICE_ID),
        dtype_name=str(AppConfig.DTYPE_ID),
      )
      probe_translation(loaded.tokenizer, loaded.model, loaded.device)
    self._smoke_passed = True

  def health(self) -> dict[str, Any]:
    loaded = self._loaded
    return {
      "role": "translation",
      "ready": bool(loaded is not None and self._smoke_passed),
      "model_ref": self._model_ref,
      "device": str(getattr(loaded, "device", "")) if loaded is not None else "",
    }

  def translate_text(self, payload: dict[str, Any]) -> str:
    source_language = _normalize_language(payload.get("src_lang") or "")
    target_language = _normalize_language(payload.get("tgt_lang") or "")
    text = str(payload.get("text") or "").strip()
    if not text:
      return ""

    loaded = self._load_runtime(
      model_ref=str(AppConfig.PATHS.TRANSLATION_ENGINE_DIR),
      device_id=str(AppConfig.DEVICE_ID),
      dtype_name=str(AppConfig.DTYPE_ID),
    )

    tokenizer = loaded.tokenizer
    model = loaded.model
    device = loaded.device
    lang_code_to_id = getattr(tokenizer, "lang_code_to_id", {}) or {}

    if not target_language or target_language == "auto" or target_language not in lang_code_to_id:
      raise self._translation_error(
        "error.translation.worker_error",
        detail=f"unsupported target '{target_language}'",
      )
    if not source_language or source_language == "auto" or source_language not in lang_code_to_id:
      raise self._translation_error(
        "error.translation.worker_error",
        detail=f"unsupported source '{source_language}'",
      )

    max_new_tokens = int(payload.get("max_new_tokens") or 0)
    num_beams = int(payload.get("num_beams") or 3)
    no_repeat_ngram_size = int(payload.get("no_repeat_ngram_size") or 0)

    tokenizer.src_lang = source_language
    enc = tokenizer(text, return_tensors="pt", truncation=True).to(device)
    generate_kwargs: dict[str, Any] = {
      "forced_bos_token_id": tokenizer.get_lang_id(target_language),
      "max_new_tokens": max_new_tokens,
      "num_beams": num_beams,
    }
    if no_repeat_ngram_size > 0:
      generate_kwargs["no_repeat_ngram_size"] = no_repeat_ngram_size
    with torch.inference_mode():
      gen = model.generate(**enc, **generate_kwargs)
    out = tokenizer.batch_decode(gen, skip_special_tokens=True)[0]
    return str(out).strip()
