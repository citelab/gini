"""Memory watchdog — fleet memory tracking + runaway detection for the Dashboard.

Motivated by a real failure: a wedged router control socket leaked a ~15 MB client
process per poll until the Docker VM's OOM killer swept most of the lab (exit 137,
seven containers at once, no warning). The kill was silent; the LEAK had a textbook
linear slope for twenty minutes. Trend detection turns that into an early warning.

Pure domain: `ingest()` per-service memory samples (from `docker stats`), keep a short
ring per service, fit a least-squares slope, and flag sustained growth. A runaway is a
WARNING for the human, never an auto-kill — in a teaching lab a student's iperf spike
can look like a leak.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemSeries:
    svc: str
    t: list = field(default_factory=list)        # sample times (s)
    mib: list = field(default_factory=list)      # memory at each sample (MiB)

    def add(self, tnow: float, mem_mib: float, window_s: float) -> None:
        self.t.append(tnow)
        self.mib.append(float(mem_mib))
        cutoff = tnow - window_s
        while len(self.t) > 1 and self.t[0] < cutoff:
            self.t.pop(0)
            self.mib.pop(0)

    def slope_mib_per_min(self) -> float | None:
        """Least-squares slope over the retained window, in MiB/minute.
        None if the window is too thin to mean anything."""
        n = len(self.t)
        if n < 4:
            return None
        tm = sum(self.t) / n
        mm = sum(self.mib) / n
        den = sum((tt - tm) ** 2 for tt in self.t)
        if den <= 0:
            return None
        num = sum((tt - tm) * (v - mm) for tt, v in zip(self.t, self.mib))
        return (num / den) * 60.0

    @property
    def span_s(self) -> float:
        return (self.t[-1] - self.t[0]) if len(self.t) > 1 else 0.0

    @property
    def growth_mib(self) -> float:
        return (self.mib[-1] - self.mib[0]) if len(self.mib) > 1 else 0.0


@dataclass
class Runaway:
    svc: str
    slope_mib_per_min: float
    growth_mib: float
    span_s: float


class MemWatch:
    """Ingests successive `docker stats` snapshots; reports fleet total + runaways."""

    WINDOW_S = 300.0          # judge trends over the last 5 minutes
    MIN_SPAN_S = 120.0        # need >= 2 minutes of history before accusing anyone
    SLOPE_MIB_MIN = 8.0       # sustained growth faster than this is suspicious
    MIN_GROWTH_MIB = 40.0     # ...and it must have actually gained this much

    def __init__(self) -> None:
        self.series: dict[str, MemSeries] = {}

    def ingest(self, stats: dict, tnow: float) -> None:
        """stats: {svc: {"mem_used": MiB, ...}} — one `docker stats` snapshot."""
        for svc, m in (stats or {}).items():
            mem = m.get("mem_used")
            if mem is None:
                continue
            s = self.series.get(svc)
            if s is None:
                s = self.series[svc] = MemSeries(svc)
            s.add(tnow, mem, self.WINDOW_S)
        # forget services that vanished (container stopped/removed)
        gone = [svc for svc in self.series if svc not in (stats or {})]
        for svc in gone:
            del self.series[svc]

    def total_mib(self) -> float:
        return sum(s.mib[-1] for s in self.series.values() if s.mib)

    def runaways(self) -> list[Runaway]:
        out = []
        for s in self.series.values():
            if s.span_s < self.MIN_SPAN_S:
                continue
            sl = s.slope_mib_per_min()
            if sl is None:
                continue
            if sl >= self.SLOPE_MIB_MIN and s.growth_mib >= self.MIN_GROWTH_MIB:
                out.append(Runaway(s.svc, sl, s.growth_mib, s.span_s))
        return sorted(out, key=lambda r: -r.slope_mib_per_min)

    def clear(self) -> None:
        self.series.clear()


def estimate_need_mib(n_services: int) -> float:
    """Rough VM-memory need for a lab: ~280 MiB per container (lean machines are far
    less, k8s/OS-zoo far more — this is a middle number) plus daemon/system headroom."""
    return 280.0 * max(n_services, 1) + 1536.0
