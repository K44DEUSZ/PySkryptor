# app/model/core/runtime/runtime_logging.py
from __future__ import annotations

import atexit
import json
import logging
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

_LOG = logging.getLogger(__name__)

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


@dataclass(frozen=True)
class StructuredLogEvent:
    """Normalized log event transferred between engine hosts and the main process."""

    role: str
    logger: str
    level: str
    message: str
    pid: int | None = None
    exc_text: str = ""


def _metadata_suffix(metadata: dict[str, Any] | None) -> str:
    values = dict(metadata or {})
    tokens = [f"{key}={value}" for key, value in values.items() if str(key).strip() and value not in (None, "")]
    return f" [{' '.join(tokens)}]" if tokens else ""


def normalize_log_level_name(level: str, *, default: str = "warning") -> str:
    """Normalize a user-facing level name to one of: debug/info/warning/error."""

    fallback = str(default or "warning").strip().lower()
    if fallback == "critical":
        fallback = "error"
    if fallback not in _LEVEL_MAP:
        fallback = "warning"

    raw = str(level or "").strip().lower()
    if raw == "critical":
        return "error"
    return raw if raw in _LEVEL_MAP else fallback


def log_level_name_from_value(level: int | str, *, default: str = "warning") -> str:
    """Resolve either a stdlib level or a text label to a normalized level name."""

    if isinstance(level, str):
        return normalize_log_level_name(level, default=default)

    try:
        numeric = int(level)
    except (TypeError, ValueError):
        return normalize_log_level_name(default, default="warning")

    if numeric >= logging.ERROR:
        return "error"
    if numeric >= logging.WARNING:
        return "warning"
    if numeric >= logging.INFO:
        return "info"
    return "debug"


