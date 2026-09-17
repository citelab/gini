"""The `gini-doctor` command.

Runs the Python doctor (Stage 1) in this process. A pipx or pip install already has a working
Python, so there is nothing for Stage 0 to find here.

The legacy shell engine is still shipped while the Python doctor is checked against it on the lab's
Linux machines. `gini-doctor legacy …` runs it explicitly, and its multi-machine options
(`--fanout`, `--compare`, `--report`, `--menu`) are routed to it automatically, so instructions
already written for it keep working.
"""
from __future__ import annotations

import os
import shutil
import sys

from . import SCRIPT

# Options only the legacy engine understands. Stage 1's equivalents are subcommands.
LEGACY_OPTIONS = ("--fanout", "--compare", "--compare-all", "--report", "--menu", "--no-run", "--all")


def legacy(args) -> int:
    """Hand the packaged legacy script to `sh`, unchanged. `execv`, so the exit status and the
    terminal belong to the script."""
    if not SCRIPT.exists():
        print("gini-doctor: the legacy script is missing from this install (%s)" % SCRIPT, file=sys.stderr)
        return 2
    sh = shutil.which("sh")
    if sh is None:
        print("gini-doctor: the legacy engine needs a POSIX shell. On Windows, run it inside WSL or\n"
              "Git Bash, or use the Python doctor: gini-doctor run", file=sys.stderr)
        return 2
    os.execv(sh, [sh, str(SCRIPT)] + list(args))
    return 0  # unreachable


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "legacy":
        return legacy(args[1:])
    if args and args[0] in LEGACY_OPTIONS:
        print("gini-doctor: %s belongs to the legacy engine; running `gini-doctor legacy %s`"
              % (args[0], " ".join(args)), file=sys.stderr)
        return legacy(args)
    if args and args[0] in ("--list", "--groups"):
        args = ["groups"]
    from .stage1.cli import main as stage1_main
    return stage1_main(args)
