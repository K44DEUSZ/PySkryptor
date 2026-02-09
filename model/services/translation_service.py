# model/services/translation_service.py
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

import torch

from model.config.app_config import AppConfig as Config
from model.constants.m2m100_languages import m2m100_language_codes
from view.utils.translating import tr

LogFn = Callable[[str], None]


def _norm(code: str) -> str:
    return str(code or "").strip().lower().replace("_", "-").split("-", 1)[0]


def _supported() -> Set[str]:
    return set(m2m100_language_codes())


@dataclass
class _WorkerIO:
    proc: subprocess.Popen
    lock: threading.Lock


_WORKER: Optional[_WorkerIO] = None
_WORKER_GUARD = threading.Lock()


class TranslationService:
    """Translation via a dedicated worker process (Windows-safe).

    SentencePiece (used by M2M100 tokenizer) can hard-crash the Python process on Windows.
    Running translation in a separate process prevents the whole app from crashing.
    """

    @classmethod
    def supported_language_codes(cls) -> Set[str]:
        return _supported()

    def warmup(self, *, log: Optional[LogFn] = None) -> bool:
        ok = self._ensure_worker(log=log)
        return ok

    def translate(self, text: str, *, src_lang: str, tgt_lang: str, log: Optional[LogFn] = None) -> str:
        t = str(text or "").strip()
        if not t:
            return ""

        if not self._ensure_worker(log=log):
            return ""

        sup = _supported()
        src = _norm(src_lang)
        tgt = _norm(tgt_lang)


        if not tgt or tgt == "auto":
            tgt = "en"
            if log:
                log(tr("log.translation.target_defaulted", code=tgt))

        if tgt not in sup:
            if log:
                log(tr("log.translation.failed.unsupported_target_language", code=tgt))
            return ""

        if not src or src not in sup:
            src = "en"

        snap = Config.SETTINGS
        if snap is None:
            raise RuntimeError("error.runtime.settings_not_initialized")

        mdl = snap.model.get("translation_model", {}) if isinstance(snap.model, dict) else {}
        model_path = Config.TRANSLATION_ENGINE_DIR
        use_local = model_path.exists() and model_path.is_dir() and model_path.name != "__missing__"
        if not use_local:
            raise RuntimeError(f"error.model.translation_missing||{model_path}")
        model_ref = str(model_path)
        dtype_name = str(mdl.get("dtype", "auto") or "auto").strip().lower()
        local_files_only = True
        engine_cfg = snap.engine if isinstance(snap.engine, dict) else {}
        low_cpu_mem_usage = bool(engine_cfg.get("low_cpu_mem_usage", True))
        max_new_tokens = int(mdl.get("max_new_tokens", 256))
        chunk_max_chars = int(mdl.get("chunk_max_chars", 1200))
        quality_preset = str(mdl.get("quality_preset", "balanced") or "balanced").strip().lower()

        payload = {
            "cmd": "translate",
            "text": t,
            "src": src,
            "tgt": tgt,
            "model_ref": model_ref,
            "dtype": dtype_name,
            "low_cpu_mem_usage": low_cpu_mem_usage,
            "max_new_tokens": max_new_tokens,
            "chunk_max_chars": chunk_max_chars,
            "quality_preset": quality_preset,
        }

        try:
            rep = self._rpc(payload)
        except Exception as ex:
            if log:
                log(tr("log.translation.failed.worker_protocol", msg=str(ex)))
            return ""

        if not isinstance(rep, dict) or not rep.get("ok", False):
            err = ""
            if isinstance(rep, dict):
                err = str(rep.get("error", "")) or str(rep.get("code", ""))
            if log:
                log(tr("log.translation.failed.worker_error", msg=err or "unknown"))
            return ""

        return str(rep.get("text", "") or "").strip()

    # ----- Worker management -----

    def _ensure_worker(self, *, log: Optional[LogFn] = None) -> bool:
        global _WORKER

        with _WORKER_GUARD:
            if _WORKER is not None and _WORKER.proc.poll() is None:
                return True

            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "model.services.translation_service", "--worker"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except Exception as ex:
                if log:
                    log(tr("log.translation.failed.worker_start", msg=str(ex)))
                _WORKER = None
                return False

            _WORKER = _WorkerIO(proc=proc, lock=threading.Lock())

        # ping outside guard
        try:
            rep = self._rpc({"cmd": "ping"})
            ok = bool(isinstance(rep, dict) and rep.get("ok", False))
        except Exception:
            ok = False

        if ok:
            if log:
                log(tr("log.translation.worker_ready"))
            return True

        if log:
            log(tr("log.translation.failed.worker_start", msg="ping failed"))
        return False

    def _rpc(self, payload: Dict) -> Dict:
        global _WORKER
        if _WORKER is None or _WORKER.proc.poll() is not None:
            raise RuntimeError("translation worker is not running")

        io = _WORKER
        assert io.proc.stdin is not None
        assert io.proc.stdout is not None

        line = json.dumps(payload, ensure_ascii=True)
        with io.lock:
            io.proc.stdin.write(line + "\n")
            io.proc.stdin.flush()
            out = io.proc.stdout.readline()
        if not out:
            raise RuntimeError("no response from worker")
        return json.loads(out)

