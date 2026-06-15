"""Open a platform terminal running a command — the 'double-click to log in' plumbing.

Mirrors old gBuilder's xterm-on-double-click, but cross-platform: a real terminal
window the student types into (docker exec into a machine, or a console into a
network element).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def open_terminal(title: str, cwd: str | Path, command: str) -> tuple[bool, str]:
    cwd = str(cwd)
    if sys.platform == "darwin":
        script = f'cd {_q(cwd)} && clear && echo {_q("== " + title + " ==")} && {command}'
        osa = f'tell application "Terminal" to do script "{script.replace(chr(34), chr(92) + chr(34))}"'
        try:
            subprocess.Popen(["osascript", "-e", osa,
                              "-e", 'tell application "Terminal" to activate'])
            return True, "opened Terminal"
        except FileNotFoundError:
            return False, "osascript not found"
    # Linux: try the common terminal emulators
    for term, args in (
        ("x-terminal-emulator", ["-e"]),
        ("gnome-terminal", ["--"]),
        ("konsole", ["-e"]),
        ("xterm", ["-e"]),
    ):
        if shutil.which(term):
            full = f"cd {_q(cwd)}; {command}; exec ${{SHELL:-sh}}"
            try:
                subprocess.Popen([term, *args, "bash", "-lc", full])
                return True, f"opened {term}"
            except OSError:
                continue
    return False, ("no terminal emulator found — run manually:\n"
                   f"  cd {cwd} && {command}")


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
