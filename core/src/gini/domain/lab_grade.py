"""Grading an A-Lab: what the student's program SAID, against what GINI independently SAW.

The wiring checklist (`lab_spec.evaluate`) answers "did you connect it up?" by pattern-matching
source. That is necessary and it is not marking: a student can pass all sixteen checks with a
`sysinfo` that returns zero. This module answers the other question — **does it tell the truth?**
— and it is the one that needs a running kernel.

The shape of every metric here is the same, and it is the whole idea: **two readings of the same
quantity, taken from two places that cannot see each other.** The student's program calls their
system call and prints a number. GINI reads the same quantity over the serial dump path, from the
kernel's own structures, with no way for the student's code to influence it. Agreement is
evidence; disagreement is the bug they need to find.

PURE, like `xv6_runner` and for the same reason: everything here is arithmetic over two
`Reading`s, so every metric is unit-testable offline with no kernel, no container and no QEMU.
Taking the readings is impure and belongs to the caller.

**A metric that could not be measured is not a failure.** If nothing was allocated between the
two readings there is nothing for `freemem` to track, and reporting that as a wrong answer would
mark a student down for a workload that did not run. Those come back `ok=None` and travel as
"not run" — the same distinction the probes draw with `pending`, and the same one the Teaching
Center's measurement section already renders.

A metric is never a mark. `describe` says what was compared and the numbers travel with it, so a
marker can see that `freemem` was out by three pages and decide what that is worth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: xv6's page size. `freemem` is reported in BYTES by the assignment's own header, and GINI counts
#: PAGES — comparing them without this is the commonest way to get a metric that never agrees.
PAGE = 4096

#: How much has to move between two readings before "freemem tracks allocation" means anything.
#: Below this the two readings are the same moment for practical purposes, and a comparison of
#: two zeroes would pass every submission including one that returns a constant.
MIN_ALLOC_PAGES = 8

#: What the seeded `sysinfotest.c` prints, mapped to the names the metrics use. Matched on the
#: LABEL rather than the line number, so a student who reorders their output, or adds a line of
#: their own, is still read correctly — the handout fixes the labels, not the order.
_LABELS = {
    "free bytes": "freemem",
    "freemem": "freemem",
    "processes": "nproc",
    "nproc": "nproc",
    "runnable": "nrunnable",
    "sleeping": "nsleeping",
    "mem in use": "memused",
    "uptime": "uptime",
    "load 5s": "load5",
    "load 15s": "load15",
    "load 30s": "load30",
}

_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ]*?)\s*:\s*([0-9]+(?:\.[0-9]+)?)")


def parse_report(lines) -> dict:
    """`["free bytes:  4096", …]` -> `{"freemem": 4096.0, …}`.

    Forgiving on purpose. The file is the student's and they will reformat it: extra spaces, a
    different order, an added line of their own, `uptime: 41 ticks` with a word after the number.
    What the handout fixes is the LABEL, so that is what this keys on; anything it does not
    recognise is ignored rather than guessed at.
    """
    out: dict = {}
    for raw in lines or []:
        m = _LINE.match(str(raw))
        if not m:
            continue
        label = " ".join(m.group(1).lower().split())
        key = _LABELS.get(label)
        if key is None:
            continue
        try:
            out[key] = float(m.group(2))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class Reading:
    """One moment, seen twice: the student's printed report and GINI's own view of the kernel.

    Both halves are optional because either can be missing for honest reasons — the program did
    not print a line, or the machine was not answering when the snapshot was taken — and a metric
    that cannot be computed says so rather than failing.
    """
    report: dict = field(default_factory=dict)   # parse_report() of their program's output
    free_pages: float | None = None              # GINI: pages on the allocator's free list
    procs: int | None = None                     # GINI: rows in the process table
    runnable: int | None = None                  # GINI: of those, RUNNABLE
    sleeping: int | None = None                  # GINI: of those, SLEEPING
    ticks: int | None = None                     # GINI: the kernel's own tick counter

    def said(self, key: str):
        return self.report.get(key)


@dataclass(frozen=True)
class GradeResult:
    """One metric, measured. `ok` is None when it could not be measured at all."""
    id: str
    part: str
    describe: str
    metric: str
    ok: bool | None
    summary: str
    detail: dict = field(default_factory=dict)

    @property
    def pending(self) -> bool:
        return self.ok is None


def _r(value) -> str:
    """A number as a marker would write it: 4096 not 4096.0, 3.06 kept."""
    if value is None:
        return "—"
    f = float(value)
    return str(int(f)) if f == int(f) else f"{f:g}"


def _missing(item, what: str) -> GradeResult:
    return GradeResult(id=item.id, part=item.part, describe=item.describe, metric=item.metric,
                       ok=None, summary=what)


def _verdict(item, ok: bool, summary: str, **detail) -> GradeResult:
    return GradeResult(id=item.id, part=item.part, describe=item.describe, metric=item.metric,
                       ok=ok, summary=summary, detail=detail)


def _tol(item, default: float = 0.0) -> float:
    try:
        return float(item.tolerance) if item.tolerance is not None else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# the metrics
#
# One function per `metric:` name in the YAML. Adding a metric to an assignment means adding one
# here; an unknown name comes back "not measured" rather than silently passing, because a metric
# nobody implemented must never read as a green tick.
# --------------------------------------------------------------------------- #

def _m_syscall_named(item, before, after, names):
    want = str(getattr(item, "expect", "") or "")
    if not names:
        return _missing(item, "no system call table was read from their syscall.h")
    got = sorted(set(names.values()))
    ok = want in got
    return _verdict(item, ok,
                    f"{want} is wired at number "
                    f"{next((n for n, v in names.items() if v == want), '?')}" if ok
                    else f"no call named {want}; this kernel has {', '.join(got) or 'none'}",
                    expected=want, found=got)


def _m_freemem_delta(item, before, after, names):
    if after is None:
        return _missing(item, "only one reading was taken, so nothing could be tracked")
    said_a, said_b = before.said("freemem"), after.said("freemem")
    if said_a is None or said_b is None:
        return _missing(item, "their program did not print a free-memory line")
    if before.free_pages is None or after.free_pages is None:
        return _missing(item, "GINI could not read the free list")
    gini = after.free_pages - before.free_pages
    if abs(gini) < MIN_ALLOC_PAGES:
        # Guarded deliberately. Two zeroes agree, and a `freemem` that returns a constant would
        # pass this metric on an idle machine — the same shape of bug as a wiring check that is
        # green before the student starts.
        return _missing(item, f"only {_r(abs(gini))} pages moved between the readings; "
                              f"nothing was allocated, so there was nothing to track")
    theirs = (said_b - said_a) / PAGE
    off = abs(theirs - gini)
    return _verdict(item, off <= _tol(item, 2),
                    f"they moved {_r(theirs)} pages, GINI saw {_r(gini)} — out by {_r(off)}",
                    student_delta_pages=theirs, gini_delta_pages=gini, off_by=off)


def _m_freemem_absolute(item, before, after, names):
    said = before.said("freemem")
    if said is None:
        return _missing(item, "their program did not print a free-memory line")
    if before.free_pages is None:
        return _missing(item, "GINI could not read the free list")
    theirs = said / PAGE
    off = abs(theirs - before.free_pages)
    return _verdict(item, off <= _tol(item, 512),
                    f"they say {_r(theirs)} pages free, GINI counts {_r(before.free_pages)} — "
                    f"out by {_r(off)}",
                    student_pages=theirs, gini_pages=before.free_pages, off_by=off)


def _m_nproc(item, before, after, names):
    said = before.said("nproc")
    if said is None:
        return _missing(item, "their program did not print a process count")
    if before.procs is None:
        return _missing(item, "GINI could not read the process table")
    off = abs(said - before.procs)
    return _verdict(item, off <= _tol(item, 0),
                    f"they say {_r(said)}, GINI reads {_r(before.procs)} from the same table",
                    student=said, gini=before.procs, off_by=off)


def _m_states(item, before, after, names):
    run, sleep = before.said("nrunnable"), before.said("nsleeping")
    if run is None and sleep is None:
        return _missing(item, "their program printed neither a runnable nor a sleeping count")
    tol = _tol(item, 1)
    parts, worst, seen = [], 0.0, False
    for label, theirs, gini in (("runnable", run, before.runnable),
                                ("sleeping", sleep, before.sleeping)):
        if theirs is None or gini is None:
            continue
        seen = True
        off = abs(theirs - gini)
        worst = max(worst, off)
        parts.append(f"{label} {_r(theirs)} vs {_r(gini)}")
    if not seen:
        return _missing(item, "GINI could not read the process states")
    return _verdict(item, worst <= tol, ", ".join(parts) + f" — worst off by {_r(worst)}",
                    off_by=worst)


def _m_uptime(item, before, after, names):
    if after is None:
        return _missing(item, "only one reading was taken, so uptime could not be seen to move")
    said_a, said_b = before.said("uptime"), after.said("uptime")
    if said_a is None or said_b is None:
        return _missing(item, "their program did not print an uptime line")
    if before.ticks is None or after.ticks is None:
        return _missing(item, "GINI could not read the kernel's tick counter")
    gini = after.ticks - before.ticks
    if gini <= 0:
        return _missing(item, "the kernel's own tick did not advance between the readings")
    theirs = said_b - said_a
    off = abs(theirs - gini)
    return _verdict(item, theirs > 0 and off <= _tol(item, 1),
                    f"they advanced {_r(theirs)} ticks, the kernel advanced {_r(gini)}"
                    if theirs > 0 else "their uptime did not move at all",
                    student_delta=theirs, gini_delta=gini, off_by=off)


def _m_load(item, before, after, names):
    if after is None:
        return _missing(item, "only one reading was taken, so the average could not be seen to "
                              "respond")
    a5, b5 = before.said("load5"), after.said("load5")
    b30 = after.said("load30")
    if a5 is None or b5 is None:
        return _missing(item, "their program did not print a 5-second load average")
    if before.runnable is not None and after.runnable is not None \
            and after.runnable <= before.runnable:
        return _missing(item, "nothing became runnable between the readings, so there was no "
                              "rise for the average to follow")
    rose = b5 > a5
    lagged = b30 is None or b30 <= b5
    note = f"5s went {_r(a5)} -> {_r(b5)}"
    if b30 is not None:
        note += f", 30s at {_r(b30)}"
    if not rose:
        note += " — it did not rise"
    elif not lagged:
        note += " — but the 30s average is above the 5s, which is the wrong way round"
    return _verdict(item, rose and lagged, note, load5_before=a5, load5_after=b5, load30=b30)


_METRICS = {
    "syscall_named": _m_syscall_named,
    "sysinfo_freemem_delta_vs_free_pages_delta": _m_freemem_delta,
    "sysinfo_freemem_vs_free_pages": _m_freemem_absolute,
    "sysinfo_nproc_vs_procs": _m_nproc,
    "sysinfo_states_vs_procs": _m_states,
    "sysinfo_uptime_advances": _m_uptime,
    "sysinfo_load_responds": _m_load,
}


def grade(spec, before: Reading, after: Reading | None = None,
          syscall_names: dict | None = None) -> tuple[GradeResult, ...]:
    """Measure every metric the assignment declares. One result each, in the spec's own order.

    `before` and `after` are two readings with some work in between — something allocating memory
    and making processes runnable — which is what the delta metrics need. With only `before`, the
    metrics that need two come back "not measured" and the rest are still measured.
    """
    out = []
    for item in getattr(spec, "grade", ()) or ():
        fn = _METRICS.get(item.metric)
        if fn is None:
            out.append(_missing(item, f"no measurement is implemented for '{item.metric}'"))
            continue
        try:
            out.append(fn(item, before, after, syscall_names or {}))
        except Exception as e:                    # noqa: BLE001 — one metric must not lose the rest
            out.append(_missing(item, f"could not be measured: {type(e).__name__}: {e}"))
    return tuple(out)


def tally(results) -> dict:
    """`{"ok": n, "failed": n, "pending": n, "total": n}` — the shape of an attempt at a glance."""
    rs = tuple(results or ())
    ok = sum(1 for r in rs if r.ok is True)
    pending = sum(1 for r in rs if r.ok is None)
    return {"ok": ok, "failed": len(rs) - ok - pending, "pending": pending, "total": len(rs)}
