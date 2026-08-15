"""GINI gBuilder — next-generation topology builder for networks + cloud."""

# Version is derived from git tags at build time by setuptools-scm, which writes _version.py into
# the package. Running from a source checkout without a build falls back to a dev marker.
try:
    from ._version import version as __version__
except Exception:  # noqa: BLE001 — no built _version.py (raw source tree)
    try:
        from importlib.metadata import version as _v
        __version__ = _v("gini-toolkit")
    except Exception:  # noqa: BLE001
        __version__ = "0.0.0+unknown"
