"""xv6 (RISC-V) state model + parsers — the Machine Lab's read side.

State is read from a running xv6 under QEMU without patching the kernel (GDB-read-first):
  • the process table from xv6's built-in Ctrl-P dump (`procdump`), or from GDB reading `proc[]`;
  • CPU registers from GDB `info registers`;
  • the kernel stack from GDB `bt`.

This module is PURE (text -> dataclasses), so the parsing is unit-tested without QEMU/GDB.
The GDB/console client that produces the text is a thin runtime bridge (Mac/Docker side); the
Machine Lab renders whatever this returns and accumulates a scheduling timeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# xv6 procdump prints short state words (Ctrl-P); the in-container gdb agent emits full words.
# Accept both and normalise to full names.
_STATE = {"unused": "unused", "used": "used", "sleep": "sleeping", "sleeping": "sleeping",
          "runble": "runnable", "runnable": "runnable", "run": "running", "running": "running",
          "zombie": "zombie"}
_RUNNING = "running"


@dataclass
class Proc:
    pid: int
    state: str          # unused|used|sleeping|runnable|running|zombie
    name: str
    parent: int | None = None
    cpu_ticks: int | None = None

    @property
    def running(self) -> bool:
        return self.state == _RUNNING


@dataclass
class CpuState:
    regs: dict = field(default_factory=dict)   # name -> value string (pc, sp, ra, satp, …)

    def key(self, name: str) -> str:
        return self.regs.get(name, "—")


@dataclass
class Frame:
    fn: str
    loc: str = ""       # file:line, if known


@dataclass
class Snapshot:
    procs: list = field(default_factory=list)   # [Proc]
    running_pid: int | None = None
    ticks: int | None = None
    cpu: CpuState | None = None
    stack: list = field(default_factory=list)   # [Frame]
    cpus: dict = field(default_factory=dict)    # cpu_index -> running pid (SMP; {} = single CPU)
    cpu_regs: dict = field(default_factory=dict)  # cpu_index -> CpuState (per-CPU registers)


# -- parsers ---------------------------------------------------------------- #
_PROC_RE = re.compile(r"^\s*(\d+)\s+([A-Za-z]+)\s+(\S+)")


def parse_procdump(text: str) -> list[Proc]:
    """xv6 `procdump` (Ctrl-P) lines `<pid> <state> <name>` -> Procs (active only)."""
    out: list[Proc] = []
    for line in (text or "").splitlines():
        m = _PROC_RE.match(line)
        if not m:
            continue
        st = _STATE.get(m.group(2).lower())
        if st is None or st == "unused":
            continue
        out.append(Proc(int(m.group(1)), st, m.group(3)))
    return out


_REG_RE = re.compile(r"^\s*([a-z][a-z0-9_]*)\s+(0x[0-9a-fA-F]+|-?\d+)")


def parse_registers(text: str, keep=("pc", "sp", "ra", "satp", "epc", "a0", "a7")) -> CpuState:
    """GDB `info registers` -> CpuState (all regs; `keep` are the ones the UI leads with)."""
    regs: dict = {}
    for line in (text or "").splitlines():
        m = _REG_RE.match(line)
        if m:
            regs[m.group(1)] = m.group(2)
    return CpuState(regs=regs)


_BT_RE = re.compile(r"^#\d+\s+(?:0x[0-9a-fA-F]+\s+in\s+)?(\w+)\s*\([^)]*\)(?:\s+at\s+(\S+))?")


def parse_backtrace(text: str) -> list[Frame]:
    """GDB `bt` -> [Frame] (innermost first)."""
    out: list[Frame] = []
    for line in (text or "").splitlines():
        m = _BT_RE.match(line.strip())
        if m:
            out.append(Frame(m.group(1), m.group(2) or ""))
    return out


def parse_regs_line(text: str) -> CpuState:
    """The FIRST `REGS …` line's registers (a single-CPU summary for the Ask GINI card)."""
    for line in (text or "").splitlines():
        if "REGS" in line:
            return CpuState(regs=dict(re.findall(r"([a-z0-9]+)\s+(0x[0-9a-fA-F]+)", line)))
    return CpuState(regs={})


def parse_cpu_regs(text: str) -> dict:
    """Parse gini_dump's per-CPU `REGS cpu <i> pid <p> pc 0x.. sp 0x.. …` lines ->
    {cpu_index: CpuState} — each CPU's live registers, no gdb halt."""
    out: dict = {}
    for m in re.finditer(r"REGS cpu (\d+) pid (\d+)([^\n]*)", text or ""):
        regs = dict(re.findall(r"([a-z0-9]+)\s+(0x[0-9a-fA-F]+)", m.group(3)))
        regs["pid"] = m.group(2)
        out[int(m.group(1))] = CpuState(regs=regs)
    return out


