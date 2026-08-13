"""Page-replacement simulator — the real algorithms (FIFO / LRU / Optimal) run over a reference
string with a bounded frame budget. Deterministic and exact, so it is ground truth for the thrashing
game and a substrate for other paging games (policy comparison, Belady's anomaly, next-eviction).

This is a faithful implementation of the algorithms, not a hand-waved model — but it is NOT xv6-live
(xv6 has no eviction). A live page-replacement mechanism in the kernel is a separate, larger build.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReplayResult:
    policy: str
    frames: int
    faults: int
    evictions: int
    fault_at: list          # bool per reference — was it a page fault?


def simulate(refs, frames: int, policy: str = "lru") -> ReplayResult:
    """Run `policy` over `refs` with `frames` physical frames. FIFO / LRU / OPT."""
    refs = list(refs)
    policy = policy.lower()
    if frames <= 0:
        return ReplayResult(policy, frames, len(refs), 0, [True] * len(refs))
    if policy == "opt":
        return _opt(refs, frames)
    inset: set = set()
    order: list = []        # FIFO: insertion order; LRU: recency (least-recent at front)
    faults = evictions = 0
    fault_at: list = []
    for pg in refs:
        hit = pg in inset
        fault_at.append(not hit)
        if hit:
            if policy == "lru":
                order.remove(pg); order.append(pg)      # touch -> most recent
            continue
        faults += 1
        if len(inset) >= frames:
            victim = order.pop(0)                        # FIFO oldest / LRU least-recent
            inset.discard(victim); evictions += 1
        inset.add(pg); order.append(pg)
    return ReplayResult(policy, frames, faults, evictions, fault_at)


def _opt(refs, frames) -> ReplayResult:
    inset: set = set()
    faults = evictions = 0
    fault_at: list = []
    for i, pg in enumerate(refs):
        hit = pg in inset
        fault_at.append(not hit)
        if hit:
            continue
        faults += 1
        if len(inset) >= frames:
            def next_use(p):
                for j in range(i + 1, len(refs)):
                    if refs[j] == p:
                        return j
                return 1 << 30                            # never used again -> evict first
            victim = max(inset, key=next_use)
            inset.discard(victim); evictions += 1
        inset.add(pg)
    return ReplayResult("opt", frames, faults, evictions, fault_at)


def resident_state(refs, frames: int, policy: str = "lru") -> tuple:
    """Replay `refs` and return (resident_set, order) at the end. `order` is eviction order: FIFO
    insertion order / LRU least-recent-first — so order[0] is the next victim when full."""
    policy = policy.lower()
    inset: set = set()
    order: list = []
    for pg in refs:
        if pg in inset:
            if policy == "lru":
                order.remove(pg); order.append(pg)
            continue
        if len(inset) >= frames and order:
            victim = order.pop(0)
            inset.discard(victim)
        inset.add(pg); order.append(pg)
    return inset, order


# -- metrics (the observable signature) -------------------------------------------------------- #
def fault_rate(res: ReplayResult) -> float:
    return res.faults / len(res.fault_at) if res.fault_at else 0.0


def unique_pages(refs) -> int:
    return len(set(refs))


def peak_working_set(refs, window: int = 10) -> int:
    """Largest number of distinct pages touched within any `window`-reference sliding window."""
    from collections import deque
    w: deque = deque()
    seen: dict = {}
    peak = 0
    for p in refs:
        w.append(p); seen[p] = seen.get(p, 0) + 1
        if len(w) > window:
            old = w.popleft(); seen[old] -= 1
            if seen[old] == 0:
                del seen[old]
        peak = max(peak, len(seen))
    return peak


def ws_growth(refs) -> float:
    """How much the distinct-page set grows from the first half to the second (phase change)."""
    if len(refs) < 4:
        return 0.0
    h = len(refs) // 2
    a = len(set(refs[:h])); b = len(set(refs[h:]))
    return max(0.0, (b - a) / max(a, 1))


def locality(refs, window: int = 8) -> float:
    """Fraction of reuses whose previous touch was within `window` references (1 = tight locality)."""
    last: dict = {}
    hits = total = 0
    for i, p in enumerate(refs):
        if p in last:
            total += 1
            if i - last[p] <= window:
                hits += 1
        last[p] = i
    return hits / total if total else 0.0


def run_features(refs, frames: int, policy: str = "lru") -> dict:
    """The observable signature of a run — what the thrashing game shows the student."""
    res = simulate(refs, frames, policy)
    return {"fault_rate": fault_rate(res), "frames": frames,
            "working_set": peak_working_set(refs), "unique_pages": unique_pages(refs),
            "ws_growth": ws_growth(refs), "locality": locality(refs)}
