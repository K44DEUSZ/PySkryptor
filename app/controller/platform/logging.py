# app/controller/platform/logging.py
from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any, TextIO

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

_APP_FILE_HANDLER_NAME = "PySkryptorAppFile"
_CONSOLE_HANDLER_NAME = "PySkryptorConsole"
_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


@dataclass(frozen=True)
class LoggingContext:
    logger: logging.Logger
    logs_dir: Path
    app_log_path: Path
    crash_log_path: Path

class LoggingSetup:
    """File logging bootstrap + crash hooks."""

    @staticmethod
    def _normalize_level_name(level: str) -> str:
        raw = str(level or "warning").strip().lower()
        return raw if raw in _LEVEL_MAP else "warning"

    @staticmethod
    def _coerce_level(level: str) -> int:
        return _LEVEL_MAP.get(LoggingSetup._normalize_level_name(level), logging.WARNING)

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

        level = LoggingSetup._normalize_level_name(defaults_cfg.get("level", "warning"))
        if "level" in settings_cfg:
            level = LoggingSetup._normalize_level_name(settings_cfg.get("level", level))

        return file_enabled, level

    @staticmethod
    def setup(
        app_log_path: Path,
        crash_log_path: Path,
        *,
        logger_name: str = "PySkryptor",
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
        level_name = LoggingSetup._normalize_level_name(bootstrap_level)
        root.setLevel(LoggingSetup._coerce_level(level_name))

        if file_enabled:
            LoggingSetup._ensure_app_file_handler(
                root,
                app_log_path,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
        else:
            LoggingSetup._remove_named_handler(root, _APP_FILE_HANDLER_NAME)
        LoggingSetup._ensure_console_handler(root, enabled=console)

        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.NOTSET)
        logger.propagate = True

        LoggingSetup._write_startup_header(root)
        LoggingSetup._install_excepthook(root, crash_log_path)

        logging.getLogger("transformers").setLevel(logging.ERROR)

        if enable_faulthandler:
            LoggingSetup._enable_faulthandler(crash_log_path, logger=root)

        root.debug(
            "Logging bootstrap initialized. level=%s file_enabled=%s app_log=%s crash_log=%s",
            level_name,
            bool(file_enabled),
            app_log_path,
            crash_log_path,
        )

        return LoggingContext(
            logger=logger,
            logs_dir=logs_dir,
            app_log_path=app_log_path,
            crash_log_path=crash_log_path,
        )

    @staticmethod
    def apply_settings(ctx: LoggingContext, *, file_enabled: bool, level: str) -> None:
        root = logging.getLogger()

        lvl = LoggingSetup._normalize_level_name(level)
        root.setLevel(LoggingSetup._coerce_level(lvl))

        if file_enabled:
            LoggingSetup._ensure_app_file_handler(
                root,
                ctx.app_log_path,
                max_bytes=2_000_000,
                backup_count=5,
            )
        else:
            LoggingSetup._remove_named_handler(root, _APP_FILE_HANDLER_NAME)

        root.debug(
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
        handler = LoggingSetup._find_named_handler(logger, name)
        if handler is None:
            return
        try:
            logger.removeHandler(handler)
            handler.close()
        except (OSError, ValueError):
            pass

    @staticmethod
    def _ensure_app_file_handler(
        logger: logging.Logger,
        app_log_path: Path,
        *,
        max_bytes: int,
        backup_count: int,
    ) -> None:
        existing = LoggingSetup._find_named_handler(logger, _APP_FILE_HANDLER_NAME)
        if isinstance(existing, logging.handlers.RotatingFileHandler):
            current_path = Path(getattr(existing, "baseFilename", "")).resolve()
            if current_path == Path(app_log_path).resolve():
                return
            LoggingSetup._remove_named_handler(logger, _APP_FILE_HANDLER_NAME)

        fh = logging.handlers.RotatingFileHandler(
            app_log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.set_name(_APP_FILE_HANDLER_NAME)
        fh.setFormatter(LoggingSetup._create_formatter())
        logger.addHandler(fh)

    @staticmethod
    def _ensure_console_handler(logger: logging.Logger, *, enabled: bool) -> None:
        existing = LoggingSetup._find_named_handler(logger, _CONSOLE_HANDLER_NAME)
        if not enabled:
            if existing is not None:
                LoggingSetup._remove_named_handler(logger, _CONSOLE_HANDLER_NAME)
            return
        if existing is not None:
            return

        sh = logging.StreamHandler()
        sh.set_name(_CONSOLE_HANDLER_NAME)
        sh.setFormatter(LoggingSetup._create_formatter())
        logger.addHandler(sh)

    @staticmethod
    def _append_crash_entry(crash_log_path: Path, title: str, text: str) -> None:
        try:
            with Path(crash_log_path).open("a", encoding="utf-8") as f:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n--- {title} {stamp} ---\n")
                f.write(str(text or ""))
                if text and not str(text).endswith("\n"):
                    f.write("\n")
        except OSError:
            pass

    @staticmethod
    def _write_startup_header(logger: logging.Logger) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.debug("Startup session opened. started_at=%s", ts)

    @staticmethod
    def _install_excepthook(logger: logging.Logger, crash_log_path: Path) -> None:
        def _hook(exc_type, exc, tb) -> None:
            text = "".join(traceback.format_exception(exc_type, exc, tb))
            logger.error("Unhandled exception.\n%s", text)
            LoggingSetup._append_crash_entry(crash_log_path, "python crash", text)

        sys.excepthook = _hook

    @staticmethod
    def _enable_faulthandler(crash_log_path: Path, *, logger: logging.Logger | None = None) -> None:
        try:
            import faulthandler

            f: TextIO = Path(crash_log_path).open("a", encoding="utf-8")
            faulthandler.enable(file=f)

            def _close() -> None:
                try:
                    f.close()
                except OSError:
                    pass

            atexit.register(_close)
            if logger:
                logger.debug("Faulthandler enabled. path=%s", crash_log_path)
        except (ImportError, OSError, RuntimeError, AttributeError, TypeError, ValueError) as ex:
            if logger:
                logger.warning("Faulthandler enable failed. detail=%s", ex)

    @staticmethod
    def make_qt_message_handler(logger: logging.Logger, crash_log_path: Path):
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
                except Exception:
                    origin = ""

                try:
                    from PyQt5 import QtCore  # type: ignore
                    qt_debug = getattr(QtCore, "QtDebugMsg", 0)
                    qt_info = getattr(QtCore, "QtInfoMsg", None)
                    qt_warning = getattr(QtCore, "QtWarningMsg", 1)
                    qt_critical = getattr(QtCore, "QtCriticalMsg", 2)
                    qt_fatal = getattr(QtCore, "QtFatalMsg", 3)
                except Exception:
                    qt_debug = 0
                    qt_info = 4
                    qt_warning = 1
                    qt_critical = 2
                    qt_fatal = 3

                crash_title: str | None = None
                if mode == qt_debug:
                    logger.debug("[qt] %s%s", msg, origin)
                elif qt_info is not None and mode == qt_info:
                    logger.info("[qt] %s%s", msg, origin)
                elif mode == qt_warning:
                    logger.warning("[qt] %s%s", msg, origin)
                elif mode == qt_critical:
                    logger.error("[qt] %s%s", msg, origin)
                    crash_title = "qt critical"
                elif mode == qt_fatal:
                    logger.critical("[qt] %s%s", msg, origin)
                    crash_title = "qt fatal"
                else:
                    logger.info("[qt] %s%s", msg, origin)

                if crash_title is not None:
                    LoggingSetup._append_crash_entry(crash_log_path, crash_title, f"{msg}{origin}")
            except Exception:
                pass

        return _qt_handler
