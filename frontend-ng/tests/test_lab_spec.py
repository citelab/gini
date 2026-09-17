"""An assignment is data (`gini.domain.lab_spec`), and the tracker is not the grade.

Every check in a lab spec is a pattern over the student's own source, so it answers "have you
written the line" and never "does it work". These tests pin that boundary, the comment handling
that stops a commented-out line ticking its box, and — most usefully — that the shipped
`syscall-sysinfo` pack actually matches a correct solution and fails on realistic mistakes.
"""
from __future__ import annotations

import pytest

from gini.domain import lab_spec as L

# A solution that is right. Deliberately not minimal: it carries the comments, whitespace and
# spelling a student would really write, because the patterns have to survive those.
GOOD = {
    "syscall.h": "#define SYS_close  21\n#define SYS_sync   22\n#define SYS_sysinfo 23\n",
    "syscall.c": ("extern uint64 sys_sync(void);\n"
                  "extern uint64 sys_sysinfo(void);\n"
                  "static uint64 (*syscalls[])(void) = {\n"
                  "  [SYS_sync]    = sys_sync,\n"
                  "  [SYS_sysinfo] sys_sysinfo,\n};\n"),
    "sysproc.c": ("extern struct proc proc[NPROC];\n"
                  "uint64\nsys_sysinfo(void)\n{\n"
                  "  struct sysinfo info;\n"
                  "  uint64 addr;\n  argaddr(0, &addr);\n"
                  "  info.freemem = freemem();\n"
                  "  if (copyout(p->pagetable, p->sz, addr, (char *)&info, sizeof(info)) < 0)\n"
                  "    return -1;\n  return 0;\n}\n"),
    "user.h": "int sync(void);\nint sysinfo(struct sysinfo *info);\n",
    "usys.pl": 'entry("sync");\nentry("sysinfo");\n',
    "defs.h": "// kalloc.c\nvoid* kalloc(void);\nuint64 freemem(void);\n",
    "kalloc.c": ("uint64\nfreemem(void)\n{\n  acquire(&kmem.lock);\n"
                 "  struct run *r = kmem.freelist;\n  release(&kmem.lock);\n  return 0;\n}\n"),
    "sysinfo.h": "struct sysinfo { uint64 freemem; uint64 nproc; };\n",
    "sysinfotest.c": '#include "user/user.h"\nint main(void){ return 0; }\n',
}


@pytest.fixture(scope="module")
def spec():
    s = L.get("syscall-sysinfo")
    assert s is not None, "the shipped assignment pack did not load"
    return s


def _read(files):
    return lambda name: files.get(name)


def _ev(spec, files):
    """Evaluate against PRISTINE, so `kind: edited` checks can be decided the way the face does."""
    return L.evaluate(spec, _read(files), pristine=_read(PRISTINE))


def test_the_shipped_assignment_loads_with_everything_it_needs(spec):
    assert spec.parts() == ("A", "B")
    assert spec.uprogs() == ("sysinfotest",)
    assert spec.file("kalloc.c") is not None, "part B cannot be done without it"
    assert spec.file("proc.c") is None, "proc[] is reachable with an extern; proc.c stays the image's"
    # the two files xv6 does not have, or that start the student off
    assert {f.name for f in spec.files if f.seed} == {"sysinfo.h", "sysinfotest.c"}


def test_a_correct_solution_passes_every_check(spec):
    res = _ev(spec, GOOD)
    failed = [r.check.id for r in res if not r.passed]
    assert failed == [], f"a correct solution failed: {failed}"
    assert L.progress(res)["parts"] == {"A": (6, 6), "B": (4, 4)}


def test_the_commonest_mistake_fails_exactly_one_check(spec):
    """Forgetting entry() in usys.pl — nothing fails until the linker."""
    files = dict(GOOD, **{"usys.pl": 'entry("sync");\n'})
    res = _ev(spec, files)
    assert [r.check.id for r in res if not r.passed] == ["usys"]
    assert L.next_step(res).id == "usys"
    assert "LINKER" in L.next_step(res).hint


def test_a_missing_dispatch_row_is_distinguishable_from_a_missing_define(spec):
    """These two produce completely different failures — one at compile, one at runtime."""
    no_row = dict(GOOD, **{"syscall.c": "extern uint64 sys_sysinfo(void);\n"})
    assert [r.check.id for r in _ev(spec, no_row) if not r.passed] == ["dispatch"]
    no_def = dict(GOOD, **{"syscall.h": "#define SYS_sync 22\n"})
    assert [r.check.id for r in _ev(spec, no_def) if not r.passed] == ["number"]


def test_commenting_a_line_out_does_not_tick_its_box(spec):
    files = dict(GOOD, **{"syscall.h": "// #define SYS_sysinfo 23\n"})
    assert [r.check.id for r in _ev(spec, files) if not r.passed] == ["number"]
    files = dict(GOOD, **{"usys.pl": '# entry("sysinfo");\n'})
    assert [r.check.id for r in _ev(spec, files) if not r.passed] == ["usys"]


def test_a_hash_in_C_is_a_directive_and_must_not_be_stripped():
    """The most important line in a syscall lab starts with a hash."""
    assert "#define SYS_sysinfo 23" in L.strip_comments("#define SYS_sysinfo 23\n", "syscall.h")
    assert "entry" not in L.strip_comments('# entry("x");\n', "usys.pl")
    assert "keep" in L.strip_comments("/* gone */ keep // gone\n", "a.c")


