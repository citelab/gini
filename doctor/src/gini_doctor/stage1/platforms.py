"""Which of the three platforms this is, in the one vocabulary every probe declares against.

Windows, Linux and macOS are equals. A probe that only makes sense on one of them says so, and
the report records ``n/a`` elsewhere, so a comparison between a Mac and a Linux box does not fill
up with "missing" facts that were never supposed to exist.
"""
from __future__ import annotations

import sys

LINUX = "linux"
MACOS = "macos"
WINDOWS = "windows"
ALL = frozenset({LINUX, MACOS, WINDOWS})
POSIX = frozenset({LINUX, MACOS})


def current(platform: str | None = None) -> str:
    """Map ``sys.platform`` onto the three families. Anything else unix-like counts as linux,
    because the probes that matter there (procfs, subuid) are the linux ones."""
    p = platform if platform is not None else sys.platform
    if p.startswith("win") or p == "cygwin":
        return WINDOWS
    if p == "darwin":
        return MACOS
    return LINUX
