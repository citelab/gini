"""The GINI version, for anything that needs to record which build produced it.

Lives here rather than in `gini/__init__.py` because `gini` is an implicit NAMESPACE package: it
is shared by `gini-core` (this distribution) and `gini-toolkit`, and a namespace package cannot
have an `__init__.py` in either without one shadowing the other.

Reported into every proof chain, so a teacher reading a submission can see which gBuilder made it.
Never a blocker: an unknown version returns "" rather than raising.
"""
from __future__ import annotations


def gini_version() -> str:
    """The installed version, preferring the full toolkit over the core it depends on."""
    try:
        from ._version import version          # written by setuptools-scm at build time
        return str(version)
    except Exception:                          # noqa: BLE001 — a raw source checkout
        pass
    from importlib.metadata import version as _v
    for dist in ("gini-toolkit", "gini-core", "gini-teaching-center"):
        try:
            return str(_v(dist))
        except Exception:                      # noqa: BLE001
            continue
    return "0.0.0+unknown"


__version__ = gini_version()
