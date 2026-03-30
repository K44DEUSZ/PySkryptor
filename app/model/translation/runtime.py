# app/model/translation/runtime.py
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

import torch

from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.utils.string_utils import normalize_lang_code
from app.model.translation.chunking import chunk_text


def _norm_lang(code: str | None) -> str:
    return normalize_lang_code(code, drop_region=True)


@dataclass
class _LoadedM2M100:
    """Loaded tokenizer, model and device triple cached by the worker runtime."""

    tokenizer: Any
    model: Any
    device: torch.device


_WORKER_STATE: dict[tuple[str, str, str, bool], _LoadedM2M100] = {}


def _resolve_device(device_str: str) -> torch.device:
    wanted = str(device_str or "cpu").strip().lower()
    if wanted.startswith("cuda") and torch.cuda.is_available():
        try:
            return torch.device(device_str)
        except (RuntimeError, TypeError, ValueError):
            return torch.device("cuda")
    return torch.device("cpu")


def _resolve_dtype(dtype_name: str, device: torch.device) -> torch.dtype:
    if str(device).startswith("cpu"):
        return torch.float32
    name = str(dtype_name or "auto").strip().lower()
    if name in ("float16", "fp16"):
        return torch.float16
    if name in ("bfloat16", "bf16"):
        return torch.bfloat16
    if name in ("float32", "fp32"):
        return torch.float32
    return torch.float16


def _load_m2m100(
    *,
    model_ref: str,
    device_str: str,
    dtype_name: str,
    local_files_only: bool,
    low_cpu_mem_usage: bool,
) -> _LoadedM2M100:
    from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

    device = _resolve_device(device_str)
    dtype = _resolve_dtype(dtype_name, device)

    tokenizer = M2M100Tokenizer.from_pretrained(model_ref, local_files_only=local_files_only)
    model = M2M100ForConditionalGeneration.from_pretrained(
        model_ref,
        local_files_only=local_files_only,
        torch_dtype=dtype,
        low_cpu_mem_usage=low_cpu_mem_usage,
    )
    model.to(device)
    model.eval()
    return _LoadedM2M100(tokenizer=tokenizer, model=model, device=device)


def _load_runtime(req: dict[str, Any]) -> _LoadedM2M100:
    model_ref = str(req.get("model_ref") or "").strip()
    device_str = str(req.get("device") or "").strip()
    dtype_name = str(req.get("dtype") or "").strip().lower()
    low_cpu_mem_usage = bool(req.get("low_cpu_mem_usage"))

    key = (model_ref, device_str, dtype_name, low_cpu_mem_usage)
    loaded = _WORKER_STATE.get(key)
    if loaded is not None:
        return loaded

    loaded = _load_m2m100(
        model_ref=model_ref,
        device_str=device_str,
        dtype_name=dtype_name,
        local_files_only=True,
        low_cpu_mem_usage=low_cpu_mem_usage,
    )
    _WORKER_STATE[key] = loaded
    return loaded


def _handle_request(req: dict[str, Any]) -> dict[str, Any]:
    cmd = str(req.get("cmd", ""))
    if cmd == "ping":
        return {"ok": True}

    if cmd == "warmup":
        try:
            _load_runtime(req)
        except (OSError, RuntimeError, ValueError) as ex:
            return {"ok": False, "code": "warmup_failed", "error": str(ex)}
        return {"ok": True}

    if cmd == "translate":
        src = _norm_lang(req.get("src", ""))
        tgt = _norm_lang(req.get("tgt", ""))
        max_new_tokens = int(req.get("max_new_tokens"))
        chunk_max_chars = int(req.get("chunk_max_chars"))
        runtime = RuntimeProfiles.resolve_translation_runtime(
            profile=req.get("profile", RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE),
            overrides={
                "style": req.get("style"),
                "num_beams": req.get("num_beams"),
                "no_repeat_ngram_size": req.get("no_repeat_ngram_size"),
            },
        )

        try:
            loaded = _load_runtime(req)
        except (OSError, RuntimeError, ValueError) as ex:
            return {"ok": False, "code": "load_failed", "error": str(ex)}

        tokenizer = loaded.tokenizer
        model = loaded.model
        device = loaded.device
        lang_code_to_id = getattr(tokenizer, "lang_code_to_id", {}) or {}

        if not tgt or tgt == "auto" or tgt not in lang_code_to_id:
            return {
                "ok": False,
                "code": "unsupported_target",
                "error_key": "error.translation.unsupported_target",
                "error_params": {"lang": str(tgt)},
                "error": f"unsupported target '{tgt}'",
            }
        if not src or src == "auto" or src not in lang_code_to_id:
            return {
                "ok": False,
                "code": "unsupported_source",
                "error_key": "error.translation.unsupported_source",
                "error_params": {"lang": str(src)},
                "error": f"unsupported source '{src}'",
            }

        out_parts: list[str] = []
        for chunk in chunk_text(str(req.get("text", "")), max_chars=chunk_max_chars):
            tokenizer.src_lang = src
            enc = tokenizer(chunk, return_tensors="pt", truncation=True).to(device)
            forced = tokenizer.get_lang_id(tgt)
            gen_kwargs: dict[str, Any] = {
                "forced_bos_token_id": forced,
                "max_new_tokens": max_new_tokens,
                "num_beams": int(runtime.get("num_beams", 3)),
            }
            no_repeat = int(runtime.get("no_repeat_ngram_size", 0) or 0)
            if no_repeat > 0:
                gen_kwargs["no_repeat_ngram_size"] = no_repeat
            gen = model.generate(**enc, **gen_kwargs)
            out = tokenizer.batch_decode(gen, skip_special_tokens=True)[0]
            out_parts.append(str(out).strip())

        return {"ok": True, "text": "\n\n".join([part for part in out_parts if part]).strip()}

    return {"ok": False, "code": "bad_command", "error": f"unsupported cmd '{cmd}'"}


def _run_runtime() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as ex:
            sys.stdout.write(json.dumps({"ok": False, "code": "bad_json", "error": str(ex)}) + "\n")
            sys.stdout.flush()
            continue
        try:
            rep = _handle_request(req if isinstance(req, dict) else {})
        except Exception as ex:
            rep = {"ok": False, "code": "worker_exception", "error": str(ex)}
        sys.stdout.write(json.dumps(rep, ensure_ascii=True) + "\n")
        sys.stdout.flush()
    return 0


def _cli_entry(argv: list[str]) -> int:
    if "--worker" not in argv:
        return 0
    return _run_runtime()


if __name__ == "__main__":
    raise SystemExit(_cli_entry(sys.argv[1:]))