# --------------------------------------------------------------------------------------
# Worker entrypoint
# --------------------------------------------------------------------------------------

@dataclass
class _LoadedM2M100:
    tokenizer: object
    model: object
    device: torch.device


_WORKER_STATE: Dict[Tuple, _LoadedM2M100] = {}


def _chunk_text(text: str, *, max_chars: int) -> List[str]:
    t = str(text or "").strip()
    if not t:
        return []
    parts: List[str] = []
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
    return getattr(Config, "DTYPE", torch.float16)


def _load_m2m100(
    *,
    model_ref: str,
    dtype_name: str,
    local_files_only: bool,
    low_cpu_mem_usage: bool,
) -> _LoadedM2M100:
    from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer  # type: ignore

    device = getattr(Config, "DEVICE", torch.device("cpu"))
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


def _worker_handle(req: Dict) -> Dict:
    cmd = str(req.get("cmd", ""))
    if cmd == "ping":
        return {"ok": True}

    if cmd == "translate":
        sup = _supported()
        src = _norm(req.get("src", ""))
        tgt = _norm(req.get("tgt", ""))

        if not tgt or tgt not in sup:
            return {"ok": False, "code": "unsupported_target", "error": f"unsupported target '{tgt}'"}

        if not src or src not in sup:
            src = "en"

        model_ref = str(req.get("model_ref") or "facebook/m2m100_418M").strip()
        dtype_name = str(req.get("dtype") or "auto").strip().lower()
        local_files_only = True
        low_cpu_mem_usage = bool(req.get("low_cpu_mem_usage", True))
        max_new_tokens = int(req.get("max_new_tokens", 256))
        chunk_max_chars = int(req.get("chunk_max_chars", 1200))

        preset = str(req.get("quality_preset", "balanced") or "balanced").strip().lower()
        if preset not in ("fast", "balanced", "accurate"):
            preset = "balanced"

        key = (model_ref, dtype_name, low_cpu_mem_usage)
        loaded = _WORKER_STATE.get(key)
        if loaded is None:
            loaded = _load_m2m100(
                model_ref=model_ref,
                dtype_name=dtype_name,
                local_files_only=True,
                low_cpu_mem_usage=low_cpu_mem_usage,
            )
            _WORKER_STATE[key] = loaded

        tok = loaded.tokenizer
        model = loaded.model
        device = loaded.device

        out_parts: List[str] = []
        for chunk in _chunk_text(str(req.get("text", "")), max_chars=chunk_max_chars):
            tok.src_lang = src
            enc = tok(chunk, return_tensors="pt", truncation=True).to(device)
            forced = tok.get_lang_id(tgt)
            gen_kwargs = {"forced_bos_token_id": forced, "max_new_tokens": max_new_tokens}
            if preset == "fast":
                gen_kwargs["num_beams"] = 1
            elif preset == "balanced":
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
        except Exception as ex:
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


def _main(argv: List[str]) -> int:
    if "--worker" in argv:
        return _run_worker()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
