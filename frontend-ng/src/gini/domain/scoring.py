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
    forks_done: int = 0       # how many difficulty forks the student fully completed
    forks_total: int = 0      # forks this mission offers (0 = a plain, fork-less mission)

    @property
    def summary(self) -> str:
        s = f"{self.met}/{self.total} objectives"
        if self.pending:
            s += f" ({self.pending} awaiting a run)"
        if self.forks_total:
            s += f" · {self.forks_done}/{self.forks_total} harder forks"
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


def score(results, *, complete_when: str = "all", on_time: bool = True,
          forks_done: int = 0, forks_total: int = 0) -> Score:
    """Compute the band from CORE objective results, lifted by difficulty forks.

    Fork-less mission (forks_total == 0) — unchanged, so nothing regresses:
        gold = complete AND on time · pass = complete · partial = some met · incomplete = none.

    Forked mission — the fork is the difficulty knob (GINI_AUTHORING_DESIGN.md):
        the golden (easy) path completing earns PASS; **gold now means you also took a harder fork**.
        So a student can't coast to gold on the easy path — gold is reserved for going deeper.
    """
    results = list(results)
    met = sum(1 for r in results if r.met)
    pending = sum(1 for r in results if r.status == "pending")
    complete = is_complete(results, complete_when)          # completion is CORE-only; forks are extra
    if not complete:
        band = PARTIAL if met > 0 else INCOMPLETE
    elif forks_total == 0:
        band = GOLD if on_time else PASS                    # legacy semantics preserved exactly
    else:
        band = GOLD if (forks_done >= 1 and on_time) else PASS
    return Score(band=band, met=met, total=len(results), complete=complete, pending=pending,
                 forks_done=forks_done, forks_total=forks_total)