def parse_cpu_lines(text: str) -> dict:
    """Parse gini_dump's `CPU <i> pid <n>` lines -> {cpu_index: running_pid}. Empty on a
    single-CPU kernel (or old build) — the UI then falls back to one strip for running_pid."""
    return {int(m.group(1)): int(m.group(2))
            for m in re.finditer(r"CPU\s+(\d+)\s+pid\s+(\d+)", text or "")}


def parse_sched(text: str) -> dict:
    """Parse gini_dump's `SCHED policy N quantum N` line — the kernel's ACTUAL scheduler settings
    (so the UI can show the real quantum and confirm the slider took effect)."""
    m = re.search(r"SCHED\s+policy\s+(\d+)\s+quantum\s+(\d+)", text or "")
    return {"policy": int(m.group(1)), "quantum": int(m.group(2))} if m else {}


def running_pid(procs) -> int | None:
    for p in procs:
        if p.running:
            return p.pid
    return None


# -- scheduling timeline (Gantt) -------------------------------------------- #
@dataclass
class Slot:
    tick: int | None
    pid: int | None
    name: str


class SchedTimeline:
    """Accumulate which process is RUNNING across snapshots — the Gantt strip that makes
    context switches visible. Records a slot only when the running process changes (a switch)
    or the tick advances, so the strip reads as the switch history."""

    def __init__(self, cap: int = 400) -> None:
        self.slots: list[Slot] = []
        self.cap = cap
        self._last_pid: int | None = None

    def add(self, snap: Snapshot) -> None:
        pid = snap.running_pid if snap.running_pid is not None else running_pid(snap.procs)
        name = next((p.name for p in snap.procs if p.pid == pid), "")
        self.add_run(pid, snap.ticks, name)

    def add_run(self, pid, ticks, name="") -> None:
        """Record a single (pid, ticks) sample — used per-CPU on SMP as well as the aggregate."""
        if pid != self._last_pid or (self.slots and self.slots[-1].tick != ticks):
            self.slots.append(Slot(ticks, pid, name))
            self._last_pid = pid
            if len(self.slots) > self.cap:
                self.slots = self.slots[-self.cap:]

    def recent(self, n: int = 60) -> list[Slot]:
        return self.slots[-n:]

    def switches(self) -> int:
        return max(0, sum(1 for i in range(1, len(self.slots))
                          if self.slots[i].pid != self.slots[i - 1].pid))


# -- offline demo provider -------------------------------------------------- #
# A pure, deterministic round-robin over a few procs, so the Machine Lab is explorable
# (and testable) without a live QEMU/GDB. On the Mac a real GDB bridge replaces this with
# the same Snapshot shape. Not a simulation of xv6 internals — just a stand-in feed.
_DEMO_PROCS = [(1, "init"), (2, "sh"), (3, "spin"), (4, "spin"), (5, "primes")]


class DemoScheduler:
    """Deterministic RR feed of Snapshots — the offline stand-in for the GDB bridge."""

    def __init__(self, timeslice: int = 1) -> None:
        self.timeslice = max(1, int(timeslice))
        self._ticks = 0
        self._run_ix = 2          # start on the first CPU-bound proc (pid 3)
        self._runnable = [3, 4, 5]

    def set_timeslice(self, ticks: int) -> None:
        self.timeslice = max(1, int(ticks))

    def step(self) -> "Snapshot":
        """Advance one context switch and return the new snapshot."""
        self._ticks += self.timeslice
        self._run_ix = (self._run_ix + 1) % len(_DEMO_PROCS)
        while _DEMO_PROCS[self._run_ix][0] not in self._runnable:
            self._run_ix = (self._run_ix + 1) % len(_DEMO_PROCS)
        return self.snapshot()

    def snapshot(self) -> "Snapshot":
        run_pid = _DEMO_PROCS[self._run_ix][0]
        procs = [
            Proc(pid, "running" if pid == run_pid
                 else "runnable" if pid in self._runnable
                 else "sleeping", name)
            for pid, name in _DEMO_PROCS]
        pc = 0x80001000 + (run_pid * 0x40) + (self._ticks & 0xF)
        cpu = CpuState(regs={
            "pc": hex(pc), "sp": hex(0x3FFFFF9000 - run_pid * 0x1000),
            "ra": hex(0x80001D3C), "satp": hex(0x8000000000080000 + run_pid),
            "a0": hex(run_pid), "a7": "0x7"})
        stack = [Frame("swtch", "kernel/swtch.S:20"),
                 Frame("sched", "kernel/proc.c:493"),
                 Frame("yield", "kernel/proc.c:515"),
                 Frame("usertrap", "kernel/trap.c:67")]
        return Snapshot(procs=procs, running_pid=run_pid, ticks=self._ticks,
                        cpu=cpu, stack=stack)
