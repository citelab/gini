"""Run a command without letting it take the doctor down with it.

Commands are **argv lists**, never shell strings: nothing a probe runs goes through a shell, so
there is no quoting to get wrong and no way for a value on the machine to become a command.

``run`` never raises. What happened is always in the result, including the command's own error
text, because "FAILED" without the error is the least useful line a diagnostic can print.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Mapping, NamedTuple, Optional, Sequence

OK = "ok"            # ran, exit status 0
FAILED = "failed"    # ran, non-zero exit status
ABSENT = "absent"    # the program is not on this machine
TIMEOUT = "timeout"  # ran too long and was stopped
ERROR = "error"      # could not be started for another reason (permissions, bad binary, …)


class Result(NamedTuple):
    status: str
    returncode: Optional[int]
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.status == OK

    def text(self) -> str:
        """stdout with surrounding whitespace removed — what most probes want."""
        return self.stdout.strip()

    def evidence(self) -> str:
        """The most useful single line of error text, for a fact's ``detail``."""
        for stream in (self.stderr, self.stdout):
            for line in stream.strip().splitlines():
                if line.strip():
                    return line.strip()[:300]
        if self.returncode is not None:
            return "exit status %d" % self.returncode
        return self.status


def which(program: str) -> Optional[str]:
    return shutil.which(program)


def run(argv: Sequence[str], timeout: float = 20.0, env: Optional[Mapping[str, str]] = None,
        stdin_text: Optional[str] = None) -> Result:
    if not argv:
        return Result(ERROR, None, "", "empty command")
    exe = argv[0]
    if os.path.dirname(exe) == "" and shutil.which(exe) is None:
        return Result(ABSENT, None, "", "%s: not found on PATH" % exe)
    full_env = None
    if env is not None:
        full_env = dict(os.environ)
        full_env.update(env)
    try:
        proc = subprocess.run(
            list(argv),
            input=stdin_text,
            # A probe never inherits the console's input: a command waiting for a keypress would
            # hang the doctor on exactly the machine someone is trying to diagnose.
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=full_env,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        return Result(ABSENT, None, "", str(e))
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        return Result(TIMEOUT, None, out or "", "timed out after %ss" % timeout)
    except (OSError, ValueError) as e:
        return Result(ERROR, None, "", "%s: %s" % (type(e).__name__, e))
    status = OK if proc.returncode == 0 else FAILED
    return Result(status, proc.returncode, proc.stdout or "", proc.stderr or "")
