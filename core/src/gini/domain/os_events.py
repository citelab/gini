"""One ordered story from four separate kernel rings — the OS HUD's X-ray.

GINI already records everything a student needs: the syscall ring, the trap ring, the page-fault
ring. What it never had was a way to put them in ONE order, because each ring only knows its own
insertion sequence. The kernel now stamps every recorded event with a global counter
(`gini_seq`), so the rings merge-sort into a single exact timeline.

That timeline is the point. Launching a program crosses every subsystem in the course —

    ecall fork -> proc created -> ecall exec -> inode lookup -> block reads -> page table built
    -> scheduled -> context switch -> return to user -> first touch page-faults -> runs

— and until now a student could see each of those in a different lab, but never as one sequence.

Pure: text in, dataclasses out. No Qt, no Docker; unit-tested against canned dumps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .xv6 import SYSCALL_NAMES, trap_kind_name
from .xv6_vm import SCAUSE_NAMES

# which lane an event belongs to — the X-ray's swimlanes, top to bottom
LANES = ("syscall", "proc", "memory", "fs", "trap")

# syscalls that change the shape of the process world, not just its data. These are the spine of
# a launch story, so the X-ray promotes them out of the raw syscall stream.
_PROC_CALLS = {"fork", "exec", "exit", "wait", "kill", "sbrk", "sbrklazy"}
_FS_CALLS = {"open", "read", "write", "close", "mknod", "unlink", "link", "mkdir",
             "chdir", "dup", "pipe", "fstat", "sync"}


@dataclass(frozen=True)
class OsEvent:
    """One thing the kernel did, placed on the global clock."""
    seq: int
    pid: int
    lane: str                 # one of LANES
    kind: str                 # "fork" | "page fault" | "timer int" | …
    detail: str = ""          # human-readable specifics (address, return value, cause)
    raw: str = ""             # the source line, so a student can always see the ground truth

    @property
    def label(self) -> str:
        return f"{self.kind} {self.detail}".strip()


# ---- parsers: each ring -> [OsEvent] ---------------------------------------------------------
# All three lines end with the seq the kernel stamped. Older kernels have no seq at all, so the
# trailing group is optional and those events are dropped from the X-ray (they cannot be ordered)
# while the existing per-ring views keep working exactly as before.
_TRACE_RE = re.compile(r"^TRACE (\d+) (\d+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+)(?: (\d+))?\s*$", re.M)
_FLT_RE = re.compile(r"^FLT (\d+) (\d+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+)(?: (\d+))?\s*$", re.M)
# ANCHORED, and that matters: this pattern ends in `\s*$`, so a field appended to the wire
# format does not merely go unread — the whole line stops matching and the X-ray's trap lane goes
# quietly empty. `h<n>` (the hart) was added to `TR` and is accepted here for exactly that reason;
# the sibling parser in `xv6.py` is unanchored and would have kept working, so this panel would
# have been the only casualty and the one nobody was looking at.
_TR_RE = re.compile(
    r"^TR (\d+) (\d+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+)"
    r"(?: (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+))?(?: (\d+))?"
    r"(?: h(\d+))?\s*$", re.M)


def syscall_events(text: str, names: dict | None = None) -> list:
    """`TRACE <pid> <num> <a0> <ret> <seq>` -> events, split into the proc / fs / syscall lanes so
    a launch reads as a story rather than a flat call list."""
    out = []
    for m in _TRACE_RE.finditer(text or ""):
        if m.group(5) is None:
            continue                                    # unstamped: cannot be ordered
        num = int(m.group(2))
        name = (names or {}).get(num) or SYSCALL_NAMES.get(num, f"sys{num}")
        lane = "proc" if name in _PROC_CALLS else ("fs" if name in _FS_CALLS else "syscall")
        ret = m.group(4)
        detail = f"-> {int(ret, 16)}" if lane == "proc" else f"-> {ret}"
        out.append(OsEvent(seq=int(m.group(5)), pid=int(m.group(1)), lane=lane,
                           kind=name, detail=detail, raw=m.group(0)))
    return out


def fault_events(text: str) -> list:
    """`FLT <pid> <scause> <va> <epc> <seq>` -> memory-lane events."""
    out = []
    for m in _FLT_RE.finditer(text or ""):
        if m.group(5) is None:
            continue
        cause = SCAUSE_NAMES.get(int(m.group(2)), f"scause {m.group(2)}")
        out.append(OsEvent(seq=int(m.group(5)), pid=int(m.group(1)), lane="memory",
                           kind=cause, detail=f"at {m.group(3)}", raw=m.group(0)))
    return out


def trap_events(text: str, include_timer: bool = False, include_device: bool = False) -> list:
    """`TR <pid> <kind> <cause> <epc> <tval> [csrs] <seq>` -> trap-lane events.

    Two kinds are excluded by default, for different reasons:

    * **timer** — by far the most numerous events; they would bury the story of a launch. Turn
      them on to teach preemption specifically.
    * **device** — these are mostly *us*. Every dump the HUD requests is a control byte written to
      the serial port, which raises a UART interrupt, which is recorded as a device trap. Polling
      three endpoints a second manufactures a steady stream of them, so leaving them on fills the
      trap lane with a picture of the measurement rather than of the machine. (Same observer
      effect the CSR strip has, where `scause` permanently reads "external int".) Turn them on to
      teach device interrupts — ideally while doing real disk I/O, where the virtio traps are the
      interesting ones.
    """
    out = []
    for m in _TR_RE.finditer(text or ""):
        seq = m.group(9)
        if seq is None:
            continue
        kind = trap_kind_name(int(m.group(2)))
        if kind == "timer" and not include_timer:
            continue
        if kind == "device" and not include_device:
            continue
        if kind in ("syscall", "pagefault"):
            continue          # already told, in richer form, by the syscall and fault rings
        out.append(OsEvent(seq=int(seq), pid=int(m.group(1)), lane="trap",
                           kind=kind, detail=f"cause {m.group(3)}", raw=m.group(0)))
    return out


def merge(*streams, pid: int | None = None, limit: int = 0) -> list:
    """Merge event streams into one exactly-ordered timeline.

    `pid` filters to a single process — the usual way to read a launch, since a busy machine
    produces thousands of unrelated events. `limit` keeps only the newest N.
    """
    evs = [e for s in streams for e in s]
    if pid is not None:
        evs = [e for e in evs if e.pid == pid]
    evs.sort(key=lambda e: e.seq)
    return evs[-limit:] if limit else evs


# ---- episodes: the story of one launch -------------------------------------------------------
@dataclass
class Episode:
    """The events belonging to one program launch, from the `exec` that started it to the last
    event we have for that pid. This is what the X-ray draws when a student launches something."""
    pid: int
    events: list = field(default_factory=list)
    name: str = ""

    @property
    def span(self) -> tuple:
        return (self.events[0].seq, self.events[-1].seq) if self.events else (0, 0)

    @property
    def lanes(self) -> dict:
        out: dict = {ln: [] for ln in LANES}
        for e in self.events:
            out.setdefault(e.lane, []).append(e)
        return out

    def summary(self) -> str:
        """A one-line telling of the story — what the debrief slide shows."""
        by = self.lanes
        n_fs = len(by.get("fs", []))
        n_mem = len(by.get("memory", []))
        parts = [f"pid {self.pid}"]
        if any(e.kind == "exec" for e in self.events):
            parts.append("exec")
        if n_fs:
            parts.append(f"{n_fs} file op(s)")
        if n_mem:
            parts.append(f"{n_mem} page fault(s)")
        return " · ".join(parts)


def episodes(events: list) -> list:
    """Split a merged stream into per-process episodes, newest process first.

    A launch is bounded by `fork`/`exec` on one side and `exit` on the other, but a student may
    open the HUD mid-run, so an episode is simply "every event for this pid, in order" — which is
    the useful thing to look at either way.
    """
    order: list = []
    seen: dict = {}
    for e in events:
        if e.pid not in seen:
            seen[e.pid] = Episode(pid=e.pid)
            order.append(seen[e.pid])
        seen[e.pid].events.append(e)
    order.sort(key=lambda ep: ep.span[0], reverse=True)
    return order


class EventWindow:
    """Keep only the recent past — the fix for a HUD that fills up and never empties.

    The kernel rings are CUMULATIVE: every poll returns the last `GINI_RING` events *ever*, not
    the recent ones. Drawn naively the HUD accumulates forever, the time axis stretches across all
    of history, and a fresh launch is squeezed into a sliver at the right-hand edge.

    Events carry `seq` (a logical clock), not wall-clock time, so ageing needs a timestamp. We use
    FIRST OBSERVATION: an event seen for the first time at poll `t` is at most one poll interval
    older than `t`, which is ample for a window measured in seconds. Events already seen keep
    their original stamp, so nothing drifts forward as it is re-reported.

    Layout stays in seq space (exact ordering); the window only decides what is still on screen.
    """

    MAX_EVENTS = 400          # hard cap: `grind` can emit thousands a second

    def __init__(self, window_s: float = 10.0) -> None:
        self.window_s = window_s
        self._seen: dict = {}          # seq -> first-observed time
        self._events: dict = {}        # seq -> OsEvent
        # Highest seq ever admitted. Needed because the kernel ring keeps re-reporting events we
        # have already aged out: without this they look brand new on the next poll, get a fresh
        # timestamp, and never leave the screen. The clock is monotonic, so "at or below the
        # high-water mark" is a complete test for "already dealt with".
        self._hwm: int = -1

    def add(self, events: list, now: float) -> list:
        """Fold a freshly polled batch in, age out the old, return what should be drawn."""
        for e in events:
            if e.seq <= self._hwm:
                continue               # already on screen, or already retired — never re-stamp
            self._seen[e.seq] = now
            self._events[e.seq] = e
            self._hwm = e.seq
        cutoff = now - self.window_s
        for seq in [s for s, t in self._seen.items() if t < cutoff]:
            self._seen.pop(seq, None)
            self._events.pop(seq, None)
        keep = sorted(self._events)[-self.MAX_EVENTS:]
        if len(keep) < len(self._events):                  # trim the oldest beyond the cap
            for seq in [s for s in self._events if s < keep[0]]:
                self._seen.pop(seq, None)
                self._events.pop(seq, None)
        return [self._events[s] for s in keep]

    def set_window(self, window_s: float) -> None:
        self.window_s = max(1.0, float(window_s))

    def clear(self) -> None:
        self._seen.clear()
        self._events.clear()
        self._hwm = -1

    def __len__(self) -> int:
        return len(self._events)


def launch_of(events: list, pid: int) -> Episode:
    """The launch story for one pid: everything from its first `exec` onward (or the whole
    episode when the exec has already scrolled out of the ring)."""
    mine = [e for e in events if e.pid == pid]
    start = next((i for i, e in enumerate(mine) if e.kind == "exec"), 0)
    return Episode(pid=pid, events=mine[start:])
