# app/model/translation/gateway.py
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

_LOG = logging.getLogger(__name__)


class TranslationError(AppError):
    """Key-based error used for i18n-friendly translation failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


@dataclass
class _WorkerIO:
    """Live worker process handles shared across translation RPC calls."""

    proc: subprocess.Popen[str]
    lock: threading.Lock


@dataclass(frozen=True)
class _TranslationWorkerPolicy:
    """Timing policy for background translation worker communication."""

    poll_interval_s: float = 0.1
    ping_timeout_s: float = 15.0
    warmup_timeout_s: float = 600.0
    request_timeout_s: float = 120.0


class _ReadableTextStream(Protocol):
    """Minimal text-stream contract used by the translation worker client."""

    def readline(self) -> str: ...


class _TranslationWorkerClient:
    """Private worker transport for TranslationService RPC calls."""

    def __init__(self, *, policy: _TranslationWorkerPolicy | None = None) -> None:
        self._policy = policy or _TranslationWorkerPolicy()
        self._worker: _WorkerIO | None = None
        self._guard = threading.Lock()

    @property
    def policy(self) -> _TranslationWorkerPolicy:
        return self._policy

    def dispose(self, *, log_reason: str = "") -> None:
        with self._guard:
            io = self._worker
            self._worker = None

        if io is None:
            return

        if log_reason:
            _LOG.debug("Translation worker disposing. reason=%s", log_reason)

        proc = io.proc
        try:
            proc.kill()
        except (ProcessLookupError, OSError) as proc_ex:
            _LOG.debug("Translation worker process kill skipped. detail=%s", proc_ex)

        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                continue

    def _read_worker_line(
        self,
        stdout: _ReadableTextStream,
        *,
        timeout_s: float,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        result_queue: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

        def _reader() -> None:
            try:
                line_text = stdout.readline()
                result_queue.put(line_text)
            except BaseException as ex:
                result_queue.put(ex)

        reader = threading.Thread(target=_reader, name="translation-worker-readline", daemon=True)
        reader.start()
        deadline = time.monotonic() + max(0.1, float(timeout_s))

        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                self.dispose(log_reason="cancelled")
                raise OperationCancelled()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.dispose(log_reason="rpc_timeout")
                raise TranslationError("error.translation.no_response_from_worker")

            try:
                result = result_queue.get(timeout=min(self._policy.poll_interval_s, remaining))
            except queue.Empty:
                continue

            if not isinstance(result, str):
                self.dispose(log_reason="read_failed")
                detail = str(result) if isinstance(result, BaseException) else (
                    f"unexpected worker line type: {type(result).__name__}"
                )
                raise TranslationError("error.translation.worker_protocol_error", detail=detail)

            return result

        self.dispose(log_reason="rpc_timeout")
        raise TranslationError("error.translation.no_response_from_worker")

    def ensure_worker(self, *, log: Callable[[str], None] | None = None) -> None:
        with self._guard:
            if self._worker is not None and self._worker.proc.poll() is None:
                _LOG.debug("Translation worker reused. worker=translation")
                return

            _LOG.debug("Translation worker starting. worker=translation")
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "app.model.translation.runtime", "--worker"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=str(AppConfig.PATHS.ROOT_DIR),
                )
            except (OSError, ValueError, RuntimeError) as ex:
                self._worker = None
                _LOG.warning("Translation worker start failed. detail=%s", ex)
                raise TranslationError("error.translation.worker_start_failed", detail=str(ex))

            self._worker = _WorkerIO(proc=proc, lock=threading.Lock())

        try:
            rep = self.rpc({"cmd": "ping"}, timeout_s=self._policy.ping_timeout_s)
        except AppError:
            self.dispose(log_reason="ping_failed")
            raise
        except Exception as ex:
            self.dispose(log_reason="ping_failed")
            _LOG.warning("Translation worker ping failed. detail=%s", ex)
            raise TranslationError("error.translation.worker_ping_failed")

        ok = bool(isinstance(rep, dict) and rep.get("ok", False))
        if not ok:
            self.dispose(log_reason="ping_rejected")

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

    def rpc(
        self,
        payload: dict[str, Any],
        *,
        timeout_s: float,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        io = self._worker
        if io is None or io.proc.poll() is not None:
            raise TranslationError("error.translation.worker_not_running")

        stdin = io.proc.stdin
        stdout = io.proc.stdout
        if stdin is None or stdout is None:
            raise TranslationError("error.translation.worker_protocol_error", detail="worker stdio unavailable")

        line = json.dumps(payload, ensure_ascii=True)
        with io.lock:
            try:
                stdin.write(line + "\n")
                stdin.flush()
            except (OSError, ValueError) as ex:
                self.dispose(log_reason="write_failed")
                raise TranslationError("error.translation.worker_protocol_error", detail=str(ex))
            out = self._read_worker_line(stdout, timeout_s=timeout_s, cancel_check=cancel_check)
        if not out:
            self.dispose(log_reason="worker_eof")
            raise TranslationError("error.translation.no_response_from_worker")
        try:
            rep = json.loads(out)
        except json.JSONDecodeError as ex:
            self.dispose(log_reason="invalid_json")
            raise TranslationError("error.translation.worker_protocol_error", detail=str(ex))
        return rep if isinstance(rep, dict) else {}


_WORKER_CLIENT = _TranslationWorkerClient()
