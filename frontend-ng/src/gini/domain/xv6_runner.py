"""Xv6Runner — the OS domain's behavioral-probe runner (the GINI-as-oracle seam for the kernel).

The networking oracle answers `reach(A -> B) == ok` against a live topology. The OS oracle answers
`measure(<what>, <which>) <op> <value>` against the running kernel's telemetry — CPU share, wait
times, ticket fairness, shadow liveness. Crucially this reuses the EXISTING probe grammar and
evaluator in `probes.py` unchanged: we only add a runner that knows how to compute OS metrics, the
same way `services/probe_runner.py` computes networking ones. GINI, never the model, gives the
verdict.

Pure: it reads a `Snapshot` (procs + sched fields), a `SchedTimeline` (who ran, CPU share), and the
shadow manifest ({name: ShadowStatus}); a `FakeRunner`/hand-built telemetry makes every predicate
unit-testable offline.

OS metrics understood by `measure(what, which)`:
  cpu_share, highest_priority          -> the highest-priority proc's fraction of recent CPU
  cpu_share, <pid>                     -> that pid's fraction of recent CPU
  max_wait_slices, any                 -> longest any RUNNABLE proc has waited (aging counter)
  share_ratio, tickets                 -> max deviation of observed share from the ticket ratio
  every_runnable_runs, any             -> 1.0 if every runnable proc ran in the window, else 0.0
  shadow_active, <name>                -> 1.0 if that shadow is active, else 0.0
  shadow_faults, <name>                -> how many times that shadow crashed back to the primary
"""
from __future__ import annotations


class Xv6Runner:
    """A `probes.Runner` for the OS domain. Only `available()` + `measure()` are meaningful here
    (OS win-conditions are all `measure(...)`); the networking methods are harmless no-ops so the
    same evaluator can call either runner."""

    def __init__(self, snapshot=None, timeline=None, shadows=None, window: int = 60) -> None:
        self.snapshot = snapshot                    # domain.xv6.Snapshot (procs + sched fields)
        self.timeline = timeline                    # domain.xv6.SchedTimeline (who ran / shares)
        self.shadows = shadows or {}                # {name: ShadowStatus}
        self.window = window

    # -- probes.Runner protocol --------------------------------------------- #
    def available(self) -> bool:
        return self.snapshot is not None

    def reach(self, src, dst, port=None) -> bool:   # not used by OS probes
        return False

    def http(self, src, dst, port) -> bool:
        return False

    def backends(self, lb) -> int:
        return 0

    def flow(self, ovs, match) -> bool:
        return False

    def measure(self, what: str, which: str):
        """Return the OS metric value, or None when it can't be read (probe treats None as
        not-satisfied, never a crash)."""
        if self.snapshot is None:
            return None
        procs = list(getattr(self.snapshot, "procs", []) or [])
        handler = getattr(self, f"_m_{what}", None)
        return handler(which, procs) if handler else None

    # -- individual metrics -------------------------------------------------- #
    def _shares(self) -> dict:
        return self.timeline.shares(self.window) if self.timeline is not None else {}

    def _highest_priority_pid(self, procs):
        """The proc with the numerically-lowest priority = highest scheduling priority."""
        cand = [p for p in procs if p.priority is not None]
        if not cand:
            return None
        return min(cand, key=lambda p: (p.priority, p.pid)).pid

    def _m_cpu_share(self, which, procs):
        shares = self._shares()
        if which == "highest_priority":
            pid = self._highest_priority_pid(procs)
            return None if pid is None else float(shares.get(pid, 0.0))
        try:
            return float(shares.get(int(which), 0.0))
        except (TypeError, ValueError):
            return None

    def _m_max_wait_slices(self, which, procs):
        waits = [p.wait_ticks for p in procs
                 if p.state == "runnable" and p.wait_ticks is not None]
        return float(max(waits)) if waits else None

    def _m_share_ratio(self, which, procs):
        """Max absolute deviation of each ticketed proc's observed CPU share from its ticket share
        (0 = share tracks tickets exactly; the lottery win-condition asserts this is small)."""
        ticketed = [p for p in procs if p.tickets]
        total = sum(p.tickets for p in ticketed)
        if not ticketed or total <= 0:
            return None
        shares = self._shares()
        return float(max(abs(shares.get(p.pid, 0.0) - p.tickets / total) for p in ticketed))

    def _m_every_runnable_runs(self, which, procs):
        runnable = {p.pid for p in procs if p.state in ("runnable", "running")}
        if not runnable:
            return 1.0
        if self.timeline is None:
            return None
        ran = {s.pid for s in self.timeline.recent(self.window)}
        return 1.0 if runnable <= ran else 0.0

    def _m_shadow_active(self, which, procs):
        s = self.shadows.get(which)
        return 1.0 if (s is not None and s.active) else 0.0

    def _m_shadow_faults(self, which, procs):
        s = self.shadows.get(which)
        return None if s is None else float(s.faults)
