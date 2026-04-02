# app/model/engines/host_main.py
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from typing import Any, Callable

from app.model.core.config.config import AppConfig
from app.model.core.domain.errors import AppError
from app.model.core.runtime.bootstrap import resolve_runtime_roots
from app.model.core.runtime.runtime_logging import (
    configure_external_library_logging,
    configure_structured_stderr_logging,
    enable_process_faulthandler,
    install_process_excepthook,
)
from app.model.core.utils.progress_utils import clamp_progress_pct
from app.model.engines.runtime_config import apply_engine_runtime
from app.model.settings.service import SettingsService
from app.model.settings.validation import SettingsError
from app.model.transcription.host_runtime import TranscriptionHostRuntime
from app.model.translation.host_runtime import TranslationHostRuntime

_LOG = logging.getLogger("app.model.engines.host_main")


def _configure_runtime(role: str) -> None:
    bundle_root, install_root = resolve_runtime_roots(__file__, unfrozen_parent_index=3)
    AppConfig.set_root_dir(bundle_root, install_root=install_root)
    AppConfig.ensure_dirs()
    crash_metadata = {"role": role, "pid": os.getpid()}

    configure_structured_stderr_logging(role=role)
    install_process_excepthook(
        AppConfig.PATHS.CRASH_LOG_PATH,
        title="engine python crash",
        logger=_LOG,
        metadata=crash_metadata,
    )
    enable_process_faulthandler(
        AppConfig.PATHS.CRASH_LOG_PATH,
        logger=_LOG,
        metadata=crash_metadata,
    )
    configure_external_library_logging(logger=_LOG)

    settings_service = SettingsService()
    try:
        settings = settings_service.load()
    except SettingsError as ex:
        if str(ex.key) != "error.settings.settings_missing":
            raise
        settings = settings_service.restore_defaults()
    AppConfig.initialize_from_snapshot(settings)
    apply_engine_runtime(settings.engine)
    _LOG.debug("Engine host configured. role=%s pid=%s cwd=%s", role, os.getpid(), os.getcwd())


def _runtime_for_role(role: str) -> Any:
    if role == "transcription":
        return TranscriptionHostRuntime()
    if role == "translation":
        return TranslationHostRuntime()
    raise ValueError(f"Unsupported engine role: {role}")


def _reply_ok(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, "payload": dict(payload or {})}


def _reply_error(ex: BaseException) -> dict[str, Any]:
    if isinstance(ex, AppError):
        return {
            "ok": False,
            "error_key": str(ex.key),
            "error_params": dict(ex.params or {}),
            "detail": str(ex.cause or ex.key),
        }
    return {"ok": False, "detail": str(ex)}


def _write_stdout_message(message: dict[str, Any], *, write_lock: threading.Lock) -> None:
    with write_lock:
        sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def _write_stdout_event(event_name: str, payload: dict[str, Any], *, write_lock: threading.Lock) -> None:
    _write_stdout_message(
        {
            "event": str(event_name or "").strip().lower(),
            "payload": dict(payload or {}),
        },
        write_lock=write_lock,
    )


def _progress_event_payload(pct: int) -> dict[str, int]:
    return {"pct": clamp_progress_pct(pct)}


def _build_progress_event_callback(
    emit_event: Callable[[str, dict[str, Any]], None] | None,
) -> Callable[[int], None] | None:
    if emit_event is None:
        return None

    def _forward_progress(pct: int) -> None:
        emit_event("progress", _progress_event_payload(pct))

    return _forward_progress


def _handle_request(
    runtime: Any,
    role: str,
    request: dict[str, Any],
    *,
    emit_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    cmd = str(request.get("cmd") or "").strip().lower()
    if cmd == "ping":
        return _reply_ok({"role": role, "pid": os.getpid()})
    if cmd == "warmup":
        runtime.warmup()
        return _reply_ok(runtime.health())
    if cmd == "health":
        health = dict(runtime.health() or {})
        health.setdefault("role", role)
        health.setdefault("pid", os.getpid())
        return _reply_ok(health)
    if cmd == "shutdown":
        return _reply_ok({"role": role, "pid": os.getpid(), "shutdown": True})
    if cmd == "transcribe_wav" and role == "transcription":
        progress_cb = _build_progress_event_callback(emit_event)
        return _reply_ok(runtime.transcribe_wav(request, progress_cb=progress_cb))
    if cmd == "recognize_audio" and role == "transcription":
        return _reply_ok(runtime.recognize_audio(request))
    if cmd == "translate_text" and role == "translation":
        return _reply_ok({"text": runtime.translate_text(request)})
    return {"ok": False, "detail": f"unsupported command '{cmd}' for role '{role}'"}


def _run_host(role: str) -> int:
    _configure_runtime(role)
    runtime = _runtime_for_role(role)
    stdout_lock = threading.Lock()

    for raw_line in sys.stdin:
        line = str(raw_line or "").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            request = request if isinstance(request, dict) else {}
        except json.JSONDecodeError as ex:
            reply = {"ok": False, "detail": str(ex)}
        else:
            try:
                reply = _handle_request(
                    runtime,
                    role,
                    request,
                    emit_event=lambda event_name, payload: _write_stdout_event(
                        event_name,
                        payload,
                        write_lock=stdout_lock,
                    ),
                )
            except BaseException as ex:
                _LOG.error("Engine host request failed. role=%s cmd=%s", role, request.get("cmd"), exc_info=True)
                reply = _reply_error(ex)

        _write_stdout_message(reply, write_lock=stdout_lock)
        if bool(reply.get("ok", False)) and bool((reply.get("payload") or {}).get("shutdown")):
            break
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the engine-host CLI arguments."""

    parser = argparse.ArgumentParser(prog="AIModelHost")
    parser.add_argument("--engine", required=True, choices=("transcription", "translation"))
    return parser.parse_args(argv)


def cli_entry(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    return _run_host(str(args.engine))


if __name__ == "__main__":
    raise SystemExit(cli_entry())
