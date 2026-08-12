"""MachineState — the shared bridge between a running xv6 Machine and the rest of GINI.

Decision (signed off): the *state model* is the bridge, not the Machine Lab dialog. One
`MachineState` owns the provider, the latest `Snapshot`, the scheduling timeline, and a
`StateWatcher`; the Lab renders from it and the Ask GINI agent reads from it, so help works
whether or not the dialog is open and both sides see one atomic snapshot.

Everything here is pure (no Qt): the provider is injected (the offline `DemoScheduler` today,
a GDB bridge on the Mac later), so the serializer, the deltas, and the event watcher are all
unit-tested without QEMU.

  • `state_card(...)`   — a compact, progressive (L0/L1/L2) text block the agent is fed.
  • `StateWatcher`      — diffs successive snapshots into pedagogical events for Coach.
  • `MachineState`      — owns provider + timeline + watcher; refresh/step/controls + card().
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .xv6 import DemoScheduler, SchedTimeline, Snapshot
from .xv6_fs import DemoDisk, fs_summary
from .xv6_vm import DemoVm, memory_summary


# --------------------------------------------------------------------------- #
# Pedagogical events (Coach consumes these)
# --------------------------------------------------------------------------- #
@dataclass
class OsEvent:
    kind: str                 # starvation | cpu_monopoly | zombie_leak | idle | control
    detail: str               # human one-liner
    pid: int | None = None


@dataclass
class CoachLedger:
    """'Measured help' bookkeeping: a per-machine hint BUDGET plus an instructor-visible LOG.
    The moat is that GINI's help is grounded in live kernel state a foreign agent can't see;
    the measure is that the help is finite and recorded, so offloading shows up as a signal."""
    budget: int = 8
    used: int = 0
    log: list = field(default_factory=list)   # [{n, events:[(kind,detail,pid)], hint}]

    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    def can_help(self) -> bool:
        return self.remaining() > 0

    def record(self, events, hint: str = "") -> int:
        self.used += 1
        self.log.append({"n": self.used,
                         "events": [(e.kind, e.detail, e.pid) for e in events],
                         "hint": hint})
        return self.remaining()


class StateWatcher:
    """Edge-triggered detector: turns a stream of snapshots into a few teachable events.

    Fires each condition once per episode (when it becomes true), and re-arms when it clears,
    so Coach gets a nudge on the *transition*, not every tick."""

    def __init__(self, starve: int = 4, monopoly: int = 5, zombie: int = 3) -> None:
        self.starve, self.monopoly, self.zombie = starve, monopoly, zombie
        self._runnable_streak: dict[int, int] = {}
        self._running_streak: dict[int, int] = {}
        self._zombie_age: dict[int, int] = {}
        self._fired: set = set()          # (kind, pid) currently-active conditions

    def observe(self, snap: Snapshot) -> list[OsEvent]:
        events: list[OsEvent] = []
        present = {p.pid for p in snap.procs}
        others_runnable = sum(1 for p in snap.procs if p.state == "runnable")
        any_active = False
        for p in snap.procs:
            if p.state == "running":
                any_active = True
                self._running_streak[p.pid] = self._running_streak.get(p.pid, 0) + 1
                self._runnable_streak[p.pid] = 0
            elif p.state == "runnable":
                any_active = True
                self._runnable_streak[p.pid] = self._runnable_streak.get(p.pid, 0) + 1
                self._running_streak[p.pid] = 0
            else:
                self._running_streak[p.pid] = 0
                self._runnable_streak[p.pid] = 0
            if p.state == "zombie":
                self._zombie_age[p.pid] = self._zombie_age.get(p.pid, 0) + 1
            else:
                self._zombie_age.pop(p.pid, None)

        # starvation — runnable a long time without ever getting the CPU
        for pid, streak in self._runnable_streak.items():
            self._edge(events, ("starvation", pid), streak >= self.starve,
                       lambda pid=pid, s=streak: OsEvent(
                           "starvation",
                           f"pid {pid} has stayed RUNNABLE for {s} slices without running "
                           "— it may be starving under this policy.", pid))
        # cpu monopoly — one pid holds the CPU while others are ready
        for pid, streak in self._running_streak.items():
            self._edge(events, ("cpu_monopoly", pid),
                       streak >= self.monopoly and others_runnable > 0,
                       lambda pid=pid, s=streak: OsEvent(
                           "cpu_monopoly",
                           f"pid {pid} has held the CPU for {s} slices while others are "
                           "runnable — is the time-slice too large, or the policy unfair?", pid))
        # zombie not reaped
        for pid, age in self._zombie_age.items():
            self._edge(events, ("zombie_leak", pid), age >= self.zombie,
                       lambda pid=pid, a=age: OsEvent(
                           "zombie_leak",
                           f"pid {pid} has been a ZOMBIE for {a} snapshots — its parent "
                           "hasn't wait()ed for it.", pid))
        # nothing runnable/running — idle or stuck
        self._edge(events, ("idle", None), not any_active,
                   lambda: OsEvent("idle",
                                   "no process is runnable or running — the CPU is idle "
                                   "(all sleeping/zombie). Deadlock, or just waiting?"))
        # drop fired flags for pids that vanished
        self._fired = {(k, pid) for (k, pid) in self._fired
                       if pid is None or pid in present}
        return events

    def _edge(self, out, key, cond, make) -> None:
        if cond and key not in self._fired:
            out.append(make())
            self._fired.add(key)
        elif not cond:
            self._fired.discard(key)

    def active(self, kind: str | None = None) -> set:
        """The pids whose conditions are CURRENTLY active (not just fired once) — so the UI can
        badge a starving/monopolising proc for as long as it's true, then clear it. Optionally
        filter by kind ('starvation' | 'cpu_monopoly' | 'zombie_leak')."""
        return {pid for (k, pid) in self._fired
                if pid is not None and (kind is None or k == kind)}


# --------------------------------------------------------------------------- #
# The state card (what the agent is fed)
# --------------------------------------------------------------------------- #
_STATE_ORDER = {"running": 0, "runnable": 1, "sleeping": 2, "zombie": 3, "used": 4,
                "unused": 5}


def state_card(snap: Snapshot, timeline: SchedTimeline | None = None,
               meta: dict | None = None, deltas: list[str] | None = None,
               level: int = 0) -> str:
    """Render a compact, LLM-facing snapshot of the xv6 kernel. `level` is progressive:
    0 = scheduling picture (always on), 1 = + registers & kernel stack, 2 = + memory/FS."""
    if snap is None:
        return ""
    meta = meta or {}
    lines = ["xv6 Machine — live kernel state (ground truth; describe only what appears here)"]
    pol = meta.get("policy", "round-robin")
    ts = meta.get("timeslice")
    head = f"policy: {pol}"
    if ts is not None:
        head += f" · time-slice: {ts} tick{'s' if ts != 1 else ''}"
    if snap.ticks is not None:
        head += f" · ticks: {snap.ticks}"
    lines.append(head)
    run = snap.running_pid
    runp = next((p for p in snap.procs if p.pid == run), None)
    lines.append(f"running: pid {run} ({runp.name})" if runp else "running: (none — idle)")
    if snap.procs:
        lines.append("processes (proc[]):")
        for p in sorted(snap.procs, key=lambda p: (_STATE_ORDER.get(p.state, 9), p.pid)):
            mark = " *" if p.pid == run else "  "
            lines.append(f" {mark} pid {p.pid:<3} {p.state:<9} {p.name}")
    if timeline is not None:
        tail = ",".join(str(s.pid) for s in timeline.recent(10))
        lines.append(f"context switches: {timeline.switches()} · recent CPU: {tail}")
    if deltas:
        lines.append("changes since your last question: " + "; ".join(deltas))

    if level >= 1:
        cpu = snap.cpu
        if cpu is not None:
            regs = " ".join(f"{k}={cpu.key(k)}" for k in
                            ("pc", "sp", "ra", "satp", "a0", "a7"))
            lines.append("CPU registers: " + regs)
        if snap.stack:
            lines.append("kernel stack (bt):")
            for i, f in enumerate(snap.stack):
                loc = f" {f.loc}" if f.loc else ""
                lines.append(f"   #{i} {f.fn}{loc}")
    if level >= 2:
        pt = getattr(snap, "page_table", None)
        lines.append("memory: " + (pt if pt else
                     "page-table / FS detail not exported in this build"))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# MachineState — the owner
# --------------------------------------------------------------------------- #
@dataclass
class MachineState:
    """Owns the live state for one xv6 Machine. `provider` has snapshot()/step()/
    set_timeslice() (offline DemoScheduler or the Mac GDB bridge)."""
    provider: object                              # scheduler feed (snapshot()/step()/…)
    device_id: str = ""
    policy: str = "round-robin"
    mode: str = "real"                            # "real" (live kernel) | "demo" (stand-in). A
    #                                               USER choice — never auto-switched (see set_mode)
    latest: Snapshot | None = None
    timeline: SchedTimeline = field(default_factory=SchedTimeline)
    cpu_timelines: dict = field(default_factory=dict)   # cpu_index -> SchedTimeline (SMP)
    watcher: StateWatcher = field(default_factory=StateWatcher)
    ledger: CoachLedger = field(default_factory=CoachLedger)
    vm: object = None                             # virtual-memory reader (snapshot()->VmSnapshot)
    fs: object = None                             # file-system reader (snapshot()->FsSnapshot)
    on_event: object = None                       # callback(self) fired on new pedagogical events
    _events: list = field(default_factory=list)
    _prev_card: dict = field(default_factory=dict)
    # The two data "planes" the mode toggles between: each is (provider, vm, fs). The injected
    # trio becomes the plane matching the initial `mode`; the other is created lazily. `_real`
    # may stay None when no xv6 is running — then Real mode shows an error, never demo data.
    _real: tuple | None = None
    _demo: tuple | None = None

    def __post_init__(self) -> None:
        if self.vm is None:
            self.vm = DemoVm()                    # offline stand-ins; Mac bridge overrides
        if self.fs is None:
            self.fs = DemoDisk()
        trio = (self.provider, self.vm, self.fs)
        if self.mode == "demo":
            self._demo = self._demo or trio
        else:
            self._real = self._real or trio
        try:
            self._ingest(self.provider.snapshot())
        except Exception:
            pass

    # -- mode (Real/Demo) — an explicit user action, never automatic --------- #
    def has_real(self) -> bool:
        """True when a live kernel plane is attached (an xv6 is running)."""
        return bool(self._real and self._real[0] is not None)

    def attach_real(self, provider, vm=None, fs=None) -> None:
        """Wire the live bridge as the Real plane (called when the topology starts). If we're
        currently in Real mode, switch to it now so an already-open Lab goes live."""
        self._real = (provider, vm if vm is not None else getattr(provider, "vm", None),
                      fs if fs is not None else getattr(provider, "fs", None))
        if self.mode == "real":
            self.provider, self.vm, self.fs = self._real
            self.refresh()

    def set_mode(self, mode: str) -> None:
        """Switch the active data plane. This is the ONLY place the source changes, and only when
        the user asks — nothing auto-falls-back from real to demo. Clears the timelines so demo
        and real samples never blend on the Gantt."""
        if mode not in ("real", "demo") or mode == self.mode:
            return
        self.mode = mode
        prov, vm, fs = self._plane(mode)
        self.provider, self.vm, self.fs = prov, vm, fs
        self.timeline = SchedTimeline()
        self.cpu_timelines = {}
        self.latest = None
        if prov is not None:
            try:
                self._ingest(prov.snapshot())
            except Exception:
                pass

    def _plane(self, mode: str) -> tuple:
        if mode == "real":
            return self._real if self.has_real() else (None, None, None)
        if self._demo is None:                    # build the demo plane on first switch to it
            self._demo = (DemoScheduler(), DemoVm(), DemoDisk())
        return self._demo

    # -- reads --------------------------------------------------------------- #
    @property
    def timeslice(self) -> int:
        return int(getattr(self.provider, "timeslice", 1) or 1)

    def refresh(self) -> Snapshot | None:
        if self.provider is None:                 # Real mode with no running kernel -> no data
            return None
        self._ingest(self.provider.snapshot())
        return self.latest

    def step(self) -> Snapshot | None:
        if self.provider is None:
            return None
        self._ingest(self.provider.step())
        return self.latest

    def _ingest(self, snap: Snapshot | None) -> None:
        # An empty process list means the read FAILED (init+sh always exist), not that the
        # kernel has no processes — so keep the last good snapshot instead of blanking the UI.
        if snap is None or not getattr(snap, "procs", None):
            return
        self.latest = snap
        self.timeline.add(snap)
        # per-CPU timelines for the SMP Gantt strips; fall back to one strip (cpu 0) when the
        # kernel reports no CPU lines (single-CPU or older build).
        cpus = snap.cpus or ({0: snap.running_pid} if snap.running_pid is not None else {})
        for ci, pid in cpus.items():
            name = next((p.name for p in snap.procs if p.pid == pid), "")
            self.cpu_timelines.setdefault(ci, SchedTimeline()).add_run(pid, snap.ticks, name)
        self._emit(self.watcher.observe(snap))

    def _emit(self, events) -> None:
        """Record events; notify a listener (proactive Coach) only for genuine teachable
        moments — NOT the student's own control changes, which they already know about."""
        if not events:
            return
        self._events.extend(events)
        if self.on_event and any(e.kind != "control" for e in events):
            try:
                self.on_event(self)
            except Exception:
                pass

    def pending_events(self) -> bool:
        return bool(self._events)

    def scheduling_flags(self) -> dict:
        """Currently-active scheduling conditions, for the scheduler-face badges:
        {"starvation": {pids}, "cpu_monopoly": {pids}, "zombie_leak": {pids}}. Read-only view of
        the watcher — does NOT drain the Coach event queue."""
        return {kind: self.watcher.active(kind)
                for kind in ("starvation", "cpu_monopoly", "zombie_leak")}

    def shadows(self) -> dict:
        """The shadow manifest ({name: ShadowStatus}) from the live provider — which student
        shadows are wired and healthy. Empty when the provider (or build) has no shadows."""
        getter = getattr(self.provider, "shadows", None)
        if callable(getter):
            try:
                return getter() or {}
            except Exception:
                return {}
        return {}

    # -- controls ------------------------------------------------------------ #
    def set_timeslice(self, ticks: int) -> None:
        old = self.timeslice
        try:
            self.provider.set_timeslice(ticks)
        except Exception:
            pass
        if int(ticks) != old:
            self._emit([OsEvent(
                "control", f"you changed the time-slice {old} -> {ticks} ticks")])

    def set_policy(self, policy: str) -> None:
        if policy and policy != self.policy:
            self._emit([OsEvent(
                "control", f"you switched the scheduler policy {self.policy} -> {policy}")])
            self.policy = policy
        setter = getattr(self.provider, "set_policy", None)
        if callable(setter):
            try:
                setter(policy)
            except Exception:
                pass

    # -- for Coach ----------------------------------------------------------- #
    def drain_events(self) -> list:
        ev, self._events = self._events, []
        return ev

    # -- for the agent (Explain / Chat context) ------------------------------ #
    def card(self, level: int = 0) -> str:
        meta = {"policy": self.policy, "timeslice": self.timeslice}
        deltas = self._compute_deltas(meta)
        # scheduler card (L0/L1); at L2 append live memory + FS summaries (real ground truth,
        # so paging/file-system questions are grounded in this student's kernel, not training).
        base = state_card(self.latest, self.timeline, meta, deltas, min(level, 1))
        if level >= 2:
            extra = []
            for provider, summarize in ((self.vm, memory_summary), (self.fs, fs_summary)):
                try:
                    s = summarize(provider.snapshot())
                    if s:
                        extra.append(s)
                except Exception:
                    pass
            if extra:
                base += "\n\n" + "\n\n".join(extra)
        return base

    def _compute_deltas(self, meta: dict) -> list[str]:
        """Short 'what changed since the last time the agent looked' line for the card."""
        out: list[str] = []
        prev = self._prev_card
        if prev:
            if prev.get("policy") != meta["policy"]:
                out.append(f"policy {prev['policy']} -> {meta['policy']}")
            if prev.get("timeslice") != meta["timeslice"]:
                out.append(f"time-slice {prev['timeslice']} -> {meta['timeslice']}")
            sw = self.timeline.switches()
            if sw != prev.get("switches"):
                out.append(f"{sw - prev.get('switches', 0)} more context switch(es)")
        self._prev_card = {"policy": meta["policy"], "timeslice": meta["timeslice"],
                           "switches": self.timeline.switches()}
        return out