def append_crash_entry(
    crash_log_path: Path,
    title: str,
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a crash entry with optional process metadata."""

    try:
        with Path(crash_log_path).open("a", encoding="utf-8") as handle:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"\n--- {str(title or '').strip()}{_metadata_suffix(metadata)} {stamp} ---\n")
            handle.write(str(text or ""))
            if text and not str(text).endswith("\n"):
                handle.write("\n")
    except OSError as ex:
        _LOG.debug("Crash log append skipped. path=%s detail=%s", crash_log_path, ex)


def install_process_excepthook(
    crash_log_path: Path,
    *,
    title: str = "python crash",
    logger: logging.Logger | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Install a process-level excepthook that appends to the shared crash log."""

    def _hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if logger is not None:
            logger.error("Unhandled exception.\n%s", text)
        append_crash_entry(crash_log_path, title, text, metadata=metadata)

    sys.excepthook = _hook


# noinspection SpellCheckingInspection
def enable_process_faulthandler(
    crash_log_path: Path,
    *,
    logger: logging.Logger | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Enable the fault handler for the current process and route dumps to the shared crash log."""

    try:
        # noinspection SpellCheckingInspection
        import faulthandler

        file_handle: TextIO = Path(crash_log_path).open("a", encoding="utf-8")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file_handle.write(
            f"\n--- fault-handler session{_metadata_suffix(metadata)} {stamp} ---\n"
        )
        file_handle.flush()
        # noinspection SpellCheckingInspection
        faulthandler.enable(file=file_handle)

        def _close() -> None:
            try:
                file_handle.close()
            except OSError as close_ex:
                if logger is not None:
                    logger.debug("Fault handler file close skipped. path=%s detail=%s", crash_log_path, close_ex)

        atexit.register(_close)
        if logger is not None:
            logger.debug("Fault handler enabled. path=%s", crash_log_path)
    except (ImportError, OSError, RuntimeError, AttributeError, TypeError, ValueError) as ex:
        if logger is not None:
            logger.warning("Fault handler enable failed. detail=%s", ex)


class StructuredStderrHandler(logging.Handler):
    """Emit structured single-line JSON log events to stderr."""

    def __init__(self, *, role: str) -> None:
        super().__init__()
        self._role = str(role or "").strip().lower() or "engine"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = structured_log_payload_from_record(record, role=self._role)
            if record.exc_info is not None:
                formatter = self.formatter or logging.Formatter("%(message)s")
                payload["exc_text"] = formatter.formatException(record.exc_info)
            sys.stderr.write(json.dumps(payload, ensure_ascii=True) + "\n")
            sys.stderr.flush()
        except (OSError, RuntimeError, TypeError, ValueError):
            self.handleError(record)


def configure_structured_stderr_logging(*, role: str, level: int = logging.DEBUG) -> None:
    """Configure the current process to emit standard logging events to structured stderr."""

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except (OSError, RuntimeError, ValueError):
            continue

    handler = StructuredStderrHandler(role=role)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(int(level))


def configure_external_library_logging(*, logger: logging.Logger | None = None) -> None:
    """Clamp noisy third-party logging and route Python warnings through stdlib logging."""

    try:
        logging.captureWarnings(True)
    except (RuntimeError, TypeError, ValueError) as ex:
        if logger is not None:
            logger.debug("Python warning capture setup skipped. detail=%s", ex)

    try:
        logging.getLogger("transformers").setLevel(logging.ERROR)
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
        if logger is not None:
            logger.debug("External library logging clamped. library=transformers")
    except (ImportError, AttributeError, RuntimeError, TypeError, ValueError) as ex:
        if logger is not None:
            logger.debug("External library logging clamp skipped. library=transformers detail=%s", ex)


def structured_log_payload_from_record(record: logging.LogRecord, *, role: str) -> dict[str, Any]:
    """Build the normalized structured payload written by engine hosts."""

    return {
        "event": "log",
        "role": str(role or "").strip().lower() or "engine",
        "pid": os.getpid(),
        "level": log_level_name_from_value(record.levelno, default="info"),
        "logger": str(record.name or "").strip(),
        "message": str(record.getMessage() or ""),
    }


def classify_unstructured_stderr_level_name(line: str) -> str | None:
    """Classify raw stderr text to a normalized level name when structure is missing."""

    text = str(line or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("traceback") or any(
        token in lowered
        for token in (
            " error",
            "error:",
            " exception",
            "exception:",
            " failed",
            "fatal",
            "critical",
        )
    ):
        return "error"
    if any(token in lowered for token in ("warning", "deprecated")):
        return "warning"
    return "info"


def parse_structured_stderr_event(raw_line: str, *, fallback_role: str) -> StructuredLogEvent | None:
    """Parse either a structured stderr JSON event or a raw fallback line."""

    line = str(raw_line or "").strip()
    if not line:
        return None

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        level_name = classify_unstructured_stderr_level_name(line)
        if level_name is None:
            return None
        return StructuredLogEvent(
            role=str(fallback_role or "").strip().lower() or "engine",
            logger="stderr",
            level=level_name,
            message=line,
        )

    if not isinstance(payload, dict) or str(payload.get("event") or "").strip().lower() != "log":
        level_name = classify_unstructured_stderr_level_name(line)
        if level_name is None:
            return None
        return StructuredLogEvent(
            role=str(fallback_role or "").strip().lower() or "engine",
            logger="stderr",
            level=level_name,
            message=line,
        )

    role = str(payload.get("role") or fallback_role).strip().lower()
    if not role:
        role = str(fallback_role or "").strip().lower() or "engine"
    logger_name = str(payload.get("logger") or "").strip()
    level_name = normalize_log_level_name(str(payload.get("level") or "info"), default="info")
    message = str(payload.get("message") or "")
    pid = int(payload["pid"]) if isinstance(payload.get("pid"), int) else None
    exc_text = str(payload.get("exc_text") or "").strip()
    return StructuredLogEvent(
        role=role,
        logger=logger_name,
        level=level_name,
        message=message,
        pid=pid,
        exc_text=exc_text,
    )


def log_level_from_name(level: str, *, default: int | str = logging.WARNING) -> int:
    """Resolve a user-facing level name to a stdlib logging constant."""

    default_name = log_level_name_from_value(default, default="warning")
    return _LEVEL_MAP[normalize_log_level_name(level, default=default_name)]
