"""Housekeeping for the per-element shadow folders under ``~/.gini/xv6-shadows/``.

The Load loop bind-mounts one folder per xv6 element (``~/.gini/xv6-shadows/<name>/gini_sched.c``)
and, by design, never deletes it — a student's shadow persists across Stop/Run. The flip side is
they accumulate (a renamed/removed element leaves an orphan). This module provides the explicit
housekeeping: **reset** one element's folder (so the next Run re-seeds the stub) and **prune**
orphans. Nothing here runs automatically — deleting student work is always a deliberate act.

Pure (os/shutil/pathlib only); unit-tested with a ``GINI_HOME_DIR`` override.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def _home() -> Path:
    # Same rule as app.paths.gini_home / content.py, replicated to avoid an app import.
    return Path(os.environ.get("GINI_HOME_DIR") or (Path.home() / ".gini")).expanduser()


def shadows_root() -> Path:
    return _home() / "xv6-shadows"


def list_shadows() -> list[str]:
    """Element names that currently have a shadow folder."""
    root = shadows_root()
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def reset_shadow(name: str) -> bool:
    """Delete one element's shadow folder so the next Run re-seeds a fresh stub. Returns True if a
    folder was removed. (Live, the Machine Lab's Revert restores the stub *contents* without a Stop;
    this is the offline / start-over path.)"""
    d = shadows_root() / name
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def prune_shadows(keep) -> list[str]:
    """Remove shadow folders whose element name is NOT in ``keep`` (orphans from deleted/renamed
    elements). Pass the CURRENT set of xv6 element names. Returns the names removed."""
    keep = set(keep or ())
    removed = []
    for name in list_shadows():
        if name not in keep and reset_shadow(name):
            removed.append(name)
    return removed
