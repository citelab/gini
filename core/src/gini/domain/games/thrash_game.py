"""Thrashing diagnosis game — riding on the page-replacement simulator.

Each case is a real LRU run over a generated reference string; the student reads the run's signature
(fault rate, frames vs working set, locality, working-set growth) and names WHY it is faulting:

  healthy              — low fault rate; frames cover the working set
  too few frames       — good locality, stable working set, but frames < working set (more RAM helps)
  working set too big  — the distinct-page set grows past the frames (a phase change)
  poor locality        — random access; more frames barely helps until they cover the whole set

Ground truth is the generator's label (deterministic). classify_thrash() is the practice-mode hint.
Simulator-backed, so this game is offline (a live xv6 mechanism would add real cases later).
"""
from __future__ import annotations

import random

from ..diagnose import Case, GameSpec
from ..paging_sim import run_features

THRASH_CLASSES = ["healthy", "too few frames", "working set too big", "poor locality"]
THRASH_ABBR = {"healthy": "ok", "too few frames": "frames",
               "working set too big": "ws-big", "poor locality": "local"}
THRASH_SPEC = GameSpec("thrash-diagnose", "Diagnose the thrashing",
                       "Why is this workload faulting?", THRASH_CLASSES, THRASH_ABBR)


def _loop(pages, reps) -> list:
    return [p for _ in range(reps) for p in range(pages)]


def gen_healthy(rng) -> tuple:
    return _loop(3, 12), 4                       # 3-page loop, 4 frames -> ~no faults after warmup


def gen_too_few(rng) -> tuple:
    return _loop(8, 8), 3                        # 8-page loop, 3 frames -> ~100% fault, good locality


def gen_ws_big(rng) -> tuple:
    refs = _loop(4, 4) + _loop(10, 3) + _loop(20, 3)     # growing distinct set (phase changes)
    return refs, 4


def gen_poor_local(rng) -> tuple:
    return [rng.randrange(40) for _ in range(140)], 5     # random over 40 pages -> weak locality


_GENERATORS = [("healthy", gen_healthy), ("too few frames", gen_too_few),
               ("working set too big", gen_ws_big), ("poor locality", gen_poor_local)]


def classify_thrash(f: dict, thresholds: dict | None = None) -> str:
    """Heuristic used for the hint. Low faults -> healthy; else the distinguishing signal wins:
    a growing distinct set -> working set too big; poor locality -> poor locality; otherwise the
    frames are simply too few."""
    th = {"fault": 0.25, "growth": 0.5, "local": 0.4}
    th.update(thresholds or {})
    if f["fault_rate"] < th["fault"]:
        return "healthy"
    if f["ws_growth"] >= th["growth"]:
        return "working set too big"
    if f["locality"] < th["local"]:
        return "poor locality"
    return "too few frames"


def demo_cases(seed: int = 0) -> list:
    rng = random.Random(seed)
    out = []
    for name, gen in _GENERATORS:
        refs, frames = gen(rng)
        f = run_features(refs, frames)
        out.append(Case(f"thr-{name}", f, name, subtitle=name, hint=classify_thrash(f)))
    return out
