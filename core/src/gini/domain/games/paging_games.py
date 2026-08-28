"""Three more paging games on the page-replacement simulator — all with EXACT ground truth computed
by the simulator (no guessing on our side):

  fault-count  (estimate)        — refs + frames + policy → how many faults?
  belady       (predict-outcome) — will adding ONE frame reduce FIFO's faults? (sometimes NO — the
                                    anomaly)
  showdown     (classify)        — on this string, does FIFO or LRU fault fewer (or tie)?
"""
from __future__ import annotations

from ..diagnose import Case, GameSpec
from ..paging_sim import resident_state, simulate

# a small bank of reference strings (kept short enough to reason about by hand)
_STRINGS = [
    ([1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5], 3),          # the classic Belady string
    ([7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2], 3),
    ([0, 1, 2, 0, 1, 2, 0, 1, 2, 0], 2),
    ([1, 2, 3, 1, 4, 1, 5, 6, 2, 1], 3),
    ([4, 3, 2, 1, 4, 3, 5, 4, 3, 2, 1, 5], 4),
    ([1, 2, 1, 3, 1, 4, 1, 5, 1, 6], 3),
]

# ---- fault-count (estimate) ----------------------------------------------- #
FAULTCOUNT_SPEC = GameSpec("faults-estimate", "Count the page faults",
                           "How many page faults will this run take?", classes=[],
                           answer="estimate", tolerance=2, unit="faults")


def faultcount_cases() -> list:
    out = []
    for i, (refs, frames) in enumerate(_STRINGS):
        faults = simulate(refs, frames, "lru").faults
        out.append(Case(f"fc-{i}", {"refs": refs, "frames": frames, "policy": "LRU"},
                        faults, subtitle=f"{faults} faults"))
    return out


# ---- Belady spotter (predict-outcome) ------------------------------------- #
BELADY_CLASSES = ["fewer faults", "same or more"]
BELADY_SPEC = GameSpec("belady-spot", "Belady spotter",
                       "Will adding ONE more frame reduce FIFO's faults?",
                       BELADY_CLASSES, {"fewer faults": "fewer", "same or more": "same+"})


def belady_cases() -> list:
    out = []
    for i, (refs, frames) in enumerate(_STRINGS):
        f0 = simulate(refs, frames, "fifo").faults
        f1 = simulate(refs, frames + 1, "fifo").faults
        truth = "fewer faults" if f1 < f0 else "same or more"
        out.append(Case(f"bel-{i}",
                        {"refs": refs, "frames": frames, "policy": "FIFO",
                         "note": f"currently {f0} faults with {frames} frames — add one?"},
                        truth, subtitle=f"{frames}f: {f0}  →  {frames + 1}f: {f1}"))
    return out


# ---- policy showdown (classify) ------------------------------------------- #
SHOWDOWN_CLASSES = ["FIFO", "LRU", "tie"]
SHOWDOWN_SPEC = GameSpec("policy-showdown", "Policy showdown",
                         "Which faults fewer on this string — FIFO or LRU?",
                         SHOWDOWN_CLASSES, {"FIFO": "FIFO", "LRU": "LRU", "tie": "tie"})


def showdown_cases() -> list:
    out = []
    for i, (refs, frames) in enumerate(_STRINGS):
        fifo = simulate(refs, frames, "fifo").faults
        lru = simulate(refs, frames, "lru").faults
        truth = "FIFO" if fifo < lru else "LRU" if lru < fifo else "tie"
        out.append(Case(f"sd-{i}", {"refs": refs, "frames": frames},
                        truth, subtitle=f"FIFO {fifo}  ·  LRU {lru}"))
    return out


# ---- next eviction (spot the culprit) ------------------------------------- #
NEXTEVICT_SPEC = GameSpec("next-evict", "Spot the next eviction",
                          "Which resident page is evicted on the next fault?",
                          classes=[], answer="spot")


def nextevict_cases() -> list:
    out = []
    for i, (refs, frames) in enumerate(_STRINGS):
        for policy in ("fifo", "lru"):
            resident, order = resident_state(refs, frames, policy)
            if len(resident) < frames or not order:
                continue
            miss = [p for p in sorted(set(refs)) if p not in resident]
            if not miss:
                continue
            victim = order[0]
            out.append(Case(
                f"ne-{i}-{policy}",
                {"refs": refs, "frames": frames, "policy": policy.upper(),
                 "note": f"next access: page {miss[0]} (faults) — who is evicted?"},
                victim, subtitle=f"evict page {victim}", options=sorted(resident)))
    return out


# ---- policy rank (order them) --------------------------------------------- #
POLICYRANK_SPEC = GameSpec("policy-rank", "Rank the policies",
                           "Order FIFO, LRU, OPT by faults — fewest first.",
                           classes=[], answer="rank")


def policyrank_cases() -> list:
    out = []
    for i, (refs, frames) in enumerate(_STRINGS):
        counts = {pol: simulate(refs, frames, pol).faults for pol in ("fifo", "lru", "opt")}
        if len(set(counts.values())) < 3:              # only strictly-ordered strings (no ties)
            continue
        order = sorted(counts, key=lambda p: counts[p])
        out.append(Case(f"pr-{i}", {"refs": refs, "frames": frames},
                        [p.upper() for p in order],
                        subtitle="  <  ".join(f"{p.upper()} {counts[p]}" for p in order),
                        options=["FIFO", "LRU", "OPT"]))
    return out
