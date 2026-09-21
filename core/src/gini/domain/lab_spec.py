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
    """
    name: str
    tree: str
    seed: str = ""
    uprog: str = ""


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
    """One thing measured while the student's code RUNS. Read by the runner, not by this module."""
    id: str
    describe: str
    metric: str
    part: str = ""
    expect: object = None
    tolerance: object = None


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
    #: part id -> what that part IS. "A: 0/6" means nothing on its own; the handout calls them
    #: Part A and Part B and the face has to use the same words, or a student is reading two
    #: different documents about one assignment.
    part_titles: dict = field(default_factory=dict)

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
                          seed=str(f.get("seed", "") or ""), uprog=str(f.get("uprog", "") or ""))
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
                            expect=g.get("expect"), tolerance=g.get("tolerance"))
                  for g in (d.get("grade") or []) if g.get("id"))
    return LabSpec(id=str(d.get("id", "")), title=str(d.get("title", "")),
                   summary=str(d.get("summary", "") or ""), hue=str(d.get("hue", "") or ""),
                   test_program=str(d.get("test_program", "") or ""),
                   released=bool(d.get("released", True)),
                   syscall_number=int(d.get("syscall_number", 0) or 0),
                   files=files, checks=checks, grade=grade,
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
