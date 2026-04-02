# app/model/translation/host_runtime.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from app.model.core.config.config import AppConfig
from app.model.core.utils.string_utils import normalize_lang_code
from app.model.engines.runtime_config import resolve_torch_device, resolve_torch_dtype
from app.model.translation.errors import TranslationError


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
        self._model_ref: str = ""
        self._device_id: str = ""
        self._dtype_name: str = ""
        self._low_cpu_mem_usage: bool = False

    @staticmethod
    def _translation_error(key: str, **params: Any) -> TranslationError:
        return TranslationError(key, **params)

    @staticmethod
    def _load_model(
        *,
        model_path: Path,
        dtype: Any,
        low_cpu_mem_usage: bool,
    ) -> Any:
        from transformers import M2M100ForConditionalGeneration

        load_kwargs: dict[str, Any] = {
            "local_files_only": True,
            "dtype": dtype,
            "low_cpu_mem_usage": bool(low_cpu_mem_usage),
        }
        try:
            return M2M100ForConditionalGeneration.from_pretrained(str(model_path), **load_kwargs)
        except TypeError as ex:
            if "dtype" not in str(ex):
                raise

        load_kwargs.pop("dtype", None)
        load_kwargs["torch_dtype"] = dtype
        return M2M100ForConditionalGeneration.from_pretrained(str(model_path), **load_kwargs)

    def _load_runtime(
        self,
        *,
        model_ref: str,
        device_id: str,
        dtype_name: str,
        low_cpu_mem_usage: bool,
    ) -> _LoadedTranslationRuntime:
        if (
            self._loaded is not None
            and self._model_ref == model_ref
            and self._device_id == device_id
            and self._dtype_name == dtype_name
            and self._low_cpu_mem_usage == bool(low_cpu_mem_usage)
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
            low_cpu_mem_usage=bool(low_cpu_mem_usage),
        )
        model.to(device)
        model.eval()

        self._model_ref = str(model_path)
        self._device_id = str(device_id)
        self._dtype_name = str(dtype_name)
        self._low_cpu_mem_usage = bool(low_cpu_mem_usage)
        self._loaded = _LoadedTranslationRuntime(tokenizer=tokenizer, model=model, device=device)
        return self._loaded

    def warmup(self) -> None:
        self._load_runtime(
            model_ref=str(AppConfig.PATHS.TRANSLATION_ENGINE_DIR),
            device_id=str(AppConfig.DEVICE_ID),
            dtype_name=str(AppConfig.DTYPE_ID),
            low_cpu_mem_usage=bool(AppConfig.engine_low_cpu_mem_usage()),
        )

    def health(self) -> dict[str, Any]:
        loaded = self._loaded
        return {
            "role": "translation",
            "ready": bool(loaded is not None),
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
            model_ref=str(payload.get("model_ref") or ""),
            device_id=str(payload.get("device") or ""),
            dtype_name=str(payload.get("dtype") or ""),
            low_cpu_mem_usage=bool(payload.get("low_cpu_mem_usage")),
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
        gen = model.generate(**enc, **generate_kwargs)
        out = tokenizer.batch_decode(gen, skip_special_tokens=True)[0]
        return str(out).strip()