def test_a_file_that_cannot_be_read_fails_rather_than_raising(spec):
    """Before the folder is seeded, or with the machine down, "not done yet" is the honest answer."""
    res = L.evaluate(spec, lambda _n: None)
    assert all(not r.passed for r in res)
    assert all(r.missing_file for r in res)
    assert L.progress(res)["passed"] == 0


def test_a_reader_that_raises_is_treated_as_a_missing_file(spec):
    def boom(_n):
        raise OSError("machine is down")
    assert all(not r.passed for r in L.evaluate(spec, boom))


def test_a_broken_pattern_in_a_pack_fails_that_check_and_nothing_else():
    s = L.from_yaml("id: x\ntitle: x\nchecks:\n"
                    "  - {id: bad, label: b, file: f, match: '([unclosed'}\n"
                    "  - {id: ok,  label: o, file: f, match: 'hello'}\n")
    res = L.evaluate(s, lambda _n: "hello")
    assert [(r.check.id, r.passed) for r in res] == [("bad", False), ("ok", True)]


def test_progress_counts_by_part(spec):
    part_a_only = {k: v for k, v in GOOD.items() if k in ("syscall.h", "syscall.c", "sysproc.c",
                                                          "user.h", "usys.pl")}
    part_a_only["sysproc.c"] = "uint64\nsys_sysinfo(void)\n{\n  return 0;\n}\n"
    res = _ev(spec, part_a_only)
    p = L.progress(res)
    assert p["parts"]["A"] == (6, 6), "part A is finishable without touching part B's files"
    assert p["parts"]["B"][0] < p["parts"]["B"][1]


def test_an_empty_spec_is_harmless():
    s = L.from_dict({})
    assert s.files == () and s.checks == ()
    assert L.progress(L.evaluate(s, lambda _n: "")) == {"passed": 0, "total": 0, "parts": {}}
    assert L.next_step(()) is None


# --------------------------------------------------------------------------- #
# The check that would have caught it.
#
# Reported from a real run: "Count free memory by walking kmem.freelist" showed a green tick on a
# machine where the student had edited nothing but syscall.h. The pattern was `kmem.freelist`, and
# the UNTOUCHED kalloc.c contains it — `kalloc` and `kfree` both use it. A tick that is green
# before the student starts is worse than no tick: it tells them work is done that is not, and it
# is the one failure a tracker must not have.
# --------------------------------------------------------------------------- #

PRISTINE = {
    # Just enough of the real files to carry what a check might wrongly match. Every line here is
    # from the stock kernel, not invented.
    "syscall.h": "#define SYS_close  21\n#define SYS_sync   22\n",
    "syscall.c": ("extern uint64 sys_sync(void);\n"
                  "static uint64 (*syscalls[])(void) = {\n  [SYS_sync] = sys_sync,\n};\n"
                  "void syscall(void){ num = p->trapframe->a7; }\n"),
    "sysproc.c": ("uint64 sys_exit(void){ int n; argint(0, &n); exit(n); return 0; }\n"
                  "uint64 sys_sbrk(void){ uint64 addr; argaddr(0, &addr); return 0; }\n"),
    "user.h": "int sync(void);\nint uptime(void);\nchar* sbrk(int);\n",
    "usys.pl": 'entry("sync");\nentry("uptime");\n',
    "defs.h": "void*           kalloc(void);\nvoid            kfree(void *);\n",
    # the one that actually caused this
    "kalloc.c": ("void kfree(void *pa){ struct run *r = (struct run*)pa;\n"
                 "  acquire(&kmem.lock); r->next = kmem.freelist; kmem.freelist = r;\n"
                 "  release(&kmem.lock); }\n"
                 "void* kalloc(void){ struct run *r; acquire(&kmem.lock);\n"
                 "  r = kmem.freelist; if(r) kmem.freelist = r->next;\n"
                 "  release(&kmem.lock); return (void*)r; }\n"),
}


def test_no_check_is_green_before_the_student_starts(spec):
    """Every check must detect something the student ADDED, not something that was always there."""
    read = lambda n: PRISTINE.get(n) or next((f.seed for f in spec.files if f.name == n), None)
    res = L.evaluate(spec, read, pristine=read)      # untouched == pristine, so nothing is edited
    green = [r.check.id for r in res if r.passed]
    assert green == [], f"these are green on an untouched tree: {green}"


def test_an_edited_check_needs_the_original_to_compare_against(spec):
    """Without a pristine reader it reports not-done rather than guessing. A green tick for work
    nobody has started is the one wrong answer a tracker can give."""
    read = lambda n: PRISTINE.get(n) or ""
    res = L.evaluate(spec, read)                     # no pristine
    assert not any(r.passed for r in res if r.check.kind == "edited")


def test_an_edited_check_goes_green_when_the_file_changes(spec):
    edited = dict(PRISTINE, **{"kalloc.c": PRISTINE["kalloc.c"] + "\nuint64 freemem(void){...}\n"})
    res = L.evaluate(spec, lambda n: edited.get(n, ""), pristine=lambda n: PRISTINE.get(n))
    assert next(r for r in res if r.check.id == "freelist").passed


def test_the_pack_has_at_least_one_check_per_part_that_is_a_real_pattern(spec):
    """`edited` is a weaker signal than a pattern, so a part made only of `edited` checks would
    tick over on any stray keystroke. Each part keeps at least one real pattern."""
    for part in spec.parts():
        kinds = {c.kind for c in spec.checks if c.part == part}
        assert "match" in kinds, f"part {part} has no pattern-based check"
