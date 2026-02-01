# model/services/app_logging_service.py
from __future__ import annotations

import atexit
import logging
import logging.handlers
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO


@dataclass(frozen=True)
class LoggingContext:
    logger: logging.Logger
    logs_dir: Path
    app_log_path: Path
    crash_log_path: Path


class AppLoggingService:
    """
    File logging bootstrap + crash hooks.

    Intentionally UI-agnostic (no Qt imports). EntryPoint installs the Qt
    message handler using make_qt_message_handler().
    """

    DEFAULT_LOG_DIR = ".logs"
    APP_LOG_NAME = "app.log"
    CRASH_LOG_NAME = "crash.log"

    @staticmethod
    def setup(
        root_dir: Path,
        *,
        logger_name: str = "PySkryptor",
        log_dir_name: str = DEFAULT_LOG_DIR,
        max_bytes: int = 2_000_000,
        backup_count: int = 5,
        console: bool = True,
        enable_faulthandler: bool = True,
    ) -> LoggingContext:
        logs_dir = Path(root_dir) / log_dir_name
        logs_dir.mkdir(parents=True, exist_ok=True)

        app_log_path = logs_dir / AppLoggingService.APP_LOG_NAME
        crash_log_path = logs_dir / AppLoggingService.CRASH_LOG_NAME

        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        if not logger.handlers:
            fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

            fh = logging.handlers.RotatingFileHandler(
                app_log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            logger.addHandler(fh)

            if console:
                sh = logging.StreamHandler()
                sh.setFormatter(fmt)
                logger.addHandler(sh)

        AppLoggingService._write_startup_header(logger)
        AppLoggingService._install_excepthook(logger, crash_log_path)

        if enable_faulthandler:
            AppLoggingService._enable_faulthandler(crash_log_path, logger=logger)

        return LoggingContext(
            logger=logger,
            logs_dir=logs_dir,
            app_log_path=app_log_path,
            crash_log_path=crash_log_path,
        )

    @staticmethod
    def _write_startup_header(logger: logging.Logger) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("--- startup %s ---", ts)

    @staticmethod
    def _install_excepthook(logger: logging.Logger, crash_log_path: Path) -> None:
        def _hook(exc_type, exc, tb) -> None:
            text = "".join(traceback.format_exception(exc_type, exc, tb))

            logger.error("unhandled exception:\n%s", text)

            try:
                with Path(crash_log_path).open("a", encoding="utf-8") as f:
                    f.write("\n--- python crash {} ---\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    f.write(text)
                    f.write("\n")
            except Exception:
                pass

        sys.excepthook = _hook

    @staticmethod
    def _enable_faulthandler(crash_log_path: Path, *, logger: Optional[logging.Logger] = None) -> None:
        try:
            import faulthandler

            f: TextIO = Path(crash_log_path).open("a", encoding="utf-8")
            faulthandler.enable(file=f)

            def _close() -> None:
                try:
                    f.close()
                except Exception:
                    pass

            atexit.register(_close)
            if logger:
                logger.info("faulthandler enabled -> %s", crash_log_path)
        except Exception as ex:
            if logger:
                logger.warning("faulthandler enable failed: %s", ex)

    @staticmethod
    def make_qt_message_handler(logger: logging.Logger, crash_log_path: Path):
        """
        Return a function compatible with QtCore.qInstallMessageHandler.

        We mirror Qt messages into crash.log as well, because native/platform
        issues often show up only there.
        """
        def _qt_handler(mode, context, message) -> None:
            try:
                msg = str(message)
                logger.warning("[qt] %s", msg)

                try:
                    with Path(crash_log_path).open("a", encoding="utf-8") as f:
                        f.write("[qt] " + msg + "\n")
                except Exception:
                    pass
            except Exception:
                pass

        return _qt_handler
