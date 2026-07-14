"""Scoring — objective results → completion + a broad band.

Completion is deterministic (GINI's runtime supplies the objective facts; the AI never scores).
We check *completion*, not quality: how many objectives are met, whether `complete_when` is
satisfied, and whether it was done on time / within attempts. Bands are intentionally coarse.

`complete_when`: `all` | `any` | `at_least(n)`. A `pending` (behavioral, un-run) objective counts
as not-yet-met.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .objectives import ObjectiveResult

GOLD, PASS, PARTIAL, INCOMPLETE = "gold", "pass", "partial", "incomplete"

_AT_LEAST = re.compile(r"at_least\(\s*(\d+)\s*\)")


@dataclass
class Score:
    band: str
    met: int
    total: int
    complete: bool
    pending: int              # behavioral objectives not yet run

    @property
    def summary(self) -> str:
        s = f"{self.met}/{self.total} objectives"
        if self.pending:
            s += f" ({self.pending} awaiting a run)"
        return s


def is_complete(results, complete_when: str = "all") -> bool:
    met = sum(1 for r in results if r.met)
    total = len(results)
    cw = (complete_when or "all").strip()
    if cw == "any":
        return met >= 1
    m = _AT_LEAST.fullmatch(cw)
    if m:
        return met >= int(m.group(1))
    return met == total and total > 0          # 'all' (default)


def score(results, *, complete_when: str = "all", on_time: bool = True) -> Score:
    """Compute the band from objective results.

    gold = complete AND on time · pass = complete · partial = some met · incomplete = none."""
    results = list(results)
    met = sum(1 for r in results if r.met)
    pending = sum(1 for r in results if r.status == "pending")
    complete = is_complete(results, complete_when)
    if complete and on_time:
        band = GOLD
    elif complete:
        band = PASS
    elif met > 0:
        band = PARTIAL
    else:
        band = INCOMPLETE
    return Score(band=band, met=met, total=len(results), complete=complete, pending=pending)
