# app/model/engines/client.py
from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from app.model.core.config.config import AppConfig
from app.model.core.domain.errors import AppError, OperationCancelled
from app.model.core.runtime.runtime_logging import log_level_from_name, parse_structured_stderr_event
from app.model.core.utils.progress_utils import parse_progress_pct
from app.model.engines.types import (
    EngineHealth,
    RecognizeAudioRequest,
    RecognizeAudioResult,
    TranscribeWavRequest,
    TranscribeWavResult,
    TranslateTextRequest,
)

_LOG = logging.getLogger(__name__)

_HostEventHandler = Callable[[str, dict[str, Any]], None]


class EngineClientError(AppError):
    """Key-based error raised for engine-host transport failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


@dataclass
class _HostIO:
    """Live subprocess handles shared across host RPC calls."""

    proc: subprocess.Popen[str]
    lock: threading.Lock


@dataclass(frozen=True)
class _HostPolicy:
    """Timing policy for engine-host process communication."""

    poll_interval_s: float = 0.1
    ping_timeout_s: float = 15.0
    warmup_timeout_s: float = 600.0
    request_timeout_s: float = 600.0


class _ReadableTextStreamProtocol(Protocol):
    """Minimal text-stream contract used by the engine-host client."""

    def readline(self) -> str: ...


class _EngineHostClient:
    """Generic newline-delimited JSON client for a single engine-host role."""

    def __init__(self, role: str, *, policy: _HostPolicy | None = None) -> None:
        self._role = str(role or "").strip().lower()
        self._policy = policy or _HostPolicy()
        self._host: _HostIO | None = None
        self._guard = threading.Lock()

    @property
    def role(self) -> str:
        return self._role

    @property
    def policy(self) -> _HostPolicy:
        return self._policy

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            host_exe = AppConfig.PATHS.ENGINE_HOST_EXE
            if not host_exe.exists():
                raise EngineClientError("error.engine.host_missing", path=str(host_exe))
            return [str(host_exe), "--engine", self._role]
        return [sys.executable, "-m", "app.model.engines.host_main", "--engine", self._role]

    def dispose(self, *, log_reason: str = "") -> None:
        with self._guard:
            io = self._host
            self._host = None

        if io is None:
            return

        if log_reason:
            _LOG.debug("Engine host disposing. role=%s reason=%s", self._role, log_reason)

        proc = io.proc
        try:
            proc.kill()
        except (ProcessLookupError, OSError) as proc_ex:
            _LOG.debug("Engine host process kill skipped. role=%s detail=%s", self._role, proc_ex)

        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                continue

    def _read_line(
        self,
        stdout: _ReadableTextStreamProtocol,
        *,
        timeout_s: float,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        result_queue: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

        def _reader() -> None:
            try:
                result_queue.put(stdout.readline())
            except BaseException as ex:
                result_queue.put(ex)

        threading.Thread(target=_reader, name=f"engine-host-readline-{self._role}", daemon=True).start()
        deadline = time.monotonic() + max(0.1, float(timeout_s))

        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                self.dispose(log_reason="cancelled")
                raise OperationCancelled()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.dispose(log_reason="rpc_timeout")
                raise EngineClientError("error.engine.no_response_from_host", role=self._role)

            try:
                result = result_queue.get(timeout=min(self._policy.poll_interval_s, remaining))
            except queue.Empty:
                continue

            if not isinstance(result, str):
                self.dispose(log_reason="read_failed")
                detail = str(result) if isinstance(result, BaseException) else type(result).__name__
                raise EngineClientError("error.engine.host_protocol_error", role=self._role, detail=detail)

            return result

        self.dispose(log_reason="rpc_timeout")
        raise EngineClientError("error.engine.no_response_from_host", role=self._role)

    @staticmethod
    def _event_payload(raw_payload: Any) -> dict[str, Any]:
        return dict(raw_payload or {}) if isinstance(raw_payload, dict) else {}

    def _dispatch_request_event(
        self,
        message: dict[str, Any],
        *,
        event_handler: _HostEventHandler | None,
    ) -> bool:
        event_name = str(message.get("event") or "").strip().lower()
        if not event_name or "ok" in message:
            return False
        if event_handler is not None:
            event_handler(event_name, self._event_payload(message.get("payload")))
        return True

    @staticmethod
    def _parse_progress_event_pct(event_name: str, payload: dict[str, Any]) -> int | None:
        if str(event_name or "").strip().lower() != "progress":
            return None
        return parse_progress_pct(payload.get("pct"))

    def _forward_stderr_line(self, raw_line: str) -> None:
        event = parse_structured_stderr_event(raw_line, fallback_role=self._role)
        if event is None:
            return

        level = log_level_from_name(event.level, default="info")
        prefix = f"[engine:{event.role}"
        if isinstance(event.pid, int):
            prefix += f" pid={event.pid}"
        prefix += "]"
        body = f"{prefix} {event.message}".strip()
        if event.exc_text:
            body = f"{body}\n{event.exc_text}"

        target_name = f"app.engine.{event.role}"
        if event.logger:
            target_name = f"{target_name}.{event.logger}"
        logging.getLogger(target_name).log(level, body)

    def _start_stderr_forwarder(self, io: _HostIO) -> None:
        stderr = io.proc.stderr
        if stderr is None:
            return

        def _reader() -> None:
            try:
                for raw_line in stderr:
                    self._forward_stderr_line(raw_line)
            except (OSError, ValueError):
                return

        threading.Thread(target=_reader, name=f"engine-host-stderr-{self._role}", daemon=True).start()

    def ensure_started(self) -> None:
        with self._guard:
            if self._host is not None and self._host.proc.poll() is None:
                return

            try:
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
                proc = subprocess.Popen(
                    self._command(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=str(AppConfig.PATHS.INSTALL_ROOT_DIR),
                    creationflags=creation_flags,
                )
            except (OSError, ValueError, RuntimeError) as ex:
                self._host = None
                raise EngineClientError("error.engine.host_start_failed", role=self._role, detail=str(ex))

            self._host = _HostIO(proc=proc, lock=threading.Lock())
            self._start_stderr_forwarder(self._host)

        reply = self.request({"cmd": "ping"}, timeout_s=self._policy.ping_timeout_s)
        if not bool(reply.get("ok", False)):
            self.dispose(log_reason="ping_failed")
            self._raise_reply_error(reply, fallback_key="error.engine.host_ping_failed")

    def request(
        self,
        payload: dict[str, Any],
        *,
        timeout_s: float,
        cancel_check: Callable[[], bool] | None = None,
        event_handler: _HostEventHandler | None = None,
    ) -> dict[str, Any]:
        io = self._host
        if io is None or io.proc.poll() is not None:
            raise EngineClientError("error.engine.host_not_running", role=self._role)

        stdin = io.proc.stdin
        stdout = io.proc.stdout
        if stdin is None or stdout is None:
            raise EngineClientError("error.engine.host_protocol_error", role=self._role, detail="host stdio missing")

        line = json.dumps(payload, ensure_ascii=True)
        with io.lock:
            try:
                stdin.write(line + "\n")
                stdin.flush()
            except (OSError, ValueError) as ex:
                self.dispose(log_reason="write_failed")
                raise EngineClientError("error.engine.host_protocol_error", role=self._role, detail=str(ex))
            while True:
                out = self._read_line(stdout, timeout_s=timeout_s, cancel_check=cancel_check)
                if not out:
                    self.dispose(log_reason="host_eof")
                    raise EngineClientError("error.engine.no_response_from_host", role=self._role)

                try:
                    reply = json.loads(out)
                except json.JSONDecodeError as ex:
                    self.dispose(log_reason="invalid_json")
                    raise EngineClientError("error.engine.host_protocol_error", role=self._role, detail=str(ex))
                if not isinstance(reply, dict):
                    return {}
                if self._dispatch_request_event(reply, event_handler=event_handler):
                    continue
                return reply

    def warmup(self) -> None:
        self.ensure_started()
        reply = self.request({"cmd": "warmup"}, timeout_s=self._policy.warmup_timeout_s)
        if not bool(reply.get("ok", False)):
            self.dispose(log_reason="warmup_failed")
            self._raise_reply_error(reply, fallback_key="error.engine.host_warmup_failed")

    def health(self) -> EngineHealth:
        self.ensure_started()
        reply = self.request({"cmd": "health"}, timeout_s=self._policy.request_timeout_s)
        if not bool(reply.get("ok", False)):
            self._raise_reply_error(reply, fallback_key="error.engine.host_health_failed")
        payload = reply.get("payload") if isinstance(reply.get("payload"), dict) else {}
        return EngineHealth(
            role=str(payload.get("role") or self._role),
            ready=bool(payload.get("ready", False)),
            pid=int(payload["pid"]) if isinstance(payload.get("pid"), int) else None,
            details=dict(payload),
        )

    def shutdown(self) -> None:
        io = self._host
        if io is None or io.proc.poll() is not None:
            self.dispose(log_reason="shutdown_skipped")
            return

        try:
            self.request({"cmd": "shutdown"}, timeout_s=5.0)
        except (AppError, OSError, RuntimeError, TypeError, ValueError):
            pass
        self.dispose(log_reason="shutdown")

    def _raise_reply_error(self, reply: dict[str, Any], *, fallback_key: str) -> None:
        error_key = str(reply.get("error_key") or "").strip()
        error_params = reply.get("error_params") if isinstance(reply.get("error_params"), dict) else {}
        if error_key:
            raise AppError(error_key, dict(error_params or {}))
        detail = str(reply.get("detail") or reply.get("error") or reply.get("code") or "").strip()
        raise EngineClientError(fallback_key, role=self._role, detail=detail or self._role)


class TranscriptionEngineClient(_EngineHostClient):
    """Process-backed transcription engine handle."""

    def __init__(self, *, policy: _HostPolicy | None = None) -> None:
        super().__init__("transcription", policy=policy)

    def transcribe_wav(
        self,
        request: TranscribeWavRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
        progress_cb: Callable[[int], None] | None = None,
    ) -> TranscribeWavResult:
        self.ensure_started()

        def _handle_event(event_name: str, payload: dict[str, Any]) -> None:
            if progress_cb is None:
                return
            pct = self._parse_progress_event_pct(event_name, payload)
            if pct is None:
                return
            progress_cb(pct)

        reply = self.request(
            {"cmd": "transcribe_wav", **request.payload()},
            timeout_s=self.policy.request_timeout_s,
            cancel_check=cancel_check,
            event_handler=_handle_event,
        )
        if not bool(reply.get("ok", False)):
            self._raise_reply_error(reply, fallback_key="error.transcription.asr_failed")
        payload = reply.get("payload") if isinstance(reply.get("payload"), dict) else {}
        segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
        return TranscribeWavResult(
            merged_text=str(payload.get("merged_text") or ""),
            segments=[dict(item or {}) for item in segments if isinstance(item, dict)],
            detected_language=str(payload.get("detected_language") or ""),
        )

    def recognize_audio(
        self,
        request: RecognizeAudioRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> RecognizeAudioResult:
        self.ensure_started()
        reply = self.request(
            {"cmd": "recognize_audio", **request.payload()},
            timeout_s=self.policy.request_timeout_s,
            cancel_check=cancel_check,
        )
        if not bool(reply.get("ok", False)):
            self._raise_reply_error(reply, fallback_key="error.transcription.asr_failed")
        payload = reply.get("payload") if isinstance(reply.get("payload"), dict) else {}
        return RecognizeAudioResult(
            text=str(payload.get("text") or ""),
            detected_language=str(payload.get("detected_language") or ""),
        )


class TranslationEngineClient(_EngineHostClient):
    """Process-backed translation engine handle."""

    def __init__(self, *, policy: _HostPolicy | None = None) -> None:
        super().__init__("translation", policy=policy)

    def translate_text(
        self,
        request: TranslateTextRequest,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        self.ensure_started()
        reply = self.request(
            {"cmd": "translate_text", **request.payload()},
            timeout_s=self.policy.request_timeout_s,
            cancel_check=cancel_check,
        )
        if not bool(reply.get("ok", False)):
            self._raise_reply_error(reply, fallback_key="error.translation.worker_error")
        payload = reply.get("payload") if isinstance(reply.get("payload"), dict) else {}
        return str(payload.get("text") or "").strip()
