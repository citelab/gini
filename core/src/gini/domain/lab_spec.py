"""A kernel assignment, as data.

The xv6 labs all have the same shape: a handful of kernel files become the student's, they edit
them, GINI rebuilds the kernel, and something is checked. What differs between assignments is
*which* files, *what* to look for, and *what* to measure — so those are data, and this module is
the reader.

The point is that a new assignment is a YAML file rather than a code change. It also stops the
handout, the progress tracker and the grader being three descriptions of one assignment that can
drift apart: `where` and `hint` are the words the handout uses, written once.

**A check is not a grade, and the distinction is load-bearing.** Every check here is a pattern
over the student's own source, so it answers "have you written the line" and never "does it
work" — a line that is present but wrong passes. That is on purpose: the tracker exists so a
student can see what the assignment requires and how far they have got, which is a different job
from marking. What is actually graded is measured while their code runs, and lives under `grade`.

Pure, like the rest of `gini.domain`: `evaluate` takes a callable that returns file text, so the
filesystem, the container and the mount are all somebody else's problem.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LABS_DIR = Path(__file__).parent / "labs"

# Comment syntax per file kind. C files must NOT have `#` lines stripped — `#define SYS_sysinfo`
# is the single most important line in a syscall lab, and it starts with a hash.
_C_LIKE = (".c", ".h")
_HASH_LIKE = (".pl", ".pm", ".sh", ".mk")


@dataclass(frozen=True)
class LabFile:
    """One file the student owns.

    `name` is what it is called in their lab folder; `tree` is where GINI links it into the kernel
    source. `seed` is the text to write when the folder is empty — used for files xv6 does not
    have (`kernel/sysinfo.h`) and for starter programs. Files with no seed are copied from the
    image's pristine copy instead. `uprog` registers a user program in the build, so the student
    never edits the Makefile.

    `part` is the part of the handout that first sends the student to this file, so User Code can
    list the files the way the handout introduces them; `also` names the later parts that come
    back to it ("proc.c — Part A, also Part C"). Both optional: a lab that leaves them out gets one
    flat list, as A-Lab 01 always has.

    `readonly` is a file the student is SHOWN and never builds: A-Lab 03's test programs, which the
    handout promises are "GINI's own copies, so the results are always about your kernel". It is
    listed, copied into their folder so they can read it in their own editor, and never linked into
    the tree, so editing it changes nothing. It still counts among the lab's files.
    """
    name: str
    tree: str
    seed: str = ""
    uprog: str = ""
    part: str = ""
    also: str = ""
    readonly: bool = False


@dataclass(frozen=True)
class Check:
    """One line in the progress tracker.

    `kind` is how it is decided:

    * `match` — a regular expression over the student's file. Most checks.
    * `edited` — the file differs from the image's copy. For work whose SHAPE is the student's
      own: "add a function that counts the free list" cannot be a pattern, because every useful
      pattern for it (`kmem.freelist`, `acquire(&kmem.lock)`) already appears in the untouched
      `kalloc.c` — `kalloc` and `kfree` both use them. A check that is green before the student
      starts is worse than no check, so that one asks a different question.
    """
    id: str
    label: str
    file: str
    match: str = ""
    kind: str = "match"
    part: str = ""
    where: str = ""          # the path as the handout names it, e.g. "kernel/syscall.h"
    hint: str = ""


@dataclass(frozen=True)
class GradeItem:
    """One thing measured while the student's code RUNS. Read by the runner, not by this module.

    `run` names the workload (a `LabRun`) whose readings the metric compares; empty means the
    before/after readings A-Lab 01's metrics use. A metric that compares two runs names both,
    space-separated."""
    id: str
    describe: str
    metric: str
    part: str = ""
    expect: object = None
    tolerance: object = None
    run: str = ""


@dataclass(frozen=True)
class LabRun:
    """One workload the grading run performs: switch to `policy` (when set), run `command` in the
    foreground, and read GINI's process table `sample_at` seconds in, while it is still running.

    Why a workload is declared and not coded: A-Lab 01's grading stirs memory with `alloc` between
    two readings, and that is right for sysinfo and meaningless for a scheduler. A scheduling lab
    has to run the same program under two policies and once more with a sleeper, and what it
    measures -- each child's tickets and run time -- only exists while the program is running."""
    name: str
    command: str
    policy: int | None = None
    sample_at: float = 25.0
    timeout: float = 75.0


