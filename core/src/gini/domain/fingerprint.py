"""Process fingerprints — a behavioral signature per process, from REAL kernel telemetry.

A fingerprint is a small feature vector over five axes, each derived from data the Machine Lab
already collects (no simulation, no LLM judgement):

  cpu      — fraction of samples the proc wanted the CPU (RUNNING/RUNNABLE), from procdump
  io_wait  — fraction of samples SLEEPING (blocked on IO / wait / pipe), from procdump
  syscalls — syscall rate, from the syscall trace ring (per-pid TRACE entries)
  faults   — page-fault rate, from the trap-taxonomy ring (per-pid pagefault traps)
  forks    — fork() rate, from the syscall ring (num == SYS_fork)

Two layers sit on top, both pure/deterministic and testable:

  • classify(fp)          — a threshold rule mapping a fingerprint to a behavior CLASS. This is the
                            baseline the "classify game" grades a student against (or that a student
                            tunes as an assignment).
  • confusion_matrix(...) — scores predicted-vs-true classes. GROUND_TRUTH is the oracle's knowledge
                            of what each shipped program really is; it grades, it never classifies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SYS_FORK = 1            # xv6 syscall number for fork()
TRAP_PAGEFAULT = 1      # trap-taxonomy kind index for a page fault

# the fingerprint axes, in radar order
FEATURE_AXES = ["cpu", "syscalls", "io_wait", "faults", "forks"]
AXIS_LABEL = {"cpu": "CPU", "syscalls": "syscalls", "io_wait": "IO wait",
              "faults": "faults", "forks": "forks"}

# behavior classes a fingerprint can be sorted into
CLASSES = ["cpu-bound", "io-bound", "memory", "fork-heavy", "mixed"]

# the oracle's ground truth — what each launchable program ACTUALLY is. Used only to grade the
# classify game; never shown to the classifier. (grind forks + does file/pipe work, so it can
# honestly be confused with io-bound — that overlap is the lesson.)
GROUND_TRUTH = {
    "spin": "cpu-bound", "busy": "cpu-bound",
    "alloc": "memory", "writer": "io-bound",
    "forktest": "fork-heavy", "grind": "fork-heavy",
}

# saturation constants: rate at which each axis reaches ~0.5 (via x/(x+k)). Tuned so the shipped
# programs land in distinct regions; documented so a student can reason about them.
_K_SYS, _K_FLT, _K_FORK = 20.0, 5.0, 2.0

# default classifier thresholds — a student assignment can tune these and watch the confusion move.
DEFAULT_THRESHOLDS = {"fork": 0.4, "flt": 0.3, "io": 0.35, "sys": 0.4, "cpu": 0.5}


@dataclass
class ProcFeatures:
    """Raw per-process tallies accumulated over a window. All fields come from real dumps."""
    pid: int
    name: str = ""
    samples: int = 0          # procdump samples observed
    run_samples: int = 0      # ...RUNNING or RUNNABLE (wanted the CPU)
    sleep_samples: int = 0    # ...SLEEPING (blocked)
    syscalls: int = 0         # syscall trace events attributed to this pid
    forks: int = 0            # ...of which fork()
    faults: int = 0           # page-fault traps
    window: float = 1.0       # seconds the tallies span (for rates)


def _sat(x: float, k: float) -> float:
    """Saturating normalizer x/(x+k) -> 0..1: bounded and stable frame to frame."""
    return x / (x + k) if x > 0 else 0.0


def fingerprint(f: ProcFeatures) -> dict:
    """A ProcFeatures -> the 0..1 feature vector (the fingerprint)."""
    w = max(f.window, 1e-6)
    s = max(f.samples, 1)
    return {
        "cpu": min(1.0, f.run_samples / s),
        "io_wait": min(1.0, f.sleep_samples / s),
        "syscalls": _sat(f.syscalls / w, _K_SYS),
        "faults": _sat(f.faults / w, _K_FLT),
        "forks": _sat(f.forks / w, _K_FORK),
    }


def classify(fp: dict, thresholds: dict | None = None) -> str:
    """Map a fingerprint to a behavior class by a simple, inspectable rule. Order matters: the most
    distinctive signal wins first. Thresholds are tunable (the assignment)."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if fp["forks"] >= th["fork"]:
        return "fork-heavy"
    if fp["faults"] >= th["flt"]:
        return "memory"
    if fp["io_wait"] >= th["io"] or (fp["syscalls"] >= th["sys"] and fp["cpu"] < th["cpu"]):
        return "io-bound"
    if fp["cpu"] >= th["cpu"]:
        return "cpu-bound"
    return "mixed"


