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
  shadow_faults, <name>                -> times that shadow WEDGED the machine (agent-counted)
  shadow_rejects, <name>               -> answers the kernel's validator threw out (want 0)
  shadow_calls, <name>                 -> times the student's function was asked at all
  faults_handled, any                  -> page faults the student's vm shadow serviced (S2)
  faults_fellthrough, any              -> faults it declined / had rejected (shipped code ran)
  pages_resident, any                  -> leaf mappings present (the heap growing, fault by fault)
  cache_hit_rate, any                  -> buffer-cache hits / (hits+misses)  (S1)
  evictions, any                       -> buffers recycled since boot
  cache_misses, any                    -> buffer-cache misses since boot
  mean_gap, any                        -> mean gap between disk-block allocations   (S4)
  max_free_run, any                    -> largest contiguous free physical run      (S3)
  free_pages, any                      -> free physical pages
  lock_contention, <name>|any          -> spins per acquire (any = the WORST lock)
  lock_acquires, <name>                -> times that lock was taken
  lock_spins, <name>                   -> failed test-and-set attempts on it
"""
from __future__ import annotations


class Xv6Runner:
    """A `probes.Runner` for the OS domain. Only `available()` + `measure()` are meaningful here
    (OS win-conditions are all `measure(...)`); the networking methods are harmless no-ops so the
    same evaluator can call either runner."""

    def __init__(self, snapshot=None, timeline=None, shadows=None, window: int = 60,
                 vm=None, fs=None, locks=None) -> None:
        self.snapshot = snapshot                    # domain.xv6.Snapshot (procs + sched fields)
        self.timeline = timeline                    # domain.xv6.SchedTimeline (who ran / shares)
        self.shadows = shadows or {}                # {name: ShadowStatus}
        self.window = window
        self.vm = vm                                # domain.xv6_vm.VmSnapshot (page tables/faults)
        self.fs = fs                                # domain.xv6_fs.FsSnapshot (buffer cache/log)
        self.locks = list(locks or [])              # [LockStat] — contention telemetry

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

    def _m_shadow_rejects(self, which, procs):
        """Answers the kernel's validator threw out. A mission asserts `== 0`: the student's code
        may be a poor policy (that is what the behavioural measures are for) but it must never
        hand the kernel an illegal answer."""
        s = self.shadows.get(which)
        return None if s is None else float(s.rejects)

    # -- vm shadow (S2) ------------------------------------------------------- #
    def _m_faults_handled(self, which, procs):
        """Page faults the student's vm shadow actually serviced. `> 0` proves their handler runs;
        pair it with `shadow_rejects == 0` to prove it runs CORRECTLY."""
        return None if self.vm is None else float(getattr(self.vm, "vmf_handled", 0))

    def _m_faults_fellthrough(self, which, procs):
        """Faults their handler declined (returned 0) or had rejected — the shipped allocator took
        those. A lazy-allocation mission wants this at 0 once the student's handler is complete."""
        return None if self.vm is None else float(getattr(self.vm, "vmf_fell", 0))

    def _m_pages_resident(self, which, procs):
        """Leaf mappings currently present — the heap growing one faulted page at a time."""
        if self.vm is None:
            return None
        return float(sum(1 for p in (self.vm.leaves or []) if getattr(p, "valid", True)))

    # -- fs shadow (S1): buffer-cache replacement ----------------------------- #
    def _m_cache_hit_rate(self, which, procs):
        """hits / (hits + misses) — the number a replacement-policy mission is graded on.
        Cumulative since boot, so reboot (or the counter reset) before a measured run."""
        if self.fs is None:
            return None
        tot = self.fs.hits + self.fs.misses
        return None if tot == 0 else float(self.fs.hits) / tot

    def _m_evictions(self, which, procs):
        return None if self.fs is None else float(getattr(self.fs, "evicts", 0))

    def _m_cache_misses(self, which, procs):
        return None if self.fs is None else float(getattr(self.fs, "misses", 0))

    def _m_mean_gap(self, which, procs):
        """Mean distance between successive disk-block allocations (S4). Lower = better locality
        = fewer seeks; the shipped first-fit allocator scatters once the disk has churned."""
        return None if self.fs is None else float(getattr(self.fs, "mean_gap", 0))

    def _m_max_free_run(self, which, procs):
        """Largest CONTIGUOUS run of free physical pages (S3) — the fragmentation score. A
        free-list allocator degrades here; a buddy/locality policy holds it up."""
        return None if self.vm is None else float(getattr(self.vm, "max_free_run", 0))

    def _m_free_pages(self, which, procs):
        return None if self.vm is None else float(getattr(self.vm, "free_pages", 0))

    # -- lock contention ------------------------------------------------------ #
    def _lock(self, name):
        return next((l for l in self.locks if l.name == name), None)

    def _m_lock_contention(self, which, procs):
        """Spins per acquire for one named lock — the number the lock lab is about. Note this is
        0 on a single-core kernel by construction: there is no second CPU to spin against."""
        if which in ("any", "worst", ""):
            return max((l.contention for l in self.locks), default=None) if self.locks else None
        l = self._lock(which)
        return None if l is None else float(l.contention)

    def _m_lock_acquires(self, which, procs):
        l = self._lock(which)
        return None if l is None else float(l.acquires)

    def _m_lock_spins(self, which, procs):
        l = self._lock(which)
        return None if l is None else float(l.spins)

    def _m_shadow_calls(self, which, procs):
        """Times the student's function was actually asked — separates 'my code is wrong' from
        'my code never runs'."""
        s = self.shadows.get(which)
        return None if s is None else float(s.calls)
