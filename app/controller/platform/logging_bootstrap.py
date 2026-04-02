# app/controller/platform/logging_bootstrap.py
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.model.core.config.meta import AppMeta
from app.model.core.runtime.runtime_logging import (
    append_crash_entry,
    configure_external_library_logging,
    enable_process_faulthandler,
    install_process_excepthook,
    log_level_from_name,
    normalize_log_level_name,
)

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

_LOG = logging.getLogger(__name__)

_APP_FILE_HANDLER_NAME = f"{AppMeta.NAME}AppFile"
_CONSOLE_HANDLER_NAME = f"{AppMeta.NAME}Console"


@dataclass(frozen=True)
class LoggingContext:
    """Resolved logging paths used during bootstrap."""

    logs_dir: Path
    app_log_path: Path
    crash_log_path: Path


class LoggingBootstrap:
    """Main-process logging sinks and crash hooks."""

    @staticmethod
    def read_bootstrap_settings(defaults_path: Path, settings_path: Path) -> tuple[bool, str]:
        def _load_json(path: Path) -> dict[str, Any]:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, JSONDecodeError, TypeError, ValueError):
                return {}
            return data if isinstance(data, dict) else {}

        def _extract_logging_cfg(doc: dict[str, Any]) -> dict[str, Any]:
            app_cfg = doc.get("app")
            if not isinstance(app_cfg, dict):
                return {}
            logging_cfg = app_cfg.get("logging")
            return logging_cfg if isinstance(logging_cfg, dict) else {}

        defaults_cfg = _extract_logging_cfg(_load_json(Path(defaults_path)))
        settings_cfg = _extract_logging_cfg(_load_json(Path(settings_path)))

        file_enabled = bool(defaults_cfg.get("enabled", True))
        if "enabled" in settings_cfg:
            file_enabled = bool(settings_cfg.get("enabled", True))

        level = normalize_log_level_name(defaults_cfg.get("level", "warning"), default="warning")
        if "level" in settings_cfg:
            level = normalize_log_level_name(settings_cfg.get("level", level), default=level)

        return file_enabled, level

    @staticmethod
    def setup(
        app_log_path: Path,
        crash_log_path: Path,
        *,
        max_bytes: int = 2_000_000,
        backup_count: int = 5,
        console: bool = True,
        file_enabled: bool = True,
        bootstrap_level: str = "warning",
        enable_faulthandler: bool = True,
    ) -> LoggingContext:
        app_log_path = Path(app_log_path)
        crash_log_path = Path(crash_log_path)
        logs_dir = app_log_path.parent
        logs_dir.mkdir(parents=True, exist_ok=True)
        crash_log_path.parent.mkdir(parents=True, exist_ok=True)

        root = logging.getLogger()
        level_name = normalize_log_level_name(bootstrap_level, default="warning")
        root.setLevel(log_level_from_name(level_name, default="warning"))

        if file_enabled:
            LoggingBootstrap._ensure_app_file_handler(
                root,
                app_log_path,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
        else:
            LoggingBootstrap._remove_named_handler(root, _APP_FILE_HANDLER_NAME)
        LoggingBootstrap._ensure_console_handler(root, enabled=console)

        LoggingBootstrap._write_startup_header()
        install_process_excepthook(crash_log_path, logger=_LOG)

        configure_external_library_logging(logger=_LOG)

        if enable_faulthandler:
            enable_process_faulthandler(crash_log_path, logger=_LOG)

        _LOG.debug(
            "Logging bootstrap initialized. level=%s file_enabled=%s app_log=%s crash_log=%s",
            level_name,
            bool(file_enabled),
            app_log_path,
            crash_log_path,
        )

        return LoggingContext(
            logs_dir=logs_dir,
            app_log_path=app_log_path,
            crash_log_path=crash_log_path,
        )

    @staticmethod
    def apply_settings(ctx: LoggingContext, *, file_enabled: bool, level: str) -> None:
        root = logging.getLogger()

        lvl = normalize_log_level_name(level, default="warning")
        root.setLevel(log_level_from_name(lvl, default="warning"))

        if file_enabled:
            LoggingBootstrap._ensure_app_file_handler(
                root,
                ctx.app_log_path,
                max_bytes=2_000_000,
                backup_count=5,
            )
        else:
            LoggingBootstrap._remove_named_handler(root, _APP_FILE_HANDLER_NAME)

        _LOG.debug(
            "Logging settings applied. level=%s file_enabled=%s app_log=%s",
            lvl,
            bool(file_enabled),
            ctx.app_log_path,
        )

    @staticmethod
    def _create_formatter() -> logging.Formatter:
        return logging.Formatter(LOG_FORMAT)

    @staticmethod
    def _find_named_handler(logger: logging.Logger, name: str) -> logging.Handler | None:
        for handler in list(logger.handlers):
            if getattr(handler, "get_name", None) and handler.get_name() == name:
                return handler
        return None

    @staticmethod
    def _remove_named_handler(logger: logging.Logger, name: str) -> None:
        handler = LoggingBootstrap._find_named_handler(logger, name)
        if handler is None:
            return
        try:
            logger.removeHandler(handler)
            handler.close()
        except (OSError, RuntimeError, ValueError) as ex:
            _LOG.debug("Named handler removal skipped. name=%s detail=%s", name, ex)

    @staticmethod
    def _ensure_app_file_handler(
        logger: logging.Logger,
        app_log_path: Path,
        *,
        max_bytes: int,
        backup_count: int,
    ) -> None:
        existing = LoggingBootstrap._find_named_handler(logger, _APP_FILE_HANDLER_NAME)
        if isinstance(existing, logging.handlers.RotatingFileHandler):
            current_path = Path(getattr(existing, "baseFilename", "")).resolve()
            if current_path == Path(app_log_path).resolve():
                return
            LoggingBootstrap._remove_named_handler(logger, _APP_FILE_HANDLER_NAME)

        fh = logging.handlers.RotatingFileHandler(
            app_log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.set_name(_APP_FILE_HANDLER_NAME)
        fh.setFormatter(LoggingBootstrap._create_formatter())
        logger.addHandler(fh)

    @staticmethod
    def _ensure_console_handler(logger: logging.Logger, *, enabled: bool) -> None:
        existing = LoggingBootstrap._find_named_handler(logger, _CONSOLE_HANDLER_NAME)
        if not enabled:
            if existing is not None:
                LoggingBootstrap._remove_named_handler(logger, _CONSOLE_HANDLER_NAME)
            return
        if existing is not None:
            return

        sh = logging.StreamHandler()
        sh.set_name(_CONSOLE_HANDLER_NAME)
        sh.setFormatter(LoggingBootstrap._create_formatter())
        logger.addHandler(sh)

    @staticmethod
    def _write_startup_header() -> None:
        _LOG.debug("Startup session opened.")

    @staticmethod
    def make_qt_message_handler(crash_log_path: Path):
        """Return a function compatible with QtCore.qInstallMessageHandler."""

        def _qt_handler(mode, context, message) -> None:
            try:
                msg = str(message)
                origin = ""
                try:
                    file_name = str(getattr(context, "file", "") or "").strip()
                    line_no = int(getattr(context, "line", 0) or 0)
                    func_name = str(getattr(context, "function", "") or "").strip()
                    parts = []
                    if file_name:
                        parts.append(file_name)
                    if line_no > 0:
                        parts.append(str(line_no))
                    if func_name:
                        parts.append(func_name)
                    if parts:
                        origin = " (" + ":".join(parts) + ")"
                except (AttributeError, TypeError, ValueError):
                    origin = ""

                try:
                    from PyQt5 import QtCore  # type: ignore
                    qt_debug = getattr(QtCore, "QtDebugMsg", 0)
                    qt_info = getattr(QtCore, "QtInfoMsg", None)
                    qt_warning = getattr(QtCore, "QtWarningMsg", 1)
                    qt_critical = getattr(QtCore, "QtCriticalMsg", 2)
                    qt_fatal = getattr(QtCore, "QtFatalMsg", 3)
                except (ImportError, AttributeError, TypeError, ValueError):
                    qt_debug = 0
                    qt_info = 4
                    qt_warning = 1
                    qt_critical = 2
                    qt_fatal = 3

                crash_title: str | None = None
                if mode == qt_debug:
                    _LOG.debug("[qt-raw] %s%s", msg, origin)
                elif qt_info is not None and mode == qt_info:
                    _LOG.info("[qt-raw] %s%s", msg, origin)
                elif mode == qt_warning:
                    _LOG.warning("[qt-raw] %s%s", msg, origin)
                elif mode == qt_critical:
                    _LOG.error("[qt-critical] %s%s", msg, origin)
                    crash_title = "qt critical"
                elif mode == qt_fatal:
                    _LOG.error("[qt-fatal] %s%s", msg, origin)
                    crash_title = "qt fatal"
                else:
                    _LOG.info("[qt-raw] %s%s", msg, origin)

                if crash_title is not None:
                    append_crash_entry(crash_log_path, crash_title, f"{msg}{origin}")
            except Exception as ex:
                try:
                    sys.stderr.write(f"Qt message handler failure: {ex}\n")
                except OSError:
                    return

        return _qt_handler
