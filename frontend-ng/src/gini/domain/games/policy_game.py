"""Case source for the guess-the-scheduler-policy game.

The signature is a short running-pid timeline (a Gantt window); the truth is the scheduler that
produced it. Live cases come from the real `SchedTimeline` + the kernel's actual `sched_policy`; the
demo deck synthesizes the three characteristic patterns:

  round-robin — a strict cyclic rotation through the runnable set
  priority    — one process dominates (highest priority runs, others starve)
  lottery     — no pattern (a random draw)
"""
from __future__ import annotations

import random
from collections import Counter

from ..diagnose import Case, GameSpec

POLICY_CLASSES = ["round-robin", "priority", "lottery"]
POLICY_ABBR = {"round-robin": "RR", "priority": "prio", "lottery": "lotto"}

POLICY_SPEC = GameSpec(
    id="guess-policy",
    title="Guess the scheduler",
    prompt="Which scheduler produced this timeline?",
    classes=POLICY_CLASSES,
    abbrev=POLICY_ABBR,
)


def _rr(pids, n) -> list:
    return [pids[i % len(pids)] for i in range(n)]


def _priority(pids, n, rng) -> list:
    """One pid dominates; every so often another sneaks a slice (aging), then back."""
    top = pids[0]
    out = []
    for _ in range(n):
        out.append(rng.choice(pids[1:]) if pids[1:] and rng.random() < 0.12 else top)
    return out


def _lottery(pids, n, rng) -> list:
    return [rng.choice(pids) for _ in range(n)]


def _is_periodic(pids, period) -> bool:
    if period <= 0 or len(pids) <= period:
        return False
    return all(pids[i] == pids[i - period] for i in range(period, len(pids)))


def classify_timeline(pids) -> str:
    """A rough heuristic used for the practice-mode HINT (not the grader). Dominant → priority;
    strictly periodic over the distinct set → round-robin; otherwise lottery."""
    if not pids:
        return "lottery"
    c = Counter(pids)
    if c.most_common(1)[0][1] / len(pids) > 0.55:
        return "priority"
    if _is_periodic(pids, len(set(pids))):
        return "round-robin"
    return "lottery"


def demo_cases(seed: int = 0, n: int = 24, instances: int = 2) -> list:
    """The offline deck: `instances` timelines per policy."""
    rng = random.Random(seed)
    pids = [3, 4, 5]
    out = []
    for k in range(instances):
        rr = _rr(pids, n)
        out.append(Case(f"rr{k}", rr, "round-robin", "round-robin", classify_timeline(rr)))
        pr = _priority(pids, n, rng)
        out.append(Case(f"prio{k}", pr, "priority", "priority", classify_timeline(pr)))
        lo = _lottery(pids, n, rng)
        out.append(Case(f"lot{k}", lo, "lottery", "lottery", classify_timeline(lo)))
    return out


def live_cases(timeline_pids, policy_name) -> list:
    """One case from the live Gantt window + the kernel's real policy (empty if we lack either)."""
    pids = [p for p in (timeline_pids or []) if p is not None]
    if len(pids) < 6 or not policy_name:
        return []
    return [Case("live", pids[-24:], policy_name, policy_name, classify_timeline(pids[-24:]))]