@dataclass(frozen=True)
class Stage:
    """One staged check: run `command` at the shell and decide pass or fail from what it printed.

    Simpler and stricter than a LabRun's measurement, and meant to be: a stage is one command and
    one verdict, and stages run in order, each only once everything it `needs` has passed. A
    broken allocator can take the kernel down, and every stage after a crash would be noise, so a
    stage whose prerequisite failed is not run at all. The rules themselves are in
    `domain.lab_stages`; this is only the data.

    `{seed}` in the command is replaced with the check's seed, so a student can repeat any run by
    typing it. `expect` and `reject` say what the output must and must not hold; left empty they
    are the usual rule, an exact `<key>: ok` line and no line containing `: FAIL`.
    """
    id: str
    command: str
    part: str = ""
    key: str = ""
    needs: tuple[str, ...] = ()
    label: str = ""
    timeout: float = 60.0
    expect: tuple[str, ...] = ()       # lines that must appear, exactly (after stripping)
    reject: tuple[str, ...] = ()       # text that must appear in no line

    @property
    def expected(self) -> tuple[str, ...]:
        return self.expect or ((f"{self.key}: ok",) if self.key else ())

    @property
    def rejected(self) -> tuple[str, ...]:
        return self.reject or ((": FAIL",) if self.key else ())


