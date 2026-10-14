# app/core/infrastructure/command_runner.py
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from app.core.domain.errors import AppError

_LOG = logging.getLogger(__name__)


@dataclass
class CommandResult:
  """Result of a completed command execution."""

  cmd: list[str]
  returncode: int
  stdout: str
  stderr: str


class CommandRunner:
  """Runs external commands with consistent logging, cancel and error handling."""

  @staticmethod
  def run(
    cmd: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
    error_key: str = "error.external_tool_failed",
  ) -> CommandResult:
    cmd_list = [str(x) for x in cmd]
    _LOG.debug("Running command. cmd=%s", " ".join(cmd_list))

    try:
      proc = subprocess.Popen(
        cmd_list,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
      )

      stdout, stderr = CommandRunner._communicate(proc, timeout_s=timeout_s, cancel_check=cancel_check)
      res = CommandResult(
        cmd=cmd_list,
        returncode=int(proc.returncode or 0),
        stdout=str(stdout or ""),
        stderr=str(stderr or ""),
      )

      if res.returncode != 0:
        raise AppError(
          key=error_key,
          params={"code": res.returncode, "cmd": " ".join(cmd_list), "stderr": res.stderr[-8000:]},
        )

      return res
    except AppError:
      raise
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as ex:
      raise AppError(key=error_key, params={"cmd": " ".join(cmd_list)}, cause=ex)

  @staticmethod
  def _communicate(
    proc: subprocess.Popen,
    *,
    timeout_s: float | None,
    cancel_check: Callable[[], bool] | None,
  ) -> tuple[str, str]:
    if timeout_s is None and cancel_check is None:
      out, err = proc.communicate()
      return str(out or ""), str(err or "")

    poll_interval_s = 0.1
    started_at = time.monotonic()

    while True:
      if cancel_check is not None and bool(cancel_check()):
        try:
          proc.kill()
        except (ProcessLookupError, OSError) as ex:
          _LOG.debug(
            "Command cancellation kill skipped. cmd=%s detail=%s",
            " ".join(proc.args) if isinstance(proc.args, (list, tuple)) else proc.args,
            ex,
          )
        proc.communicate()
        raise AppError(key="error.cancelled", params={})

      if timeout_s is not None and time.monotonic() - started_at >= float(timeout_s):
        try:
          proc.kill()
        except (ProcessLookupError, OSError) as ex:
          _LOG.debug(
            "Command timeout kill skipped. cmd=%s detail=%s",
            " ".join(proc.args) if isinstance(proc.args, (list, tuple)) else proc.args,
            ex,
          )
        proc.communicate()
        raise AppError(key="error.external_tool_timeout", params={"seconds": float(timeout_s)})

      try:
        out, err = proc.communicate(timeout=poll_interval_s)
        return str(out or ''), str(err or '')
      except subprocess.TimeoutExpired:
        continue
