"""The router module directory, as data.

``~/.gini/scripts`` is bind-mounted into every gRouter at ``/scripts``, so the modules in it
are shared by the whole topology: one program, many routers. This module turns that directory
into a plain list the GINI Source pane can render, and reads a module's text for display.

Pure and filesystem-only (no Qt, no Docker), so it is unit-tested directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScriptEntry:
    """One loadable router module."""
    name: str          # "mcast_tree.lua"
    path: Path         # absolute path on the host
    size_kb: int       # rounded up, so a 200-byte module still reads as 1 kB

    @property
    def label(self) -> str:
        return f"{self.name}   ({self.size_kb} kB)"

    @property
    def load_path(self) -> str:
        """Where the routers see it, which is what the student types."""
        return f"/scripts/{self.name}"


def list_modules(directory) -> list[ScriptEntry]:
    """Every ``.lua`` module in `directory`, sorted by name. Missing or unreadable
    directory yields an empty list, because a browser must never raise."""
    d = Path(directory)
    try:
        files = sorted((p for p in d.glob("*.lua") if p.is_file()), key=lambda p: p.name)
    except OSError:
        return []
    out: list[ScriptEntry] = []
    for p in files:
        try:
            size = max(1, -(-p.stat().st_size // 1024))     # ceil, min 1
        except OSError:
            size = 1
        out.append(ScriptEntry(name=p.name, path=p, size_kb=size))
    return out


def read_module(path) -> tuple[str, str]:
    """(text, error). Never raises; a decoding problem yields replacement characters
    rather than an exception, because showing an imperfect file beats showing nothing."""
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8", errors="replace"), ""
    except OSError as e:
        return "", f"could not read {p.name}: {e}"


def line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)