@dataclass(frozen=True)
class LabSpec:
    id: str
    title: str
    #: One line for the hub tile — what this assignment IS, in the words a student would use
    #: before they have read the handout. The title is a name; this is the sentence under it.
    summary: str = ""
    #: The program a student runs to demonstrate the assignment works, and the one whose output
    #: is captured into the proof. Left empty it is the assignment's first user program, which is
    #: what a single-program lab wants; name it when a lab ships more than one.
    test_program: str = ""
    #: How long the test may run before its output is taken as it stands. A-Lab 01's sysinfotest
    #: prints and exits; A-Lab 02's sharetest measures for 30 seconds.
    test_timeout: float = 12.0
    #: Whether students can arm it yet. A course ships the whole term's tiles from the start so
    #: the shape of it is visible, but an assignment with no files and no checks would arm to an
    #: empty panel and look broken rather than unwritten — so an unreleased one is shown and
    #: refused. Set it true (or drop the line) when the real content lands.
    released: bool = True
    #: The accent this assignment wears in the hub. Optional: left empty, the hub assigns one
    #: from its own palette by position, which is enough while assignments are added at the end.
    #: Pin it here if a lab should keep its colour when another is added before it alphabetically.
    hue: str = ""
    syscall_number: int = 0
    files: tuple[LabFile, ...] = ()
    checks: tuple[Check, ...] = ()
    grade: tuple[GradeItem, ...] = ()
    #: The workloads the grading run performs, in order (see LabRun). Empty for a lab whose
    #: metrics only need A-Lab 01's two readings around a stir.
    runs: tuple[LabRun, ...] = ()
    #: part id -> what that part IS. "A: 0/6" means nothing on its own; the handout calls them
    #: Part A and Part B and the face has to use the same words, or a student is reading two
    #: different documents about one assignment.
    part_titles: dict = field(default_factory=dict)
    #: Staged checks (see Stage), in order. A lab that has them is checked by running them — Check
    #: Part A/B/C and Check all — instead of by `runs` and `grade`.
    stages: tuple[Stage, ...] = ()

    def part_title(self, part: str) -> str:
        return str(self.part_titles.get(part, "") or "")

    def file(self, name: str) -> LabFile | None:
        return next((f for f in self.files if f.name == name), None)

    def parts(self) -> tuple[str, ...]:
        """The parts, in the order they first appear — an assignment decides its own naming."""
        seen: list[str] = []
        for c in self.checks:
            if c.part and c.part not in seen:
                seen.append(c.part)
        return tuple(seen)

    def run(self, name: str) -> LabRun | None:
        return next((r for r in self.runs if r.name == name), None)

    def stage(self, sid: str) -> Stage | None:
        return next((s for s in self.stages if s.id == sid), None)

    def stage_parts(self) -> tuple[str, ...]:
        """The parts that have stages, in order — one Check button each."""
        seen: list[str] = []
        for s in self.stages:
            if s.part and s.part not in seen:
                seen.append(s.part)
        return tuple(seen)

    def files_by_part(self) -> tuple[tuple[str, tuple[LabFile, ...]], ...]:
        """The files grouped the way the handout introduces them: [(part, files)], parts in the
        order their files first appear, then any file with no part under "". One group, "", for a
        lab that gives its files no parts at all."""
        order: list[str] = []
        for f in self.files:
            if f.part not in order:
                order.append(f.part)
        if "" in order:
            order.remove("")
            order.append("")
        return tuple((p, tuple(f for f in self.files if f.part == p)) for p in order)

    def uprogs(self) -> tuple[str, ...]:
        return tuple(f.uprog for f in self.files if f.uprog)

    @property
    def test_prog(self) -> str:
        """The program the Run test button launches, or "" if the lab has none."""
        if self.test_program:
            return self.test_program
        progs = self.uprogs()
        return progs[0] if progs else ""


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def from_dict(d: dict) -> LabSpec:
    d = d or {}
    files = tuple(LabFile(name=str(f.get("name", "")), tree=str(f.get("tree", "")),
                          seed=str(f.get("seed", "") or ""), uprog=str(f.get("uprog", "") or ""),
                          part=str(f.get("part", "") or ""), also=str(f.get("also", "") or ""),
                          readonly=bool(f.get("readonly", False)))
                  for f in (d.get("files") or []) if f.get("name") and f.get("tree"))
    checks = tuple(Check(id=str(c.get("id", "")), label=str(c.get("label", "")),
                         file=str(c.get("file", "")), match=str(c.get("match", "") or ""),
                         kind=str(c.get("kind", "") or "match"),
                         part=str(c.get("part", "") or ""), where=str(c.get("where", "") or ""),
                         hint=str(c.get("hint", "") or ""))
                   for c in (d.get("checks") or [])
                   if c.get("id") and (c.get("match") or c.get("kind") == "edited"))
    grade = tuple(GradeItem(id=str(g.get("id", "")), describe=str(g.get("describe", "") or ""),
                            metric=str(g.get("metric", "") or ""),
                            part=str(g.get("part", "") or ""),
                            expect=g.get("expect"), tolerance=g.get("tolerance"),
                            run=str(g.get("run", "") or ""))
                  for g in (d.get("grade") or []) if g.get("id"))
    runs = []
    for name, r in (d.get("runs") or {}).items():
        r = r or {}
        if not r.get("command"):
            continue
        pol = r.get("policy")
        runs.append(LabRun(name=str(name), command=str(r["command"]),
                           policy=None if pol is None else int(pol),
                           sample_at=float(r.get("sample_at", 25.0)),
                           timeout=float(r.get("timeout", 75.0))))
    stages = tuple(Stage(id=str(s.get("id", "")), command=str(s.get("command", "")),
                         part=str(s.get("part", "") or ""), key=str(s.get("key", "") or ""),
                         needs=tuple(str(n) for n in (s.get("needs") or [])),
                         label=str(s.get("label", "") or ""),
                         timeout=float(s.get("timeout", 60.0) or 60.0),
                         expect=tuple(str(x) for x in (s.get("expect") or [])),
                         reject=tuple(str(x) for x in (s.get("reject") or [])))
                   for s in (d.get("stages") or []) if s.get("id") not in (None, "")
                   and s.get("command"))
    return LabSpec(id=str(d.get("id", "")), title=str(d.get("title", "")),
                   summary=str(d.get("summary", "") or ""), hue=str(d.get("hue", "") or ""),
                   test_program=str(d.get("test_program", "") or ""),
                   test_timeout=float(d.get("test_timeout", 12.0) or 12.0),
                   released=bool(d.get("released", True)),
                   syscall_number=int(d.get("syscall_number", 0) or 0),
                   files=files, checks=checks, grade=grade, runs=tuple(runs), stages=stages,
                   part_titles={str(k): str(v) for k, v in (d.get("parts") or {}).items()})


