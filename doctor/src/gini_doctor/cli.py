"""The `gini-doctor` command: hand the packaged shell script to `sh`, unchanged.

`execvp` rather than `subprocess.run`, so the shell replaces this process entirely. That keeps
the exit status exactly as the script set it — it returns the number of failed checks, which is
what makes it usable from CI — and leaves the terminal attached, which the interactive menu needs.
"""
from __future__ import annotations

import os
import shutil
import sys

from . import SCRIPT


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not SCRIPT.exists():                       # a wheel built without its package-data
        print(f"gini-doctor: the probe script is missing from this install ({SCRIPT})",
              file=sys.stderr)
        return 2
    sh = shutil.which("sh")
    if sh is None:
        # Windows. Say what to do rather than fail with "FileNotFoundError: sh".
        print("gini-doctor: needs a POSIX shell. On Windows, run it inside WSL or Git Bash:\n"
              f"    sh {SCRIPT}", file=sys.stderr)
        return 2
    os.execv(sh, [sh, str(SCRIPT), *args])        # replaces this process; never returns
    return 0                                      # unreachable, kept for type-checkers
