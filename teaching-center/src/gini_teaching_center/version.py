from __future__ import annotations

import re
from pathlib import Path

try:
    from ._version import version as __version__     # written by setuptools-scm at build time
except Exception:                                    # noqa: BLE001 — raw source checkout
    try:
        from importlib.metadata import version as _v
        __version__ = _v("gini-teaching-center")
    except Exception:                                # noqa: BLE001
        __version__ = "0.0.0+unknown"


def on_disk() -> str:
    """The version of the package as it sits on disk RIGHT NOW — not the one running.

    The two diverge for exactly one reason, and it is the reason this function exists: `pip install
    --upgrade` replaces the files under this directory while the service keeps running, so from
    that moment the process is serving a newer `console.html` (read off disk on every request) out
    of an older Python (imported once, held in `sys.modules`). The UI then asks for endpoints the
    running code has never heard of.

    Read as a FILE rather than imported, because importing it would either return the module
    already cached in `sys.modules` — which is the running version, the very thing we are trying to
    tell it apart from — or permanently cache the new one and break the check for the rest of the
    process's life.

    "" when it cannot be told (a source checkout has no `_version.py`), and the caller must treat
    that as "no opinion" rather than as a mismatch. Guessing here is what produced the false alarm
    this replaced.
    """
    try:
        text = Path(__file__).with_name("_version.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"""(?m)^\s*(?:__version__\s*=\s*)?version\s*[:=]\s*['"]([^'"]+)['"]""", text)
    return m.group(1) if m else ""
