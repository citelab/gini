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
    priority: int | None = None   # GINI sched field (lower = higher); None if the build omits it
    tickets: int | None = None    # lottery weight
    level: int | None = None      # MLFQ queue level (student policies)
    wait_ticks: int | None = None  # aging counter (slices spent RUNNABLE)

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
    source: str = "real"                        # "real" (live kernel) or "demo" (DemoScheduler)


# -- parsers ---------------------------------------------------------------- #
# `<pid> <state> <name> [<ppid>]` — ppid is optional (stock procdump omits it; gini_dump adds it).
_PROC_RE = re.compile(r"^\s*(\d+)\s+([A-Za-z]+)\s+(\S+)(?:\s+(\d+))?")


def parse_procdump(text: str) -> list[Proc]:
    """xv6 process dump lines `<pid> <state> <name> [<ppid>]` -> Procs (active only). The
    optional ppid (from gini_dump) drives the process tree; stock procdump lines parse fine
    with parent left as None."""
    out: list[Proc] = []
    for line in (text or "").splitlines():
        m = _PROC_RE.match(line)
        if not m:
            continue
        st = _STATE.get(m.group(2).lower())
        if st is None or st == "unused":
            continue
        parent = int(m.group(4)) if m.group(4) is not None else None
        out.append(Proc(int(m.group(1)), st, m.group(3), parent=parent))
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
    (so the UI can show the real quantum/policy and confirm a control took effect)."""
    m = re.search(r"SCHED\s+policy\s+(\d+)\s+quantum\s+(\d+)", text or "")
    return {"policy": int(m.group(1)), "quantum": int(m.group(2))} if m else {}


# scheduler policy codes must match gini_patch.py's gini_pick() (0=RR, 1=priority, 2=lottery).
# Custom student policies (MLFQ, stride, …) added via the Scheduler Builder extend this map.
POLICY_NAMES = {0: "round-robin", 1: "priority", 2: "lottery"}
POLICY_IDS = {v: k for k, v in POLICY_NAMES.items()}


def policy_name(num) -> str:
    return POLICY_NAMES.get(num, f"policy{num}")


_PROC_SCHED_RE = re.compile(
    r"PROC\s+(\d+)\s+pri\s+(-?\d+)\s+tk\s+(-?\d+)\s+lv\s+(-?\d+)\s+wait\s+(-?\d+)")


def parse_proc_sched(text: str) -> dict:
    """gini_dump's per-proc `PROC <pid> pri P tk T lv L wait W` lines ->
    {pid: {"priority":P, "tickets":T, "level":L, "wait_ticks":W}}. Absent on an older build."""
    out: dict = {}
    for m in _PROC_SCHED_RE.finditer(text or ""):
        out[int(m.group(1))] = {"priority": int(m.group(2)), "tickets": int(m.group(3)),
                                "level": int(m.group(4)), "wait_ticks": int(m.group(5))}
    return out


def apply_proc_sched(procs, text: str) -> list:
    """Set the scheduling fields on each Proc from the `PROC …` lines (no-op if the build omits
    them). Returns the same list for chaining."""
    sched = parse_proc_sched(text)
    for p in procs:
        s = sched.get(p.pid)
        if s:
            p.priority, p.tickets = s["priority"], s["tickets"]
            p.level, p.wait_ticks = s["level"], s["wait_ticks"]
    return procs


def running_pid(procs) -> int | None:
    for p in procs:
        if p.running:
            return p.pid
    return None


# -- shadow manifest -------------------------------------------------------- #
# The kernel emits one line per SHADOWABLE function so the oracle/AI can tell, deterministically,
# which student shadows are wired and healthy — the OS analog of "is this router configured".
#   SHADOW <name> present=<0|1> enabled=<0|1> active=<0|1> faults=<n> hash=<hex|baseline>
# present : a non-stub shadow was compiled in (student wrote something)
# enabled : the shadow toggle is on
# active  : the dispatcher is currently running the shadow (not the primary)
# faults  : times the shadow crashed and fell back to the primary
# hash    : build-time hash of the student's file ("baseline" = the shipped stub)
@dataclass
class ShadowStatus:
    name: str
    present: bool = False
    enabled: bool = False
    active: bool = False
    faults: int = 0
    hash: str = "baseline"

    @property
    def is_student(self) -> bool:
        """A real student submission (not the shipped baseline stub)."""
        return self.present and self.hash not in ("", "baseline")

    @property
    def healthy(self) -> bool:
        """Wired in and running without having crashed back to the primary."""
        return self.active and self.faults == 0


_SHADOW_RE = re.compile(r"SHADOW\s+(\S+)\s+(.*)")


def parse_shadow_manifest(text: str) -> dict:
    """gini_shadowdump lines -> {name: ShadowStatus}. The liveness signal the assignment oracle
    checks first: are the required shadows present, active, and fault-free?"""
    out: dict = {}
    for line in (text or "").splitlines():
        m = _SHADOW_RE.match(line.strip())
        if not m:
            continue
        kv = dict(re.findall(r"(\w+)=(\S+)", m.group(2)))
        out[m.group(1)] = ShadowStatus(
            name=m.group(1),
            present=kv.get("present") == "1",
            enabled=kv.get("enabled") == "1",
            active=kv.get("active") == "1",
            faults=int(kv.get("faults", "0") or 0),
            hash=kv.get("hash", "baseline"))
    return out


def ready_queue(procs) -> list:
    """The RUNNABLE processes in scheduling order — the 'who's waiting now, and why' view that
    complements the Gantt (which is who-ran-over-time). Ordered by MLFQ level, then priority
    (lower number = higher), then pid, so the proc the scheduler would tend to favour is first.
    Missing sched fields sort as 0, so it degrades cleanly on an older kernel."""
    ready = [p for p in procs if p.state == "runnable"]
    return sorted(ready, key=lambda p: (p.level or 0, p.priority if p.priority is not None else 0,
                                        p.pid))


# current xv6-riscv system-call numbers (fork=1 .. sync=22); custom syscalls (Syscall Builder)
# start at 23 and are supplied via the `extra` map.
SYSCALL_NAMES = {
    1: "fork", 2: "exit", 3: "wait", 4: "pipe", 5: "read", 6: "kill", 7: "exec", 8: "fstat",
    9: "chdir", 10: "dup", 11: "getpid", 12: "sbrk", 13: "pause", 14: "uptime", 15: "open",
    16: "write", 17: "mknod", 18: "unlink", 19: "link", 20: "mkdir", 21: "close", 22: "sync",
}


def syscall_name(num: int, extra: dict | None = None) -> str:
    if extra and num in extra:
        return extra[num]
    return SYSCALL_NAMES.get(num, f"sys{num}")


def parse_sccounts(text: str) -> dict:
    """gini_scdump `SC <num> <count>` lines -> {syscall_number: cumulative_count}."""
    return {int(m.group(1)): int(m.group(2))
            for m in re.finditer(r"SC (\d+) (\d+)", text or "")}


@dataclass
class SyscallEvent:
    pid: int
    num: int
    a0: str = ""       # first arg (hex) at call time
    ret: str = ""      # return value (hex)


def parse_sctrace(text: str) -> list:
    """gini_scdump `TRACE <pid> <num> <a0> <ret>` lines -> [SyscallEvent] (oldest -> newest)."""
    out: list = []
    for m in re.finditer(r"TRACE (\d+) (\d+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+)", text or ""):
        out.append(SyscallEvent(int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)))
    return out


class SyscallRate:
    """Rolling per-syscall call count over a time window (default 60s) — the histogram feed.
    Feed cumulative `{num: count}` snapshots with their wall-clock time; `rates()` returns how
    many of each syscall happened in the last `window` seconds (now-count minus the count as of
    ~window ago). Before the window fills, it uses the oldest snapshot (calls-so-far)."""

    def __init__(self, window: float = 60.0, cap: int = 300) -> None:
        from collections import deque
        self.window = window
        self.snaps = deque(maxlen=cap)          # (t, {num: cumulative_count})

    def add(self, t: float, counts: dict) -> None:
        self.snaps.append((t, dict(counts)))

    def rates(self) -> dict:
        if not self.snaps:
            return {}
        now_t, now = self.snaps[-1]
        cutoff = now_t - self.window
        base = self.snaps[0][1]                 # oldest, until we have a snapshot past the cutoff
        for t, c in self.snaps:
            if t <= cutoff:
                base = c
            else:
                break
        out = {num: now[num] - base.get(num, 0) for num in now}
        return {k: v for k, v in out.items() if v > 0}


# -- traps & interrupts (the trap-taxonomy ring; gini_trapdump over Ctrl-R) ------------------- #
TRAP_KINDS = {0: "syscall", 1: "pagefault", 2: "timer", 3: "device", 4: "illegal", 5: "other"}


def trap_kind_name(kind: int) -> str:
    return TRAP_KINDS.get(kind, f"kind{kind}")


def parse_trapcounts(text: str) -> dict:
    """gini_trapdump `TC <kind> <name> <count>` lines -> {kind_index: cumulative_count}."""
    return {int(m.group(1)): int(m.group(2))
            for m in re.finditer(r"TC (\d+) \w+ (\d+)", text or "")}


@dataclass
class TrapEvent:
    pid: int
    kind: int
    cause: str = ""     # scause (hex); interrupt causes have the top bit set
    epc: str = ""       # faulting / trapping PC (hex)
    tval: str = ""      # stval — faulting address for page faults (hex)


def parse_traptrace(text: str) -> list:
    """gini_trapdump `TR <pid> <kind> <cause> <epc> <tval>` lines -> [TrapEvent] (oldest->newest)."""
    out: list = []
    for m in re.finditer(
            r"TR (\d+) (\d+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+)", text or ""):
        out.append(TrapEvent(int(m.group(1)), int(m.group(2)),
                             m.group(3), m.group(4), m.group(5)))
    return out


class TrapRate(SyscallRate):
    """Rolling per-trap-kind counts over a window (default 60s) — the trap histogram feed.
    Same mechanics as SyscallRate (cumulative snapshots -> deltas in the window), keyed by trap
    kind index instead of syscall number, so `rates()` returns traps-per-kind in the last window."""


# -- freeze a real trap (Phase 2: /trapcatch -> seed the CPU journey with live values) --------- #
# RISC-V S-mode exception codes -> a human name (interrupts are handled separately, by code).
_SCAUSE_EXC = {
    0: "instruction address misaligned", 1: "instruction access fault", 2: "illegal instruction",
    3: "breakpoint", 4: "load address misaligned", 5: "load access fault",
    6: "store address misaligned", 7: "store access fault", 8: "ecall from U-mode (syscall)",
    12: "instruction page fault", 13: "load page fault", 15: "store page fault",
}


def decode_scause(cause) -> tuple:
    """An scause value (hex string or int) -> (kind_index, human_name). Mirrors the kernel
    gini_kind() bucketing, but adds the specific exception/interrupt name for the journey caption."""
    try:
        c = int(cause, 16) if isinstance(cause, str) else int(cause)
    except (ValueError, TypeError):
        return 5, "other"
    if c & (1 << 63):                                   # interrupt (top bit set)
        code = c & 0xff
        if code == 9:
            return 3, "supervisor external interrupt (device)"
        if code == 5:
            return 2, "supervisor timer interrupt"
        if code == 1:
            return 2, "supervisor software interrupt"
        return 2, f"interrupt (code {code})"
    code = c & 0xff
    if code == 8:
        return 0, _SCAUSE_EXC[8]
    if code in (12, 13, 15):
        return 1, _SCAUSE_EXC[code]
    if code == 2:
        return 4, _SCAUSE_EXC[2]
    return 5, _SCAUSE_EXC.get(code, f"exception (code {code})")


@dataclass
class TrapFrame:
    """A single trap frozen at usertrap entry (from /trapcatch): the trap CSRs plus the user
    registers uservec saved into the trapframe. `ok` is False when the catch timed out (idle
    kernel) — the journey then falls back to its authored captions."""
    scause: str = ""
    sepc: str = ""
    stval: str = ""
    pid: int | None = None
    regs: dict = field(default_factory=dict)    # epc/ra/sp/a0../a7 (hex strings)
    kind: int = 5
    kind_name: str = "other"
    ok: bool = False


def parse_trapframe(text: str) -> TrapFrame:
    """Parse the agent's /trapcatch gdb output (`key 0x…` lines after a ===TRAP=== marker) into a
    TrapFrame. Missing/garbled fields are tolerated; ok=True only once we have a valid scause."""
    fr = TrapFrame()
    body = (text or "").split("===TRAP===", 1)[-1]
    if "gdb-timeout" in (text or "") or "gdb-error" in (text or ""):
        return fr                                       # ok stays False -> authored fallback
    for line in body.splitlines():
        m = re.match(r"\s*([a-z]\w*)\s+(0x[0-9a-fA-F]+|-?\d+)\s*$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if key in ("scause", "sepc", "stval"):
            setattr(fr, key, val)
        elif key == "pid":
            try:
                fr.pid = int(val)
            except ValueError:
                pass
        elif key in ("epc", "ra", "sp", "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7"):
            fr.regs[key] = val
    if fr.scause:
        fr.kind, fr.kind_name = decode_scause(fr.scause)
        fr.ok = True
    return fr


@dataclass
class TreeNode:
    proc: Proc
    depth: int


def build_process_tree(procs) -> list:
    """Order procs as a depth-first process TREE by parent links, returning [TreeNode(proc, depth)]
    in display order (depth = indentation). Roots are procs whose parent is 0/None/missing (init).
    Defensive against cycles and orphans so a bad read can never hang or drop a process."""
    by_pid = {p.pid: p for p in procs}
    children: dict = {}
    roots: list = []
    for p in procs:
        par = p.parent
        if par and par in by_pid and par != p.pid:
            children.setdefault(par, []).append(p)
        else:
            roots.append(p)
    out: list = []
    seen: set = set()

    def walk(p, depth):
        if p.pid in seen:                       # cycle guard
            return
        seen.add(p.pid)
        out.append(TreeNode(p, depth))
        for c in sorted(children.get(p.pid, []), key=lambda c: c.pid):
            walk(c, depth + 1)

    for r in sorted(roots, key=lambda p: p.pid):
        walk(r, 0)
    for p in procs:                             # any left over (cycle) -> show at root
        if p.pid not in seen:
            out.append(TreeNode(p, 0))
    return out


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

    def shares(self, n: int = 60) -> dict:
        """CPU share over the last `n` slots: {pid: fraction of slots it held} (idle excluded).
        This is the evidence a lottery/fairness assignment leans on — the share should track the
        ticket ratio. Coarse (the timeline is sampled), but a real observation of the live kernel."""
        pids = [s.pid for s in self.slots[-n:] if s.pid is not None]
        total = len(pids)
        if not total:
            return {}
        from collections import Counter
        return {pid: c / total for pid, c in Counter(pids).items()}


# -- offline demo provider -------------------------------------------------- #
# A pure, deterministic feed over a few procs, so the Machine Lab is explorable (and testable)
# without a live QEMU/GDB. It honours the same policies as the real gini_pick() so the scheduler
# face behaves offline: round-robin, priority (with aging), and lottery (ticket-weighted). On the
# Mac a real GDB bridge replaces this with the same Snapshot shape.
_DEMO_PROCS = [(1, "init"), (2, "sh"), (3, "spin"), (4, "spin"), (5, "primes")]
# per-proc scheduling params for the CPU-bound demo procs (init/sh sleep, so they don't compete).
_DEMO_META = {3: {"priority": 5, "tickets": 1},    # high priority, few tickets
              4: {"priority": 10, "tickets": 2},
              5: {"priority": 10, "tickets": 4}}    # low priority, most tickets


class DemoScheduler:
    """Deterministic policy-aware feed of Snapshots — the offline stand-in for the GDB bridge.
    Mirrors gini_pick(): round-robin / priority (aging) / lottery, so switching the policy in the
    UI visibly changes who runs even with no container attached."""

    def __init__(self, timeslice: int = 1, policy: str = "round-robin") -> None:
        self.timeslice = max(1, int(timeslice))
        self.policy = policy
        self._ticks = 0
        self._runnable = [3, 4, 5]
        self._run = 3             # currently running pid (starts on the first CPU-bound proc)
        self._rr_ix = 2           # round-robin cursor into _DEMO_PROCS
        self._wait = {p: 0 for p in self._runnable}   # aging counters (priority policy)
        self._seed = 2463534242   # xorshift PRNG state (lottery), fixed -> deterministic

    def set_timeslice(self, ticks: int) -> None:
        self.timeslice = max(1, int(ticks))

    def set_policy(self, policy) -> None:
        """Accept a policy name ('priority') or its numeric id (1)."""
        self.policy = POLICY_NAMES.get(policy, policy) if isinstance(policy, int) else policy

    def sc(self) -> str:
        """Offline demo of gini_scdump — growing syscall counts + a few recent calls, so the
        histogram/trace panels are explorable without a container."""
        self._sc_t = getattr(self, "_sc_t", 0) + 1
        n = self._sc_t
        lines = [f"SC 1 {n}", f"SC 16 {n * 7}", f"SC 5 {n * 3}", f"SC 13 {n * 2}", f"SC 12 {n}"]
        trace = ["TRACE 2 1 0x0 0x5", "TRACE 5 16 0x4 0x6", "TRACE 5 12 0x1000 0x4000",
                 "TRACE 2 3 0x0 0x5"]
        return "\n".join(lines + trace) + "\n"

    def traps(self) -> str:
        """Offline demo of gini_trapdump — a plausible growing trap mix so the Traps face is
        explorable without a container: mostly timer, a syscall trickle, an occasional page
        fault. Counters are cumulative (TrapRate turns them into a 60s window)."""
        self._tr_t = getattr(self, "_tr_t", 0) + 1
        n = self._tr_t
        kinds = [(0, "syscall", n * 4), (1, "pagefault", n // 2), (2, "timer", n * 9),
                 (3, "device", n // 3), (4, "illegal", 0), (5, "other", 0)]
        tc = [f"TC {k} {name} {cnt}" for k, name, cnt in kinds]
        tr = ["TR 5 2 0x8000000000000005 0x0000000000001050 0x0",       # a timer interrupt
              "TR 5 1 0x000000000000000f 0x0000000000001080 0x0000000000004000",  # store fault
              "TR 2 0 0x0000000000000008 0x0000000000001d3c 0x0",       # a syscall (ecall)
              "TR 5 2 0x8000000000000005 0x0000000000001054 0x0"]
        return "\n".join(tc + tr) + "\n"

    def catch_trap(self) -> "TrapFrame":
        """Offline demo of /trapcatch — a plausible frozen store page fault, so the CPU journey
        can be seeded with real-looking values without a container."""
        return TrapFrame(
            scause="0x000000000000000f", sepc="0x0000000000001080",
            stval="0x0000000000004000", pid=5,
            regs={"epc": "0x0000000000001080", "ra": "0x0000000000001d3c",
                  "sp": "0x0000003fffff9000", "a0": "0x0000000000000005", "a7": "0x000000000000000f"},
            kind=1, kind_name="store page fault", ok=True)

    def _pick(self) -> int:
        """Choose the next running pid per policy — the offline mirror of kernel gini_pick()."""
        rn = self._runnable
        if self.policy == "priority":
            best, best_eff = None, None
            for p in rn:
                self._wait[p] += 1                       # aging: waiting raises effective priority
                eff = _DEMO_META[p]["priority"] - self._wait[p] // 4
                if best is None or eff < best_eff:
                    best, best_eff = p, eff
            self._wait[best] = 0
            return best
        if self.policy == "lottery":
            total = sum(_DEMO_META[p]["tickets"] for p in rn)
            self._seed ^= (self._seed << 13) & 0xFFFFFFFF
            self._seed ^= self._seed >> 17
            self._seed ^= (self._seed << 5) & 0xFFFFFFFF
            win, acc = self._seed % total, 0
            for p in rn:
                acc += _DEMO_META[p]["tickets"]
                if win < acc:
                    return p
            return rn[-1]
        # round-robin
        self._rr_ix = (self._rr_ix + 1) % len(_DEMO_PROCS)
        while _DEMO_PROCS[self._rr_ix][0] not in rn:
            self._rr_ix = (self._rr_ix + 1) % len(_DEMO_PROCS)
        return _DEMO_PROCS[self._rr_ix][0]

    def step(self) -> "Snapshot":
        """Advance one context switch and return the new snapshot."""
        self._ticks += self.timeslice
        self._run = self._pick()
        return self.snapshot()

    def snapshot(self) -> "Snapshot":
        run_pid = self._run
        procs = []
        for pid, name in _DEMO_PROCS:
            st = ("running" if pid == run_pid
                  else "runnable" if pid in self._runnable else "sleeping")
            meta = _DEMO_META.get(pid)
            procs.append(Proc(pid, st, name,
                              priority=meta["priority"] if meta else None,
                              tickets=meta["tickets"] if meta else None,
                              level=0 if meta else None,
                              wait_ticks=self._wait.get(pid)))
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
                        cpu=cpu, stack=stack, source="demo")