def from_yaml(text: str) -> LabSpec:
    return from_dict(yaml.safe_load(text) or {})


def load(path) -> LabSpec:
    return from_yaml(Path(path).read_text(encoding="utf-8"))


def catalog() -> tuple[LabSpec, ...]:
    """Every assignment shipped with gini-core, in the order a course runs them.

    Sorted by FILENAME, which is why they carry a numeric prefix: the id is a slug about the
    topic (`syscall-sysinfo`) and sorting on it would put A-Lab 03 before A-Lab 01 the moment a
    scheduler lab shipped. The hub hands hues out by position, so the order is visible.
    """
    if not LABS_DIR.is_dir():
        return ()
    out = []
    for p in sorted(LABS_DIR.glob("*.yaml")):
        try:
            out.append(load(p))
        except Exception:      # noqa: BLE001 — one malformed pack must not hide the rest
            continue
    return tuple(out)


def get(spec_id: str) -> LabSpec | None:
    return next((s for s in catalog() if s.id == spec_id), None)


# --------------------------------------------------------------------------- #
# evaluating
# --------------------------------------------------------------------------- #
def strip_comments(text: str, filename: str = "") -> str:
    """Remove comments, so commenting a line out does not tick its box.

    Deliberately naive: it does not know about string literals, so `"// not a comment"` would be
    cut. That is acceptable here — these patterns look for declarations and table rows, not for
    text inside strings — and the alternative is a C parser to answer a question the compiler
    answers properly a second later.
    """
    ext = Path(filename).suffix.lower()
    if ext in _HASH_LIKE:
        return re.sub(r"#.*", "", text or "")
    if ext in _C_LIKE or not ext:
        out = re.sub(r"/\*.*?\*/", " ", text or "", flags=re.S)
        return re.sub(r"//.*", "", out)
    return text or ""


@dataclass(frozen=True)
class CheckResult:
    check: Check
    passed: bool
    missing_file: bool = False


def evaluate(spec: LabSpec, read, pristine=None) -> tuple[CheckResult, ...]:
    """Run every check.

    `read(name)` returns the text of one of the student's files, or None. `pristine(name)` returns
    the image's untouched copy, and is only needed by `kind: edited` checks — without it those
    report not-done rather than guessing, because a green tick for work nobody has started is the
    one wrong answer here.

    A file that cannot be read fails its checks rather than raising: before the folder is seeded,
    or with the machine down, "not done yet" is honest, and an exception would take the face down.
    """
    out = []
    cache: dict[str, str | None] = {}
    orig: dict[str, str | None] = {}

    def _get(store, fn, name):
        if name not in store:
            try:
                store[name] = fn(name) if fn else None
            except Exception:                      # noqa: BLE001
                store[name] = None
        return store[name]

    for c in spec.checks:
        text = _get(cache, read, c.file)
        if text is None:
            out.append(CheckResult(c, False, missing_file=True))
            continue
        if c.kind == "edited":
            was = _get(orig, pristine, c.file)
            out.append(CheckResult(c, was is not None and text != was))
            continue
        try:
            ok = re.search(c.match, strip_comments(text, c.file)) is not None
        except re.error:                           # a bad pattern in a pack is not a crash
            ok = False
        out.append(CheckResult(c, ok))
    return tuple(out)


def progress(results) -> dict:
    """`{"passed": n, "total": n, "parts": {"A": (passed, total), …}}` for the tracker."""
    rs = tuple(results)
    parts: dict[str, list[int]] = {}
    for r in rs:
        p = r.check.part or ""
        slot = parts.setdefault(p, [0, 0])
        slot[1] += 1
        if r.passed:
            slot[0] += 1
    return {"passed": sum(1 for r in rs if r.passed), "total": len(rs),
            "parts": {k: (v[0], v[1]) for k, v in parts.items()}}


def next_step(results) -> Check | None:
    """The first check not yet passed — what the face nudges toward. None when all are done."""
    return next((r.check for r in results if not r.passed), None)