def similarity(a: dict, b: dict) -> float:
    """Cosine similarity of two fingerprints (0..1) — 'who behaves like whom' for the board."""
    dot = sum(a[k] * b[k] for k in FEATURE_AXES)
    na = math.sqrt(sum(a[k] ** 2 for k in FEATURE_AXES))
    nb = math.sqrt(sum(b[k] ** 2 for k in FEATURE_AXES))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def scatter_xy(fp: dict) -> tuple[float, float]:
    """Project a fingerprint onto the behavior map: x = CPU-bound(1) <-> IO-bound(0),
    y = compute(0) <-> heavy-kernel(1, syscalls+faults+forks). Both 0..1."""
    x = max(0.0, min(1.0, 0.5 + 0.5 * (fp["cpu"] - fp["io_wait"])))
    y = max(0.0, min(1.0, (fp["syscalls"] + fp["faults"] + fp["forks"]) / 3.0))
    return x, y


def true_class(name: str) -> str | None:
    """The oracle's ground-truth class for a program name (None if unknown)."""
    return GROUND_TRUTH.get((name or "").strip())


def confusion_matrix(pairs, classes=None) -> dict:
    """[(true_class, predicted_class), …] -> {(true, pred): count}. Off-diagonal = confusion."""
    classes = classes or CLASSES
    m = {(t, p): 0 for t in classes for p in classes}
    for true, pred in pairs:
        if (true, pred) in m:
            m[(true, pred)] += 1
    return m


def accuracy(pairs) -> float:
    """Fraction of (true, pred) pairs on the diagonal."""
    pairs = list(pairs)
    if not pairs:
        return 0.0
    return sum(1 for t, p in pairs if t == p) / len(pairs)


class FingerprintAccumulator:
    """Builds per-pid ProcFeatures from a stream of observations. Pure bookkeeping — the UI feeds it
    procdump states each poll plus the NEW syscall/trap ring events since the last poll."""

    def __init__(self) -> None:
        self.feats: dict[int, ProcFeatures] = {}

    def reset(self) -> None:
        self.feats.clear()

    def _get(self, pid: int, name: str = "") -> ProcFeatures:
        f = self.feats.get(pid)
        if f is None:
            f = ProcFeatures(pid=pid, name=name)
            self.feats[pid] = f
        if name:
            f.name = name
        return f

    def observe(self, procs, sc_events=(), trap_events=(), dt: float = 0.5) -> None:
        """One poll: `procs` are the current [Proc]; `sc_events`/`trap_events` are the NEW ring
        entries since the last poll (SyscallEvent / TrapEvent). `dt` advances every proc's window."""
        for p in procs:
            f = self._get(p.pid, p.name)
            f.samples += 1
            if p.state in ("running", "runnable"):
                f.run_samples += 1
            elif p.state == "sleeping":
                f.sleep_samples += 1
            f.window += dt
        for e in sc_events:
            f = self.feats.get(e.pid)
            if f is not None:
                f.syscalls += 1
                if e.num == SYS_FORK:
                    f.forks += 1
        for e in trap_events:
            f = self.feats.get(e.pid)
            if f is not None and e.kind == TRAP_PAGEFAULT:
                f.faults += 1

    def fingerprints(self, min_samples: int = 3) -> dict:
        """{pid: fingerprint} for procs with enough samples to be meaningful (else 'settling')."""
        return {pid: fingerprint(f) for pid, f in self.feats.items() if f.samples >= min_samples}


def demo_features() -> list:
    """Canned but realistic fingerprints for the shipped programs, so the panel + game work offline
    (and drive the pure tests). Tallies chosen to match GROUND_TRUTH under the default classifier."""
    return [
        ProcFeatures(pid=4, name="spin", samples=20, run_samples=20, sleep_samples=0,
                     syscalls=1, forks=0, faults=0, window=10),
        ProcFeatures(pid=5, name="busy", samples=20, run_samples=20, sleep_samples=0,
                     syscalls=2, forks=0, faults=0, window=10),
        ProcFeatures(pid=6, name="alloc", samples=20, run_samples=14, sleep_samples=6,
                     syscalls=50, forks=0, faults=48, window=10),
        ProcFeatures(pid=7, name="writer", samples=20, run_samples=6, sleep_samples=14,
                     syscalls=200, forks=0, faults=0, window=10),
        ProcFeatures(pid=8, name="grind", samples=20, run_samples=10, sleep_samples=10,
                     syscalls=180, forks=40, faults=5, window=10),
        ProcFeatures(pid=9, name="forktest", samples=20, run_samples=12, sleep_samples=8,
                     syscalls=60, forks=50, faults=0, window=10),
    ]
