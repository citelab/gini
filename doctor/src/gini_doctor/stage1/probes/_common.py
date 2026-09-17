"""Helpers several groups share: which engine answers, and which Python runs gBuilder.

Both answers are cached in ``ctx.shared`` so a run asks each question once, however many groups
need it.
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple

from .. import platforms


def answering_engine(ctx) -> Optional[str]:
    """The engine gBuilder would use right now: ``GINI_ENGINE`` if set, else Docker if it answers,
    else Podman if it answers. ``None`` when nothing answers."""
    if "engine" in ctx.shared:
        return ctx.shared["engine"]
    chosen = None
    forced = os.environ.get("GINI_ENGINE", "").strip()
    order = [forced] if forced in ("docker", "podman") else ["docker", "podman"]
    for name in order:
        if ctx.which(name) and ctx.run([name, "info", "--format", "{{json .}}" if name == "docker"
                                        else "json"]).ok:
            chosen = name
            break
    ctx.shared["engine"] = chosen
    return chosen


def first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0].strip() if stripped else ""


def read_text(path: str, limit: int = 64 * 1024) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return None


def json_lines(text: str) -> List[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


# --------------------------------------------------------------------------- gBuilder's Python
_QUOTED_EXEC = re.compile(r'exec[^"\n]*"([^"\n]*[/\\](?:bin|Scripts)[/\\]python[0-9.]*(?:\.exe)?)"')
_BARE_PATH = re.compile(r'(?<![\w./-])(/[\w./+-]*/bin/python[0-9.]*)')


def launcher_python(path: str, which=None) -> Optional[str]:
    """The interpreter a gbuilder launcher runs. Two shapes: ``#!/path/to/python``, or pipx's
    trampoline (``#!/bin/sh`` then ``'''exec' "<venv python>" "$0" "$@"``), whose quoted path can
    contain spaces. Binary launchers (pipx on Windows makes an .exe) name nothing readable."""
    text = read_text(path, 8192)
    if not text or "\x00" in text:
        return None
    lines = text.splitlines()
    if lines and lines[0].startswith("#!"):
        parts = lines[0][2:].strip().split()
        if parts:
            interp = parts[0]
            if os.path.basename(interp) == "env" and len(parts) > 1 and which is not None:
                interp = which(parts[1]) or parts[1]
            if os.path.basename(interp).startswith("python"):
                return interp
    m = _QUOTED_EXEC.search(text) or _BARE_PATH.search(text)
    return m.group(1) if m else None


def pipx_venv_pythons(platform: str) -> List[str]:
    home = os.path.expanduser("~")
    roots = [os.environ.get("PIPX_HOME", "")]
    if platform == platforms.WINDOWS:
        roots += [os.path.join(home, "pipx"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "pipx", "pipx")]
        tail = ("venvs", "gini-toolkit", "Scripts", "python.exe")
    else:
        roots += [os.path.join(home, ".local", "share", "pipx"), os.path.join(home, ".local", "pipx"),
                  os.path.join(home, "Library", "Application Support", "pipx")]
        tail = ("venvs", "gini-toolkit", "bin", "python")
    found = []
    for root in roots:
        if root:
            candidate = os.path.join(root, *tail)
            if os.path.isfile(candidate) and candidate not in found:
                found.append(candidate)
    return found


def gbuilder_python(ctx) -> Tuple[Optional[str], str]:
    """(interpreter, how it was found). The interpreter that runs gBuilder is usually NOT the
    first python on PATH; probing the wrong one reports "No module named PySide6" on a machine
    where gBuilder starts fine, on every machine, as a permanent false alarm."""
    if "gbuilder_python" in ctx.shared:
        return ctx.shared["gbuilder_python"]
    answer: Tuple[Optional[str], str] = (None, "not found")
    launcher = ctx.which("gbuilder")
    if launcher:
        py = launcher_python(launcher, which=ctx.which)
        if py and os.path.isfile(py):
            answer = (py, "gbuilder launcher")
    if answer[0] is None:
        venvs = pipx_venv_pythons(ctx.platform)
        if venvs:
            answer = (venvs[0], "pipx venv")
    ctx.shared["gbuilder_python"] = answer
    return answer
