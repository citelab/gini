from __future__ import annotations

try:
    from ._version import version as __version__     # written by setuptools-scm at build time
except Exception:                                    # noqa: BLE001 — raw source checkout
    try:
        from importlib.metadata import version as _v
        __version__ = _v("gini-teaching-center")
    except Exception:                                # noqa: BLE001
        __version__ = "0.0.0+unknown"
