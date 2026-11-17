"""Comparing GINI versions: a Teaching Center lab's "needs GINI X or newer".

In gini.domain because both halves need it and only this half is installed on both: the Teaching
Center compares the version a gBuilder sends against the lab's minimum, and gBuilder reads the same
rule. Pure, like the rest of the domain.
"""
from __future__ import annotations

import re

_LEADING = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def version_tuple(v: str) -> tuple[int, int, int] | None:
    """`"6.22.11"` -> `(6, 22, 11)`; `"6.22"` -> `(6, 22, 0)`; None if it does not start with a
    number.

    Only the release numbers count. A development build from a checkout one commit past 6.22.11
    calls itself `6.22.12.dev1+g…` and is read as 6.22.12: it holds everything 6.22.11 had, and
    refusing the maintainer's own checkout on a suffix would help nobody. A raw source tree with
    no version at all reports `0.0.0+unknown`, which is older than any minimum: an unknown build
    cannot be shown to have what a lab needs.
    """
    m = _LEADING.match(v or "")
    if not m:
        return None
    return tuple(int(g) if g else 0 for g in m.groups())   # type: ignore[return-value]


def at_least(v: str, minimum: str) -> bool:
    """Is version `v` the same as or newer than `minimum`? No minimum is always met; a version
    that cannot be read never meets one."""
    need = version_tuple(minimum)
    if need is None:
        return True
    have = version_tuple(v)
    return have is not None and have >= need
