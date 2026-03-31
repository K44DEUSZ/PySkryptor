# app/model/translation/service.py
from __future__ import annotations

import logging
from typing import Any, Callable

from app.model.core.config.config import AppConfig
from app.model.core.config.config import ConfigError
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.errors import AppError, OperationCancelled
from app.model.core.utils.string_utils import normalize_lang_code
from app.model.engines.capabilities import translation_language_codes
from app.model.translation.chunking import chunk_text
from app.model.translation.gateway import _WORKER_CLIENT, TranslationError

_LOG = logging.getLogger(__name__)

LogFn = Callable[[str], None]


def _norm_lang(code: str | None) -> str:
    return normalize_lang_code(code, drop_region=True)


def _supported() -> set[str]:
    return set(translation_language_codes())


def _dtype_name(dtype_name: str) -> str:
    name = str(dtype_name or "float32").strip().lower()
    if name in ("float16", "fp16", "half"):
        return "float16"
    if name in ("bfloat16", "bf16"):
        return "bfloat16"
    return "float32"


class TranslationService:
    """Translation via a dedicated worker process."""

    def warmup(self, *, log: LogFn | None = None) -> bool:
        _WORKER_CLIENT.ensure_worker(log=log)
        rep = _WORKER_CLIENT.rpc(
            {"cmd": "warmup", **self._runtime_request_payload()},
            timeout_s=_WORKER_CLIENT.policy.warmup_timeout_s,
        )
        if not isinstance(rep, dict) or not rep.get("ok", False):
            _WORKER_CLIENT.dispose(log_reason="warmup_failed")
            raise TranslationError(
                "error.translation.worker_error",
                detail=str(rep.get("error") or rep.get("code") or "warmup failed"),
            )
        return True

    def translate(
        self,
        text: str,
        *,
        src_lang: str,
        tgt_lang: str,
        log: LogFn | None = None,
        progress_cb: Callable[[int], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        payload_text = str(text or "").strip()
        if not payload_text:
            return ""

        _WORKER_CLIENT.ensure_worker(log=log)

        supported = _supported()
        if not supported:
            raise TranslationError("error.translation.language_catalog_unavailable")

        src = _norm_lang(src_lang)
        tgt = _norm_lang(tgt_lang)

        if not tgt or tgt == "auto":
            raise TranslationError("error.translation.unsupported_target", lang=str(tgt_lang))
        if not src or src == "auto":
            raise TranslationError("error.translation.unsupported_source", lang=str(src_lang))

        if tgt not in supported:
            raise TranslationError("error.translation.unsupported_target", lang=str(tgt))
        if src not in supported:
            raise TranslationError("error.translation.unsupported_source", lang=str(src))

        if AppConfig.SETTINGS is None:
            raise ConfigError("error.runtime.settings_not_initialized")

        runtime_payload = self._runtime_request_payload()
        chunk_max_chars = int(runtime_payload["chunk_max_chars"])
        chunks = chunk_text(payload_text, max_chars=chunk_max_chars)
        chunk_count = len(chunks)

        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                (
                    "Translation request prepared. worker=translation text_chars=%s chunk_count=%s "
                    "src_lang=%s tgt_lang=%s device=%s dtype=%s profile=%s style=%s"
                ),
                len(payload_text),
                chunk_count,
                src,
                tgt,
                str(runtime_payload["device"]),
                str(runtime_payload["dtype"]),
                str(runtime_payload["profile"]),
                str(runtime_payload["style"]),
            )

        if progress_cb is not None:
            progress_cb(0)

        out_parts: list[str] = []
        total = max(1, chunk_count)
        for idx, chunk in enumerate(chunks, start=1):
            if cancel_check is not None and cancel_check():
                raise OperationCancelled()
            out_parts.append(
                self._translate_chunk_text(
                    chunk=chunk,
                    src=src,
                    tgt=tgt,
                    runtime_payload=runtime_payload,
                    cancel_check=cancel_check,
                )
            )
            if progress_cb is not None:
                progress_cb(int(round((idx / float(total)) * 100)))

        out = "\n\n".join([part for part in out_parts if str(part).strip()]).strip()
        if not out:
            raise TranslationError("error.translation.empty_result")

        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "Translation request finished. worker=translation text_chars=%s output_chars=%s chunk_count=%s",
                len(payload_text),
                len(out),
                chunk_count,
            )
        return out

    def _translate_chunk_text(
        self,
        *,
        chunk: str,
        src: str,
        tgt: str,
        runtime_payload: dict[str, Any],
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        payload = {
            "cmd": "translate",
            "text": str(chunk or ""),
            "src": src,
            "tgt": tgt,
            **runtime_payload,
        }

        try:
            rep = _WORKER_CLIENT.rpc(
                payload,
                timeout_s=_WORKER_CLIENT.policy.request_timeout_s,
                cancel_check=cancel_check,
            )
        except AppError:
            raise
        except Exception as ex:
            _LOG.exception("Translation worker protocol error.")
            raise TranslationError("error.translation.worker_protocol_error", detail=str(ex))

        out = self._extract_text_from_reply(rep)
        if not out:
            raise TranslationError("error.translation.empty_result")
        return out

    @staticmethod
    def _extract_text_from_reply(rep: dict[str, Any] | Any) -> str:
        if not isinstance(rep, dict) or not rep.get("ok", False):
            err_key = str(rep.get("error_key") or "").strip() if isinstance(rep, dict) else ""
            err_params = rep.get("error_params") if isinstance(rep, dict) else None
            if err_key:
                det = str(rep.get("error") or "").strip() if isinstance(rep, dict) else ""
                if det:
                    _LOG.debug("Translation worker error detail. detail=%s", det)
                raise TranslationError(err_key, **dict(err_params or {}))

            code = str(rep.get("code", "")) if isinstance(rep, dict) else ""
            err = str(rep.get("error", "")) if isinstance(rep, dict) else ""
            msg = (err or code or "unknown").strip()
            raise TranslationError("error.translation.worker_error", detail=msg)

        return str(rep.get("text", "") or "").strip()

    @staticmethod
    def _runtime_request_payload() -> dict[str, Any]:
        if AppConfig.SETTINGS is None:
            raise ConfigError("error.runtime.settings_not_initialized")

        mdl = AppConfig.translation_model_raw_cfg_dict()
        if not mdl:
            raise ConfigError("error.runtime.settings_not_initialized")

        model_path = AppConfig.PATHS.TRANSLATION_ENGINE_DIR
        if not (model_path.exists() and model_path.is_dir()):
            raise ConfigError("error.model.translation_missing", path=str(model_path))

        advanced = mdl.get("advanced") if isinstance(mdl.get("advanced"), dict) else {}
        runtime = RuntimeProfiles.resolve_translation_runtime(
            profile=mdl.get("profile", RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE),
            overrides=advanced,
        )
        return {
            "model_ref": str(model_path),
            "device": str(AppConfig.DEVICE_ID),
            "dtype": _dtype_name(str(AppConfig.DTYPE_ID)),
            "low_cpu_mem_usage": bool(AppConfig.engine_low_cpu_mem_usage()),
            "max_new_tokens": int(mdl["max_new_tokens"]),
            "chunk_max_chars": int(mdl["chunk_max_chars"]),
            "profile": str(runtime["profile"]),
            "style": str(runtime["style"]),
            "num_beams": int(runtime["num_beams"]),
            "no_repeat_ngram_size": int(runtime["no_repeat_ngram_size"]),
        }


__all__ = [
    "LogFn",
    "TranslationError",
    "TranslationService",
]
