# app/model/services/translation_service.py
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import logging
from dataclasses import dataclass
from typing import Any, Callable

import torch

from app.model.config.app_config import AppConfig as Config, ConfigError
from app.model.config.runtime_profiles import RuntimeProfiles
from app.model.domain.errors import AppError
from app.model.services.settings_service import SettingsCatalog
from app.model.helpers.string_utils import normalize_lang_code

_LOG = logging.getLogger(__name__)

class TranslationError(AppError):
    """Key-based error used for i18n-friendly translation failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))

LogFn = Callable[[str], None]

def _norm_lang(code: str) -> str:
    return normalize_lang_code(code, drop_region=True)

def _supported() -> set[str]:
    return set(SettingsCatalog.translation_language_codes())

def _dtype_name(dtype_name: str) -> str:
    name = str(dtype_name or "float32").strip().lower()
    if name in ("float16", "fp16", "half"):
        return "float16"
    if name in ("bfloat16", "bf16"):
        return "bfloat16"
    return "float32"

@dataclass
class _WorkerIO:
    proc: subprocess.Popen
    lock: threading.Lock

_WORKER: _WorkerIO | None = None
_WORKER_GUARD = threading.Lock()

class TranslationService:
    """Translation via a dedicated worker process."""


    def warmup(self, *, log: LogFn | None = None) -> bool:
        try:
            self._ensure_worker(log=log)
            rep = self._rpc({"cmd": "warmup", **self._runtime_request_payload()})
            if not isinstance(rep, dict) or not rep.get("ok", False):
                raise TranslationError(
                    "error.translation.worker_error",
                    detail=str(rep.get("error") or rep.get("code") or "warmup failed"),
                )
            return True
        except AppError:
            return False

    def translate(self, text: str, *, src_lang: str, tgt_lang: str, log: LogFn | None = None) -> str:
        t = str(text or "").strip()
        if not t:
            return ""

        self._ensure_worker(log=log)

        sup = _supported()
        if not sup:
            raise TranslationError("error.translation.language_catalog_unavailable")

        src = _norm_lang(src_lang)
        tgt = _norm_lang(tgt_lang)

        if not tgt or tgt == "auto":
            raise TranslationError("error.translation.unsupported_target", lang=str(tgt_lang))
        if not src or src == "auto":
            raise TranslationError("error.translation.unsupported_source", lang=str(src_lang))

        if tgt not in sup:
            raise TranslationError("error.translation.unsupported_target", lang=str(tgt))
        if src not in sup:
            raise TranslationError("error.translation.unsupported_source", lang=str(src))

        if Config.SETTINGS is None:
            raise ConfigError("error.runtime.settings_not_initialized")

        runtime_payload = self._runtime_request_payload()
        device_str = str(runtime_payload["device"])
        dtype_name = str(runtime_payload["dtype"])
        chunk_max_chars = int(runtime_payload["chunk_max_chars"])
        quality_preset = str(runtime_payload["quality_preset"])

        debug_chunk_count = 0
        if _LOG.isEnabledFor(logging.DEBUG):
            debug_chunk_count = len(_chunk_text(t, max_chars=chunk_max_chars))
            _LOG.debug(
                "Translation request prepared. worker=translation text_chars=%s chunk_count=%s src_lang=%s tgt_lang=%s device=%s dtype=%s preset=%s",
                len(t),
                debug_chunk_count,
                src,
                tgt,
                device_str,
                dtype_name,
                quality_preset,
            )

        payload = {
            "cmd": "translate",
            "text": t,
            "src": src,
            "tgt": tgt,
            **runtime_payload,
        }

        try:
            rep = self._rpc(payload)
        except AppError:
            raise
        except Exception as ex:
            _LOG.exception("Translation worker protocol error.")
            raise TranslationError("error.translation.worker_protocol_error", detail=str(ex))

        if not isinstance(rep, dict) or not rep.get("ok", False):
            err_key = str(rep.get("error_key") or "").strip() if isinstance(rep, dict) else ""
            err_params = rep.get("error_params") if isinstance(rep, dict) else None
            if err_key:
                det = str(rep.get("error") or "").strip()
                if det:
                    _LOG.debug("Translation worker error detail. detail=%s", det)
                raise TranslationError(err_key, **dict(err_params or {}))

            code = str(rep.get("code", "")) if isinstance(rep, dict) else ""
            err = str(rep.get("error", "")) if isinstance(rep, dict) else ""
            msg = (err or code or "unknown").strip()
            raise TranslationError("error.translation.worker_error", detail=msg)

        out = str(rep.get("text", "") or "").strip()
        if not out:
            raise TranslationError("error.translation.empty_result")

        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "Translation request finished. worker=translation text_chars=%s output_chars=%s chunk_count=%s",
                len(t),
                len(out),
                debug_chunk_count,
            )
        return out

    def _ensure_worker(self, *, log: LogFn | None = None) -> None:
        global _WORKER

        def _dispose() -> None:
            global _WORKER
            if _WORKER is None:
                return
            try:
                _WORKER.proc.kill()
            except (ProcessLookupError, OSError) as proc_ex:
                _LOG.debug("Translation worker process kill skipped. detail=%s", proc_ex)
            _WORKER = None

        with _WORKER_GUARD:
            if _WORKER is not None and _WORKER.proc.poll() is None:
                _LOG.debug("Translation worker reused. worker=translation")
                return

            _LOG.debug("Translation worker starting. worker=translation")
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "app.model.services.translation_service", "--worker"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=str(Config.PATHS.ROOT_DIR),
                )
            except (OSError, ValueError, RuntimeError) as ex:
                _WORKER = None
                _LOG.warning("Translation worker start failed. detail=%s", ex)
                raise TranslationError("error.translation.worker_start_failed", detail=str(ex))

            _WORKER = _WorkerIO(proc=proc, lock=threading.Lock())

        try:
            rep = self._rpc({"cmd": "ping"})
        except AppError:
            _dispose()
            raise
        except Exception as ex:
            _dispose()
            _LOG.warning("Translation worker ping failed. detail=%s", ex)
            raise TranslationError("error.translation.worker_ping_failed")

        ok = bool(isinstance(rep, dict) and rep.get("ok", False))
        if not ok:
            _dispose()

            err_key = str(rep.get("error_key") or "").strip() if isinstance(rep, dict) else ""
            err_params = rep.get("error_params") if isinstance(rep, dict) else None
            if err_key:
                det = str(rep.get("error") or "").strip()
                if det:
                    _LOG.debug("Translation worker ping error detail. detail=%s", det)
                raise TranslationError(err_key, **dict(err_params or {}))

            code = str(rep.get("code", "")) if isinstance(rep, dict) else ""
            err = str(rep.get("error", "")) if isinstance(rep, dict) else ""
            msg = (err or code or "ping failed").strip()
            raise TranslationError("error.translation.worker_ping_failed", detail=msg)

        _LOG.debug("Translation worker ping succeeded. worker=translation")
        if log:
            log("Translation engine ready.")
        _LOG.info("Translation engine ready.")

    @staticmethod
    def _runtime_request_payload() -> dict[str, Any]:
        if Config.SETTINGS is None:
            raise ConfigError("error.runtime.settings_not_initialized")

        mdl = Config.translation_model_raw_cfg_dict()
        if not mdl:
            raise ConfigError("error.runtime.settings_not_initialized")

        model_path = Config.PATHS.TRANSLATION_ENGINE_DIR
        if not (model_path.exists() and model_path.is_dir()):
            raise ConfigError("error.model.translation_missing", path=str(model_path))

        return {
            "model_ref": str(model_path),
            "device": str(getattr(Config, "DEVICE_ID", "cpu")),
            "dtype": _dtype_name(str(getattr(Config, "DTYPE_ID", "float32"))),
            "low_cpu_mem_usage": bool(Config.engine_low_cpu_mem_usage()),
            "max_new_tokens": int(mdl["max_new_tokens"]),
            "chunk_max_chars": int(mdl["chunk_max_chars"]),
            "quality_preset": str(mdl["quality_preset"]).strip().lower(),
        }

    @staticmethod
    def _rpc(payload: dict[str, Any]) -> dict[str, Any]:
        global _WORKER
        if _WORKER is None or _WORKER.proc.poll() is not None:
            raise TranslationError("error.translation.worker_not_running")

        io = _WORKER
        stdin = io.proc.stdin
        stdout = io.proc.stdout
        if stdin is None or stdout is None:
            raise TranslationError("error.translation.worker_protocol_error", detail="worker stdio unavailable")

        line = json.dumps(payload, ensure_ascii=True)
        with io.lock:
            stdin.write(line + "\n")
            stdin.flush()
            out = stdout.readline()
        if not out:
            raise TranslationError("error.translation.no_response_from_worker")
        try:
            rep = json.loads(out)
        except json.JSONDecodeError as ex:
            raise TranslationError("error.translation.worker_protocol_error", detail=str(ex))
        return rep if isinstance(rep, dict) else {}

@dataclass
class _LoadedM2M100:
    tokenizer: Any
    model: Any
    device: torch.device

_WORKER_STATE: dict[tuple[str, str, str, bool], _LoadedM2M100] = {}

def _chunk_text(text: str, *, max_chars: int) -> list[str]:
    t = str(text or "").strip()
    if not t:
        return []
    parts: list[str] = []
    for para in re.split(r"\n{2,}", t):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            parts.append(para)
            continue

        buf = ""
        for s in re.split(r"(?<=[.!?])\s+", para):
            s = s.strip()
            if not s:
                continue
            if len(buf) + 1 + len(s) <= max_chars:
                buf = (buf + " " + s).strip()
            else:
                if buf:
                    parts.append(buf)
                buf = s
        if buf:
            parts.append(buf)
    return parts

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

    tok = M2M100Tokenizer.from_pretrained(model_ref, local_files_only=local_files_only)
    model = M2M100ForConditionalGeneration.from_pretrained(
        model_ref,
        local_files_only=local_files_only,
        torch_dtype=dtype,
        low_cpu_mem_usage=low_cpu_mem_usage,
    )
    model.to(device)
    model.eval()
    return _LoadedM2M100(tokenizer=tok, model=model, device=device)

def _load_worker_runtime(req: dict[str, Any]) -> _LoadedM2M100:
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

def _worker_handle(req: dict[str, Any]) -> dict[str, Any]:
    cmd = str(req.get("cmd", ""))
    if cmd == "ping":
        return {"ok": True}

    if cmd == "warmup":
        try:
            _load_worker_runtime(req)
        except (OSError, RuntimeError, ValueError) as ex:
            return {"ok": False, "code": "warmup_failed", "error": str(ex)}
        return {"ok": True}

    if cmd == "translate":
        src = _norm_lang(req.get("src", ""))
        tgt = _norm_lang(req.get("tgt", ""))
        max_new_tokens = int(req.get("max_new_tokens"))
        chunk_max_chars = int(req.get("chunk_max_chars"))

        preset = RuntimeProfiles.normalize_transcription_preset(req.get("quality_preset"))
        if preset not in RuntimeProfiles.TRANSCRIPTION_PRESET_IDS:
            return {"ok": False, "code": "bad_preset", "error": f"unsupported preset '{preset}'"}

        try:
            loaded = _load_worker_runtime(req)
        except (OSError, RuntimeError, ValueError) as ex:
            return {"ok": False, "code": "load_failed", "error": str(ex)}

        tok = loaded.tokenizer
        model = loaded.model
        device = loaded.device
        lang_code_to_id = getattr(tok, "lang_code_to_id", {}) or {}

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
        for chunk in _chunk_text(str(req.get("text", "")), max_chars=chunk_max_chars):
            tok.src_lang = src
            enc = tok(chunk, return_tensors="pt", truncation=True).to(device)
            forced = tok.get_lang_id(tgt)
            gen_kwargs = {"forced_bos_token_id": forced, "max_new_tokens": max_new_tokens}
            if preset == RuntimeProfiles.TRANSCRIPTION_PRESET_FAST:
                gen_kwargs["num_beams"] = 1
            elif preset == RuntimeProfiles.TRANSCRIPTION_PRESET_BALANCED:
                gen_kwargs["num_beams"] = 3
                gen_kwargs["no_repeat_ngram_size"] = 3
            else:
                gen_kwargs["num_beams"] = 6
                gen_kwargs["no_repeat_ngram_size"] = 3
            gen = model.generate(**enc, **gen_kwargs)
            out = tok.batch_decode(gen, skip_special_tokens=True)[0]
            out_parts.append(str(out).strip())

        return {"ok": True, "text": "\n\n".join([p for p in out_parts if p]).strip()}

    return {"ok": False, "code": "bad_command", "error": f"unsupported cmd '{cmd}'"}

def _run_worker() -> int:
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
            rep = _worker_handle(req if isinstance(req, dict) else {})
        except Exception as ex:
            rep = {"ok": False, "code": "worker_exception", "error": str(ex)}
        sys.stdout.write(json.dumps(rep, ensure_ascii=True) + "\n")
        sys.stdout.flush()
    return 0

def _cli_entry(argv: list[str]) -> int:
    if "--worker" not in argv:
        return 0
    return _run_worker()

if __name__ == "__main__":
    raise SystemExit(_cli_entry(sys.argv[1:]))
