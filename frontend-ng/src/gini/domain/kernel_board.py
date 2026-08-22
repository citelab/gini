"""The kernel as a map: which subsystem, how often, at what cost — and the path that skips it.

The OS HUD's X-ray answers "what happened, in what order". This answers a different question the
X-ray cannot: what is the kernel MADE of, where is the CPU, and what is expensive. A timeline and
a map, not two versions of the same thing.

Three measurements, deliberately not conflated:

    edges     EXACT count of calls crossing from one subsystem into another. FREQUENCY.
    residency Timer-sampled CPU time per subsystem. COST.
    user      Retired instructions executed with no kernel involvement at all.

Frequency and cost disagree, and the disagreement is the lesson: the block cache is asked ~600
times for ~2% of the time; the disk is asked ~12 times for ~12% of it. A view that shaded blocks
by call count would erase that, so the two are kept apart all the way to the paint call.

THE CUMULATIVE-COUNTER RULE
---------------------------
Every counter the kernel reports is cumulative since boot. A view captioned "last 10 s" that
renders a since-boot total is lying, and the Mode lane in the OS HUD did exactly that. `Window`
below differences successive samples, so nothing downstream ever sees a raw total. Counter resets
(a reboot) are detected as a decrease and start a fresh baseline rather than producing an
enormous negative rate.

Pure: text in, dataclasses out. No Qt, no Docker; unit-tested against canned dumps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Order is the kernel's GSUB_* order — index IS the wire format, so this list is load-bearing and
# must match gini_patch.py section 4h.
SUBSYSTEMS = ("user", "trap", "syscall", "proc", "memory", "file", "pipe",
              "inode", "log", "bcache", "disk", "console", "plic", "other")

SUB_USER = 0
SUB_TRAP = 1

# The three doors into the kernel, by AGENCY. A fault is not an interrupt; students conflate them
# constantly, and the kernel counts them separately for exactly that reason.
DOORS = ("asked", "couldn't", "seized")
DOOR_HELP = {
    "asked": "the program executed ecall — a system call",
    "couldn't": "the program could not proceed — page fault, illegal instruction",
    "seized": "something else wanted attention — timer or device interrupt",
}

# Which blocks sit on the kernel↔hardware boundary. Drawn outlined rather than as a separate rule,
# because these drivers ARE the boundary.
DEVICE_BLOCKS = ("disk", "console", "plic")

# Where each block's code lives, for the click-through to source. Blocks are files, which is why
# probing the module boundary gives exact per-block traffic for free.
BLOCK_FILES = {
    "trap": ("kernel/trap.c",),
    "syscall": ("kernel/syscall.c",),
    "proc": ("kernel/proc.c", "kernel/exec.c"),
    "memory": ("kernel/vm.c", "kernel/kalloc.c"),
    "file": ("kernel/file.c",),
    "pipe": ("kernel/pipe.c",),
    "inode": ("kernel/fs.c",),
    "log": ("kernel/log.c",),
    "bcache": ("kernel/bio.c",),
    "disk": ("kernel/virtio_disk.c",),
    "console": ("kernel/uart.c", "kernel/console.c"),
    "plic": ("kernel/plic.c",),
}

_BSUB = re.compile(r"^BSUB (\d+) (\S+) (\d+)\s*$", re.M)
_BEDGE = re.compile(r"^BEDGE (\d+) (\d+) (\d+)\s*$", re.M)
_BEOBS = re.compile(r"^BEOBS (\d+) (\d+) (\d+)\s*$", re.M)
_BDOOR = re.compile(r"^BDOOR (\d+) (\d+) (\d+)\s*$", re.M)
_BUSER = re.compile(r"^BUSER (\d+) (\d+)\s*$", re.M)
_BSAMP = re.compile(r"^BSAMP (\d+)\s*$", re.M)
_BTRAIL = re.compile(r"^BTRAIL (\d+)((?: \d+)*)\s*$", re.M)

# Below this many residency samples in a window, the percentages are noise and the board says so
# rather than shading a rectangle as if it knew. GINI runs the timer at ~0.5 s so the scheduler
# Gantt is watchable, which means residency accrues at only ~2 samples/second: a 1-second window
# holds two. The shading is honest only over the longer windows.
MIN_RESID_SAMPLES = 8


@dataclass(frozen=True)
class Sample:
    """One raw dump. Every number here is CUMULATIVE — never render a Sample directly."""
    resid: dict = field(default_factory=dict)        # sub -> timer ticks
    edges: dict = field(default_factory=dict)        # (src, dst) -> calls made by the workload
    edges_obs: dict = field(default_factory=dict)    # ... and calls GINI's own polling provoked
    doors: tuple = (0, 0, 0)
    user_kinstr: int = 0                             # thousands of user-mode instructions
    user_entries: int = 0
    resid_n: int = 0                                 # residency samples behind the numbers above
    trail: tuple = ()                                # recent REAL positions, oldest first

    @property
    def ok(self) -> bool:
        """Did this dump come from a kernel that has the board built in?

        An older image answers /board with nothing at all, and the honest response is an empty
        view with a rebuild hint — not a board of zeros that looks like a very quiet machine.
        """
        return bool(self.resid)


def _edges(text: str, rx) -> dict:
    out = {}
    for m in rx.finditer(text or ""):
        a, b = int(m.group(1)), int(m.group(2))
        if a < len(SUBSYSTEMS) and b < len(SUBSYSTEMS):
            out[(SUBSYSTEMS[a], SUBSYSTEMS[b])] = int(m.group(3))
    return out


def parse(text: str) -> Sample:
    """Parse a `gini_boarddump()` dump. Unknown lines are ignored, so the dump can grow."""
    resid = {}
    for m in _BSUB.finditer(text or ""):
        i = int(m.group(1))
        if i < len(SUBSYSTEMS):
            resid[SUBSYSTEMS[i]] = int(m.group(3))
    d = _BDOOR.search(text or "")
    doors = (int(d.group(1)), int(d.group(2)), int(d.group(3))) if d else (0, 0, 0)
    u = _BUSER.search(text or "")
    n = _BSAMP.search(text or "")
    tr = _BTRAIL.search(text or "")
    trail = ()
    if tr:
        trail = tuple(SUBSYSTEMS[int(v)] for v in tr.group(2).split()
                      if v.isdigit() and int(v) < len(SUBSYSTEMS))
    return Sample(resid=resid,
                  edges=_edges(text, _BEDGE), edges_obs=_edges(text, _BEOBS),
                  doors=doors,
                  user_kinstr=int(u.group(1)) if u else 0,
                  user_entries=int(u.group(2)) if u else 0,
                  resid_n=int(n.group(1)) if n else 0,
                  trail=trail)


@dataclass
class Frame:
    """What the HUD draws: rates over one window, never totals."""
    blocks: dict = field(default_factory=dict)       # name -> calls received in the window
    blocks_obs: dict = field(default_factory=dict)   # ... of which, provoked by GINI observing
    resid: dict = field(default_factory=dict)        # name -> timer ticks in the window
    edges: dict = field(default_factory=dict)        # (src, dst) -> calls in the window
    edges_obs: dict = field(default_factory=dict)    # (src, dst) -> OUR calls in the window
    doors: tuple = (0, 0, 0)
    user_kinstr: int = 0
    user_entries: int = 0
    resid_n: int = 0
    trail: tuple = ()
    span_s: float = 0.0

    @property
    def total_resid(self) -> int:
        return sum(self.resid.values())

    @property
    def resid_trustworthy(self) -> bool:
        """Enough residency samples for the percentages to mean anything?

        At ~2 samples/second a short window holds almost none, and a rectangle shaded from two
        observations asserts a precision that does not exist. The board asks this before it
        shades, and says "sampling" when the answer is no.
        """
        return self.total_resid >= MIN_RESID_SAMPLES

    def share(self, name: str) -> float:
        """Fraction of sampled CPU time spent in this block, 0..1 — what shades the rectangle."""
        tot = self.total_resid
        return (self.resid.get(name, 0) / tot) if tot else 0.0

    def ours(self, name: str) -> int:
        """Calls into this block that GINI's own polling caused. Never zero for console and plic
        on an idle machine — the Lab is talking to the serial port several times a second."""
        return self.blocks_obs.get(name, 0)

    @property
    def here(self) -> str:
        """Where the CPU most recently actually WAS — the newest real sample, not an aggregate.

        This is what the marker draws. Earlier it drew `busiest`, the modal block over the whole
        window, under a label reading "hart 0 is here" — which overclaimed. One real observation
        with an honest label beats a synthesised position with a confident one.
        """
        return self.trail[-1] if self.trail else ""

    @property
    def kernel_entries(self) -> int:
        return sum(self.doors)

    @property
    def instr_per_entry(self) -> float:
        """The headline: user instructions executed per entry into the kernel.

        The single number that characterises a workload — a CPU-bound program runs millions
        between entries, a console-bound one a few hundred. Returns 0.0 when the window has no
        entries, rather than dividing by zero and inventing infinity.
        """
        return (self.user_kinstr * 1000.0 / self.user_entries) if self.user_entries else 0.0

    @property
    def busiest(self) -> str:
        """Block holding the most CPU time — not the most calls. The two differ, on purpose."""
        return max(self.resid, key=lambda k: self.resid[k], default="")

    def hottest_edge(self):
        return max(self.edges.items(), key=lambda kv: kv[1], default=((None, None), 0))


def _delta(now: dict, prev: dict) -> dict:
    """Difference two cumulative maps, dropping zero deltas so the frame stays sparse.

    A key that DECREASED means the kernel rebooted and its counters restarted. Treating that as a
    negative rate would paint nonsense, so the new value is taken as-is: the first window after a
    reboot is slightly overstated, which is honest and self-correcting on the next poll.
    """
    out = {}
    for k, v in now.items():
        p = prev.get(k, 0)
        d = v - p if v >= p else v
        if d:
            out[k] = d
    return out


class Window:
    """Successive cumulative samples in, per-window rates out.

    This class exists because the kernel can only report totals, and every view is captioned with
    a time window. It is the one place that conversion happens.
    """

    def __init__(self) -> None:
        self._prev: Sample | None = None
        self._prev_t: float = 0.0

    @property
    def has_baseline(self) -> bool:
        """True once a first sample has landed, so the next frame will be a real difference."""
        return self._prev is not None

    @property
    def board_supported(self) -> bool:
        """False when the running kernel answers /board with nothing — an image built before the
        board existed. The view says so and offers a rebuild rather than drawing zeros, which
        would look exactly like a very quiet machine and send a student hunting a ghost."""
        return self._prev.ok if self._prev is not None else True

    def add(self, s: Sample, now: float) -> Frame:
        prev, prev_t = self._prev, self._prev_t
        self._prev, self._prev_t = s, now
        if prev is None or not s.ok:
            # First sample establishes the baseline. Rendering it as a frame would show
            # since-boot totals under a "last 10 s" caption, which is the exact lie this class
            # exists to prevent.
            return Frame(span_s=0.0)

        edges = _delta(s.edges, prev.edges)
        edges_obs = _delta(s.edges_obs, prev.edges_obs)

        def by_block(e):
            out = {}
            for (_src, dst), n in e.items():
                if dst != "user":                    # "user" is not a block you can call into
                    out[dst] = out.get(dst, 0) + n
            return out

        doors = tuple(
            (b - a) if b >= a else b
            for a, b in zip(prev.doors, s.doors)
        )
        uk = s.user_kinstr - prev.user_kinstr
        ue = s.user_entries - prev.user_entries
        rn = s.resid_n - prev.resid_n
        return Frame(
            blocks=by_block(edges),
            blocks_obs=by_block(edges_obs),
            resid=_delta(s.resid, prev.resid),
            edges=edges,
            edges_obs=edges_obs,
            doors=doors if len(doors) == 3 else (0, 0, 0),
            user_kinstr=uk if uk >= 0 else s.user_kinstr,
            user_entries=ue if ue >= 0 else s.user_entries,
            resid_n=rn if rn >= 0 else s.resid_n,
            # The trail is a ring of recent positions, not a counter — it is carried through
            # as-is rather than differenced. Differencing it would be meaningless.
            trail=s.trail,
            span_s=max(0.0, now - prev_t),
        )

    def reset(self) -> None:
        self._prev = None
        self._prev_t = 0.0


def signature(f: Frame) -> int:
    """A cheap change-signature for HudHistory: a quiet machine records one snapshot, and every
    retained snapshot marks a real change (which is what the timeline's ticks mean)."""
    return (sum(f.blocks.values()) * 1000003
            + sum(f.resid.values()) * 10007
            + f.kernel_entries * 101
            + f.user_entries)
