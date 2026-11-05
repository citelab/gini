"""A-Lab 03 — buddy allocation and megapages: the spec, the staged checks, and the kernel side.

The evidence for the checks and the stages is the reference solution set: `pristine/` is what an
armed machine seeds, `gold/` the lab solved, seven `wrong-*` versions one mistake each, and `runs/`
what all ten stage commands printed on each of the nine kernels, booted in QEMU. It is NOT in this
repository, which is public: it is the answer to a graded lab. The maintainer keeps it privately
and points GINI_ALAB03_SOLUTIONS at it; without that, the tests that read it skip and everything
else still runs. Nothing from it is copied here -- not a line of a solution, not a transcript.

The stage tests feed the REAL transcripts through the real rules (`lab_stages.from_transcript`)
and must reproduce the build plan's table exactly: that tests the pass rule, the dependencies and
"the kernel stopped" against what the programs actually print, without booting anything.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from gini.domain import grader_view as GV
from gini.domain import lab_spec as L
from gini.domain import lab_stages as S
from gini.domain import xv6_vm as V

ROOT = Path(__file__).resolve().parents[2]
PATCHER = ROOT / "backend" / "xv6" / "gini_patch.py"
SOL = Path(os.environ.get("GINI_ALAB03_SOLUTIONS", "/nonexistent"))
needs_solutions = pytest.mark.skipif(
    not (SOL / "gold").is_dir(),
    reason="the A-Lab 03 solutions are private; set GINI_ALAB03_SOLUTIONS")

#: The build plan's section 7, row by row: 0 A1 A2 B1 B2 B3 C1 C2 C3 C4. "–" is not run.
TABLE = {
    "gold":      "ok ok ok ok ok ok ok ok ok ok",
    "starter":   "ok FAIL – – – – – – – –",
    "nomerge":   "ok ok ok FAIL FAIL FAIL ok ok ok –",
    "anysize":   "FAIL – – – – – – – – –",
    "unaligned": "FAIL – – – – – – – – –",
    "stalemark": "FAIL – – – – – – – – –",
    "nooffset":  "ok ok ok ok ok ok ok FAIL ok –",
    "nowalkfix": "ok ok ok ok ok ok ok FAIL ok –",
    "nozero":    "ok ok ok ok ok ok FAIL – – –",
}
#: The seed the recorded runs were made with.
RUN_SEED = 7


@pytest.fixture(scope="module")
def spec():
    s = L.get("a-lab-03")
    assert s is not None
    return s


def _patcher_literal(name: str) -> str:
    """One of the C sources the patcher embeds, exactly as it writes it into the image."""
    src = PATCHER.read_text(encoding="utf-8")
    m = re.search(rf'^{name} = (r?"""(?:.|\n)*?""")', src, re.MULTILINE)
    assert m, f"gini_patch.py no longer defines {name}"
    return ast.literal_eval(m.group(1))


def _verdicts(check) -> str:
    return " ".join({"ok": "ok", "FAIL": "FAIL", "not run": "–"}[r.verdict] for r in check)


# --------------------------------------------------------------------------- #
# 1. the spec
# --------------------------------------------------------------------------- #

def test_it_is_released_with_the_handouts_title_and_parts(spec):
    assert spec.released
    assert spec.title == "A-Lab 03 — buddy allocation and megapages"
    assert spec.part_titles == {"A": "Splitting", "B": "Coalescing", "C": "Megapages"}
    assert spec.test_prog == "buddytest"
    assert not spec.grade and not spec.runs        # stages grade this lab, not metrics


def test_five_files_in_the_order_of_the_handouts_figure(spec):
    assert [f.name for f in spec.files] == ["kalloc.c", "buddy.h", "vm.c", "buddytest.c",
                                            "megatest.c"]
    assert [f.tree for f in spec.files] == ["kernel/kalloc.c", "kernel/buddy.h", "kernel/vm.c",
                                            "user/buddytest.c", "user/megatest.c"]
    assert spec.file("kalloc.c").also == "B"
    assert not spec.uprogs()                       # both programs are in the image's UPROGS


def test_kalloc_is_the_only_seeded_file_and_the_test_programs_are_read_only(spec):
    assert [f.name for f in spec.files if f.seed] == ["kalloc.c"]
    assert [f.name for f in spec.files if f.readonly] == ["buddytest.c", "megatest.c"]


def test_the_starter_seed_gives_nothing_away(spec):
    """The seed is public -- every student gets it -- so it must be the old free list wearing the
    new interface, and nothing more: no split, no search, no merge."""
    seed = spec.file("kalloc.c").seed
    for fn in ("buddy_init", "buddy_alloc", "buddy_free", "kalloc_block", "kfree_block",
               "block_push", "block_pop", "block_remove", "gini_buddy_booted"):
        assert fn + "(" in seed, fn
    body = seed.split("your part", 1)[1]
    assert "BUDDY(" not in body and "block_remove(" not in body and "MAXK" not in body
    assert "if(k != 0)\n    return 0;" in body


@needs_solutions
def test_the_seed_is_exactly_the_starter_an_armed_machine_gets(spec):
    assert spec.file("kalloc.c").seed == (SOL / "pristine" / "kalloc.c").read_text()


@needs_solutions
@pytest.mark.parametrize("literal,name", [("_BUDDY_H", "buddy.h"),
                                          ("_BUDDYTEST", "buddytest.c"),
                                          ("_MEGATEST", "megatest.c")])
def test_the_image_carries_exactly_the_files_the_student_reads(literal, name):
    assert _patcher_literal(literal) == (SOL / "pristine" / name).read_text()


def test_ten_stages_with_the_handouts_commands_keys_and_needs(spec):
    rows = [(s.id, s.part, s.command, s.key, s.needs) for s in spec.stages]
    assert rows == [
        ("0", "", "buddytest", "", ()),
        ("A1", "A", "buddytest init", "init", ("0",)),
        ("A2", "A", "buddytest alloc {seed}", "alloc", ("A1",)),
        ("B1", "B", "buddytest churn {seed}", "churn", ("A2",)),
        ("B2", "B", "buddytest pieces {seed}", "pieces", ("A2",)),
        ("B3", "B", "buddytest guard", "guard", ("A2",)),
        ("C1", "C", "megatest map", "map", ("A2",)),
        ("C2", "C", "megatest data {seed}", "data", ("C1",)),
        ("C3", "C", "megatest edge {seed}", "edge", ("C1",)),
        ("C4", "C", "megatest leaks {seed}", "leaks", ("B1", "B2", "B3", "C1", "C2", "C3")),
    ]
    assert all(s.label for s in spec.stages)
    assert {s.timeout for s in spec.stages if s.command.startswith("buddytest")} == {60.0}
    assert {s.timeout for s in spec.stages if s.command.startswith("megatest")} == {120.0}
    assert spec.stage_parts() == ("A", "B", "C")


def test_stage_zero_is_the_audit_rule(spec):
    z = spec.stage("0")
    assert z.expected == ("overlaps: 0", "unaccounted: 0")
    assert z.rejected == ("misplaced:", "double frees refused:")


def test_every_other_stage_is_the_plain_pass_rule(spec):
    for s in spec.stages[1:]:
        assert s.expected == (f"{s.key}: ok",) and s.rejected == (": FAIL",)


def test_the_hints_are_hints_not_answers(spec):
    assert len(spec.checks) == 11
    for c in spec.checks:
        assert c.hint and c.where and c.label, c.id
        # no code in a hint: a hint that compiles is an answer
        assert not re.search(r"[{}]|==|\w\s*=\s*\w|\)\s*;", c.hint), c.id


# --------------------------------------------------------------------------- #
# 2. checks against the reference solutions
# --------------------------------------------------------------------------- #

def _evaluate(spec, variant):
    def read(name):
        for base in ((SOL / variant) if variant else None, SOL / "pristine"):
            if base is not None and (base / name).exists():
                return (base / name).read_text()
        return None
    return {r.check.id: r.passed for r in L.evaluate(spec, read)}


@needs_solutions
def test_every_check_is_red_on_the_files_a_student_starts_with(spec):
    got = _evaluate(spec, None)
    assert not any(got.values()), [k for k, v in got.items() if v]


@needs_solutions
def test_every_check_is_green_on_the_reference_solution(spec):
    got = _evaluate(spec, "gold")
    assert all(got.values()), [k for k, v in got.items() if not v]


# --------------------------------------------------------------------------- #
# 3. stage verdicts from what the programs really printed
# --------------------------------------------------------------------------- #

@needs_solutions
@pytest.mark.parametrize("kernel", sorted(TABLE))
def test_each_kernel_gets_its_row_of_the_table(spec, kernel):
    text = (SOL / "runs" / f"{kernel}.txt").read_text()
    check = S.from_transcript(spec, text, RUN_SEED)
    assert _verdicts(check) == TABLE[kernel]


@needs_solutions
def test_a_failure_is_summarised_by_the_line_that_decided_it(spec):
    def summary(kernel, sid):
        return S.from_transcript(spec, (SOL / "runs" / f"{kernel}.txt").read_text(),
                                 RUN_SEED).get(sid).summary
    assert summary("starter", "A1").startswith("init: FAIL")
    assert summary("nozero", "C1") == "map: FAIL"
    # data: ok came first; the FAIL line after it is what counts
    assert summary("nooffset", "C2").startswith("read into heap: FAIL")
    assert summary("unaligned", "0").startswith("misplaced: ")
    assert summary("anysize", "0") == "panic: kerneltrap"


@needs_solutions
def test_a_kernel_that_dies_stops_the_check(spec):
    run = S.from_transcript(spec, (SOL / "runs" / "anysize.txt").read_text(), RUN_SEED)
    assert run.get("0").stopped
    assert {r.summary for r in run.results[1:]} == {"the kernel stopped during 0"}


@needs_solutions
def test_a_kernel_that_never_boots_fails_stage_zero_with_its_panic(spec):
    run = S.from_transcript(spec, (SOL / "runs" / "stalemark.txt").read_text(), RUN_SEED)
    assert run.get("0").summary == "panic: kerneltrap"
    assert {r.summary for r in run.results[1:]} == {"the kernel stopped during boot"}


@needs_solutions
def test_a_failed_prerequisite_is_named_in_the_reason(spec):
    run = S.from_transcript(spec, (SOL / "runs" / "nomerge.txt").read_text(), RUN_SEED)
    assert run.get("C4").summary == "needs B1"      # the FIRST of its needs that did not pass
    run = S.from_transcript(spec, (SOL / "runs" / "starter.txt").read_text(), RUN_SEED)
    assert run.get("A2").summary == "needs A1"
    assert run.get("C2").summary == "needs C1"


# The rules on their own, with made-up output, so they are pinned without the solutions too.

def _stage(**kw):
    kw.setdefault("id", "A2")
    kw.setdefault("command", "buddytest alloc {seed}")
    kw.setdefault("key", "alloc")
    return L.Stage(**kw)


def test_the_verdict_line_must_be_a_whole_line():
    """buddytest prints `4K: ok` and A2 prints `alloc 2M: ok`. Neither is `alloc: ok`."""
    st = _stage()
    v, why, _ = S.judge(st, ["4K: ok", "alloc 2M: ok", "overlaps: 0"], True)
    assert v == S.FAIL and why == "did not print 'alloc: ok'"
    assert S.judge(st, ["alloc 2M: ok", "alloc: ok"], True)[0] == S.OK
    assert S.judge(st, ["  alloc: ok  "], True)[0] == S.OK


def test_any_fail_line_fails_the_stage_even_after_its_ok_line():
    st = _stage(id="C2", command="megatest data {seed}", key="data")
    v, why, stopped = S.judge(st, ["data: ok", "read into heap: FAIL wrong place"], True)
    assert (v, why, stopped) == (S.FAIL, "read into heap: FAIL wrong place", False)


def test_a_panic_or_a_missing_prompt_fails_and_stops_the_kernel():
    st = _stage()
    assert S.judge(st, ["alloc: ok", "panic: kfree"], False) == (S.FAIL, "panic: kfree", True)
    v, why, stopped = S.judge(st, ["alloc 4K: ok"], False)
    assert (v, why, stopped) == (S.FAIL, "timed out after 60 s", True)


def test_stage_zero_fails_on_any_audit_problem():
    z = L.Stage(id="0", command="buddytest", expect=("overlaps: 0", "unaccounted: 0"),
                reject=("misplaced:", "double frees refused:"))
    clean = ["4K: ok", "8K: none", "overlaps: 0", "unaccounted: 0"]
    assert S.judge(z, clean, True)[0] == S.OK
    assert S.judge(z, clean + ["double frees refused: 2"], True)[1] == "double frees refused: 2"
    assert S.judge(z, ["overlaps: 3", "unaccounted: 0"], True)[1] == "overlaps: 3"
    assert S.judge(z, ["overlaps: 0"], True)[1] == "did not print 'unaccounted: 0'"


def test_check_part_b_runs_what_part_b_needs_first(spec):
    assert [s.id for s in S.plan(spec, "A")] == ["0", "A1", "A2"]
    assert [s.id for s in S.plan(spec, "B")] == ["0", "A1", "A2", "B1", "B2", "B3"]
    # C4 needs Part B, so Check Part C is every stage
    assert [s.id for s in S.plan(spec, "C")] == [s.id for s in spec.stages]
    assert S.plan(spec, "all") == S.plan(spec, None) == spec.stages


def test_a_dead_kernel_fails_the_first_stage_and_runs_nothing(spec):
    ran = []
    check = S.run(S.plan(spec, "A"), lambda st, c: ran.append(c) or ([], True), 5,
                  dead="panic: kerneltrap")
    assert ran == []
    assert check.get("0").verdict == S.FAIL and check.get("0").summary == "panic: kerneltrap"
    assert check.get("A2").summary == "the kernel stopped during boot"


def test_a_note_from_the_runner_replaces_a_misleading_timeout(spec):
    check = S.run(S.plan(spec, "A")[:1], lambda st, c: ([], False, "GINI could not start it: x"),
                  5)
    assert check.get("0").summary == "GINI could not start it: x"


def test_the_live_progress_names_each_stage_as_it_starts(spec):
    heard = []
    S.run(S.plan(spec, "A"), lambda st, c: (["overlaps: 0", "unaccounted: 0", "init: ok"], True),
          42, say=heard.append)
    assert heard == ["0: buddytest", "A1: buddytest init", "A2: buddytest alloc 42"]


def test_a_down_machine_records_every_stage_as_not_run(spec):
    check = S.not_run(S.plan(spec, "all"), 9, "all", "the machine was not running")
    assert check.tally() == {"ok": 0, "failed": 0, "not_run": 10, "total": 10}
    assert all(r.ok is None for r in check)


# --------------------------------------------------------------------------- #
# 4. the seed
# --------------------------------------------------------------------------- #

def test_the_seed_is_filled_in_and_a_stage_without_one_is_unchanged(spec):
    assert S.command_for(spec.stage("A2"), 123456) == "buddytest alloc 123456"
    assert S.command_for(spec.stage("A1"), 123456) == "buddytest init"
    assert S.command_for(spec.stage("C4"), 7) == "megatest leaks 7"


def test_a_fresh_seed_is_in_range_and_shared_by_the_whole_check(spec):
    import random
    rng = random.Random(1)
    seeds = {S.new_seed(rng) for _ in range(200)}
    assert all(S.SEED_MIN <= s <= S.SEED_MAX for s in seeds) and len(seeds) > 150
    check = S.run(spec.stages, lambda st, c: ([], True), 31337)
    assert {r.seed for r in check} == {31337}


# --------------------------------------------------------------------------- #
# 5. read-only files
# --------------------------------------------------------------------------- #

def test_link_script_never_links_a_read_only_file_and_still_links_the_rest(spec):
    from gini.services import xv6_lab as X
    script = X.link_script(spec)
    for name in ("buddytest.c", "megatest.c"):
        assert f"user/{name}" not in [ln.split()[-1] for ln in script.splitlines()
                                      if ln.startswith("ln -sf")]
        assert f"chmod a-w /opt/xv6-lab/a-lab-03/{name}" in script
    for tree in ("kernel/kalloc.c", "kernel/buddy.h", "kernel/vm.c"):
        assert f"ln -sf /opt/xv6-lab/a-lab-03/{tree.split('/')[1]} {tree}" in script


def test_link_script_puts_the_image_copy_back_if_a_read_only_file_was_ever_linked(spec):
    from gini.services import xv6_lab as X
    script = X.link_script(spec)
    assert ("if [ -L user/buddytest.c ] && [ -f /opt/xv6-pristine/user/buddytest.c ]; then "
            "rm -f user/buddytest.c; cp /opt/xv6-pristine/user/buddytest.c user/buddytest.c; fi"
            ) in script


def test_a_read_only_file_reads_as_read_only_and_cannot_be_reverted(spec, tmp_path, monkeypatch):
    from gini.services import xv6_lab as X
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    X.seed_host_files(spec, "M1")
    (X.lab_dir("M1", spec.id) / "buddytest.c").write_text("// GINI's copy\n")
    assert X.state_of(spec, "M1", "buddytest.c") == "readonly"
    ok, msg = X.revert(spec, "M1", "buddytest.c")
    assert not ok and "cannot be changed" in msg
    # and the seeded file is still the student's own
    assert X.state_of(spec, "M1", "kalloc.c") == "untouched"


def test_other_labs_have_no_read_only_files():
    for sid in ("syscall-sysinfo", "a-lab-02"):
        assert not any(f.readonly for f in L.get(sid).files), sid


# --------------------------------------------------------------------------- #
# recording, and the marker's view
# --------------------------------------------------------------------------- #

class _Rec:
    def __init__(self):
        self.calls = []

    def note_spawn(self, *a, **kw):
        self.calls.append(("spawn", a, kw))

    def note_measure(self, name, result):
        self.calls.append(("measure", name, result))


def _check(spec):
    out = {"0": (["overlaps: 0", "unaccounted: 0"], True),
           "A1": (["init: FAIL memory was not given out"], True)}
    return S.run(S.plan(spec, "B"), lambda st, c: out[st.id], 4242, "B")


def test_every_stage_is_recorded_as_a_spawn_then_a_measure(spec):
    from gini.services import xv6_lab as X
    rec = _Rec()
    X.record_grades(rec, spec, _check(spec), device="M1")
    kinds = [c[0] for c in rec.calls]
    # 0 and A1 ran: spawn + measure each. A2, B1, B2, B3 did not: a measure each, no spawn.
    assert kinds == ["spawn", "measure", "spawn", "measure"] + ["measure"] * 4
    spawn = rec.calls[2]
    assert spawn[1][:2] == ("M1", "buddytest init")
    assert spawn[1][4] == ["init: FAIL memory was not given out"]
    assert spawn[2] == {"run": "stage A1"}
    name, res = rec.calls[3][1], rec.calls[3][2]
    assert name == "A-Lab 03 — buddy allocation and megapages · stage A1"
    assert res["ok"] is False and res["pending"] is False
    assert res["measurement"] == {"stage": "A1", "seed": 4242,
                                  "summary": "init: FAIL memory was not given out"}
    a2 = rec.calls[4][2]
    assert a2["pending"] is True and a2["measurement"]["summary"] == "not run — needs A1"


def test_the_markers_view_lists_the_stages_with_their_verdicts():
    rows = [{"kind": "measurement", "name": f"T · stage {sid}", "ok": ok, "pending": pending,
             "summary": summary}
            for sid, ok, pending, summary in (("0", True, False, "ok"),
                                              ("A1", False, False, "init: FAIL x"),
                                              ("A2", False, True, "not run — needs A1"))]
    cooked = GV.cook([], "T", measurements=rows)
    assert cooked["stages"] == [{"id": "0", "verdict": "ok", "summary": "ok"},
                                {"id": "A1", "verdict": "FAIL", "summary": "init: FAIL x"},
                                {"id": "A2", "verdict": "not run",
                                 "summary": "not run — needs A1"}]
    assert "Grading: stage A1 failed — init: FAIL x" in cooked["look_at"]
    assert "Grading: stage A2 not run — needs A1" in cooked["look_at"]
    assert not any("nothing to compare" in ln for ln in cooked["look_at"])
    # one row per stage for the class table
    assert [m["id"] for m in cooked["table"]["metrics"]] == ["stage 0", "stage A1", "stage A2"]


# --------------------------------------------------------------------------- #
# the runner, against a fake machine
# --------------------------------------------------------------------------- #

class _Prov:
    """A machine whose console prints `script` after each command, a chunk per poll."""

    def __init__(self, outputs, console=""):
        self.outputs, self._console, self.typed = dict(outputs), console, []
        self.stream, self.last_run_error = "", ""

    def console(self):
        return self._console

    def console_since(self, cursor):
        return self.stream[cursor:], len(self.stream)

    def run(self, prog, args="", foreground=False):
        cmd = (prog + " " + args).strip()
        self.typed.append(cmd)
        if cmd not in self.outputs:
            self.last_run_error = "no such program"
            return False
        self.stream += cmd + "\n" + self.outputs[cmd]
        return True


def test_the_runner_runs_each_stage_and_reads_what_it_printed(spec, monkeypatch):
    from gini.services import xv6_lab as X
    monkeypatch.setattr("time.sleep", lambda s: None)
    prov = _Prov({"buddytest": "overlaps: 0\nunaccounted: 0\n$ ",
                  "buddytest init": "init: ok\n$ ",
                  "buddytest alloc 77": "alloc 2M: ok\nalloc: ok\n$ "},
                 console="init: starting sh\n$ ")
    check = X.check_stages(spec, prov, "A", seed=77)
    assert prov.typed == ["buddytest", "buddytest init", "buddytest alloc 77"]
    assert _verdicts(check) == "ok ok ok"
    assert check.get("A2").lines == ("alloc 2M: ok", "alloc: ok")


def test_a_stage_is_judged_on_all_it_printed_not_on_the_first_ten_lines(spec, monkeypatch):
    """buddytest prints thirteen lines and `overlaps: 0` is the twelfth. The proof's ten-line cap
    for a test program once cut it off, and stage 0 failed the model answer."""
    from gini.services import xv6_lab as X
    monkeypatch.setattr("time.sleep", lambda s: None)
    sizes = [f"{k}: ok" for k in ("4K", "8K", "16K", "32K", "64K", "128K", "256K", "512K", "1M",
                                  "2M")]
    report = "\n".join([*sizes, "free: 4K:3 2M:63", "overlaps: 0", "unaccounted: 0"])
    check = X.check_stages(spec, _Prov({"buddytest": report + "\n$ ", "buddytest init": "$ "},
                                       console="$ "), "A", seed=1)
    assert check.get("0").verdict == S.OK
    assert len(check.get("0").lines) == 13          # and all of it is kept for the record


def test_a_panic_ends_the_wait_without_sitting_out_the_timeout(spec, monkeypatch):
    from gini.services import xv6_lab as X
    clock = [0.0]
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    prov = _Prov({"buddytest": "scause=0xf\npanic: kerneltrap\n"}, console="$ ")
    check = X.check_stages(spec, prov, "A", seed=1)
    assert clock[0] < 5                            # not the 60 s timeout
    assert check.get("0").summary == "panic: kerneltrap"
    assert check.get("A1").summary == "the kernel stopped during 0"
    assert prov.typed == ["buddytest"]


def test_a_kernel_that_panicked_at_boot_is_not_typed_at(spec, monkeypatch):
    from gini.services import xv6_lab as X
    prov = _Prov({}, console="hart 1 starting\nscause=0xf\npanic: kerneltrap\n")
    check = X.check_stages(spec, prov, "all", seed=1)
    assert prov.typed == []
    assert check.get("0").summary == "panic: kerneltrap"


def test_an_old_panic_followed_by_a_fresh_boot_is_not_a_dead_kernel():
    from gini.services import xv6_lab as X
    fresh = "panic: kfree\n\nxv6 kernel is booting\ninit: starting sh\n$ "
    assert X.kernel_dead(_Prov({}, console=fresh)) == ("", "")
    assert X.kernel_dead(_Prov({}, console="init: starting sh\n$ ")) == ("", "")


def test_a_crash_left_by_the_last_check_is_not_called_a_boot_failure(spec):
    from gini.services import xv6_lab as X
    left = "xv6 kernel is booting\ninit: starting sh\n$ megatest leaks 5\nfork: ok\npanic: kfree\n"
    assert X.kernel_dead(_Prov({}, console=left)) == ("panic: kfree", "an earlier command")
    check = X.check_stages(spec, _Prov({}, console=left), "A", seed=1)
    assert check.get("0").summary == "panic: kfree"
    assert check.get("A1").summary == "the kernel stopped during an earlier command"


def test_a_command_the_agent_refuses_says_why(spec, monkeypatch):
    from gini.services import xv6_lab as X
    check = X.check_stages(spec, _Prov({}, console="$ "), "A", seed=1)
    assert check.get("0").summary == "GINI could not start it: no such program"


def test_with_the_machine_down_every_stage_is_not_run(spec):
    from gini.services import xv6_lab as X
    check = X.check_stages(spec, None, "all", seed=3)
    assert {r.summary for r in check} == {"the machine was not running"}


# --------------------------------------------------------------------------- #
# 6. the kernel side (gini_patch.py)
# --------------------------------------------------------------------------- #

# Just enough of the pinned xv6 tree for section 6's anchors -- the same text the real files have.
VM_C = """#include "param.h"
void kvminit(){}

pte_t *
walk(pagetable_t pagetable, uint64 va, int alloc)
{
  if (va >= MAXVA)
    panic("walk");

  for (int level = 2; level > 0; level--) {
    pte_t *pte = &pagetable[PX(level, va)];
    if (*pte & PTE_V) {
      pagetable = (pagetable_t)PTE2PA(*pte);
    } else {
      if (!alloc || (pagetable = (pde_t *)kalloc()) == 0)
        return 0;
      memset(pagetable, 0, PGSIZE);
      *pte = PA2PTE(pagetable) | PTE_V;
    }
  }
  return &pagetable[PX(0, va)];
}

uint64
walkaddr(pagetable_t pagetable, uint64 va)
{
  pte_t *pte;
  uint64 pa;

  pte = walk(pagetable, va, 0);
  pa = PTE2PA(*pte);
  return pa;
}

// Remove npages of mappings starting from va. va must be
// page-aligned. It's OK if the mappings don't exist.
// Optionally free the physical memory.
void
uvmunmap(pagetable_t pagetable, uint64 va, uint64 npages, int do_free)
{
  uint64 a;
  pte_t *pte;

  if ((va % PGSIZE) != 0)
    panic("uvmunmap: not aligned");

  for (a = va; a < va + npages * PGSIZE; a += PGSIZE) {
    if ((pte = walk(pagetable, a, 0)) == 0) // leaf page table entry allocated?
      continue;
    *pte = 0;
  }
}

// Allocate PTEs and physical memory to grow a process from oldsz to
// newsz, which need not be page aligned.  Returns new size or 0 on error.
uint64
uvmalloc(pagetable_t pagetable, uint64 oldsz, uint64 newsz, int xperm)
{
  char *mem;
  uint64 a;

  for (a = PGROUNDUP(oldsz); a < newsz; a += PGSIZE) {
    mem = kalloc();
    memset(mem, 0, PGSIZE);
  }
  return newsz;
}

// Given a parent process's page table, copy
// its memory into a child's page table.
// Copies both the page table and the
// physical memory.
int
uvmcopy(pagetable_t old, pagetable_t new, uint64 sz)
{
  pte_t *pte;
  uint64 pa, i;
  uint flags;
  char *mem;

  for (i = 0; i < sz; i += PGSIZE) {
    if ((pte = walk(old, i, 0)) == 0)
      continue;
  }
  return 0;
}
"""
KALLOC_C = ("void\nkfree(void *pa)\n{\n  struct run *r;\n}\n\n"
            "void *\nkalloc(void)\n{\n  struct run *r;\n\n  return (void*)r;\n}\n")
RISCV_H = "#define PTE_X (1L << 3)\n#define MAXVA (1L << (9 + 9 + 9 + 12 - 1))\n"
DEFS_EXTRA = ("void*           kalloc(void);\nvoid            kfree(void *);\n"
              "void            kinit(void);\n"
              "uint64          walkaddr(pagetable_t, uint64);\n"
              "int             copyout(pagetable_t, uint64, uint64, char *, uint64);\n")


def _tree(tmp_path, **override):
    import test_xv6_patch as T
    k = tmp_path / "kernel"
    k.mkdir()
    (tmp_path / "user").mkdir()
    files = {"trap.c": T.TRAP_C, "proc.c": T.PROC_C, "proc.h": T.PROC_H, "vm.c": VM_C,
             "defs.h": T.DEFS_H + DEFS_EXTRA, "console.c": T.CONSOLE_C,
             "fs.c": "struct superblock sb;\n", "log.c": "struct log log;\n",
             "param.h": T.PARAM_H, "syscall.c": T.SYSCALL_C, "kalloc.c": KALLOC_C,
             "riscv.h": RISCV_H}
    files.update(override)
    for name, text in files.items():
        (k / name).write_text(text)
    (tmp_path / "Makefile").write_text(T.MAKEFILE.replace(
        "OBJS = \\\n", "OBJS = \\\n  $K/kalloc.o \\\n"))
    return k


def _patch(root):
    return subprocess.run([sys.executable, str(PATCHER), str(root)], capture_output=True,
                          text=True, check=False)


@pytest.fixture(scope="module")
def patched(tmp_path_factory):
    root = tmp_path_factory.mktemp("xv6")
    k = _tree(root)
    r = _patch(root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root, k


def test_kalloc_c_no_longer_defines_the_bitmap_and_gini_mem_c_does(patched):
    _, k = patched
    kalloc = (k / "kalloc.c").read_text()
    for fn in ("gini_page_set(uint64 pa)\n{", "gini_kadump(void)\n{", "gsh_kalloc(void *arg)\n{",
               "uint8 gini_pagemap"):
        assert fn not in kalloc, fn
    mem = (k / "gini_mem.c").read_text()
    for fn in ("gini_page_set(uint64 pa)\n{", "gini_page_clear(uint64 pa)\n{",
               "gini_page_isset(uint64 pa)\n{", "gsh_kalloc(void *arg)\n{",
               "gsh_kalloc_valid(void *arg, void *ans)\n{", "gini_kadump(void)\n{",
               "uint64\ngini_memctl(void)\n{"):
        assert fn in mem, fn
    assert 'printk("KA free %d total %d maxrun %d shadow %d\\n"' in mem   # the dump is unchanged


def test_the_moved_bitmap_is_weak_so_an_older_a_lab_01_kalloc_c_still_links(patched):
    """An A-Lab 01 folder keeps the kalloc.c it was seeded with, and an older image's copy still
    DEFINES the bitmap. With strong definitions here that kernel would fail to link."""
    mem = (patched[1] / "gini_mem.c").read_text()
    assert "uint8 gini_pagemap[(NPAGES + 7) / 8] __attribute__((weak));" in mem
    assert "uint64 gini_ka_shadow __attribute__((weak));" in mem
    for fn in ("gini_page_set", "gini_page_clear", "gini_page_isset", "gsh_kalloc",
               "gsh_kalloc_valid", "gini_kadump"):
        assert re.search(rf"__attribute__\(\(weak\)\) [\w *]+\n{fn}\(", mem), fn


def test_an_older_student_defs_h_is_not_needed_by_gini_code(patched):
    """defs.h is a student file in A-Lab 01 and 02, kept from whatever image armed it. The image's
    own vm.c and gini_sys.c must therefore declare what A-Lab 03 added for themselves."""
    _, k = patched
    vm = (k / "vm.c").read_text()
    for decl in ("void *kalloc_block(uint64) __attribute__((weak));",
                 "void kfree_block(void *, uint64) __attribute__((weak));",
                 "int demote(pagetable_t, uint64);"):
        assert decl in vm, decl
    assert vm.index("int demote(pagetable_t, uint64);") < vm.index("\nuvmunmap(")
    assert "\nuint64 gini_memctl(void);\n" in (k / "gini_sys.c").read_text()


def test_the_image_vm_c_carries_gini_s_megapage_code(patched):
    vm = (patched[1] / "vm.c").read_text()
    for fn in ("\nwalk_level(pagetable_t pagetable, uint64 va, int level, int alloc)\n{",
               "\nmegapte(pagetable_t pagetable, uint64 va)\n{",
               "\ndemote(pagetable_t pagetable, uint64 va)\n{"):
        assert vm.count(fn) == 1, fn
    unmap = vm.split("\nuvmunmap(", 1)[1].split("\n}\n", 1)[0]
    assert "kfree_block((void *)PTE2PA(*mpte), MEGAPGSIZE);" in unmap
    assert "demote(pagetable, a)" in unmap
    copy = vm.split("\nuvmcopy(", 1)[1].split("\n}\n", 1)[0]
    assert "kalloc_block(MEGAPGSIZE)" in copy and "No 2 MB block" in copy
    assert vm.count("GINI (A-Lab 03), provided") == 5


def test_none_of_the_three_student_changes_is_in_the_image(spec, patched):
    """walk()'s leaf check, walkaddr()'s offset and uvmalloc()'s megapages are the lab. Every
    Part C check must be red on what the image ships."""
    vm = (patched[1] / "vm.c").read_text()
    got = {r.check.id: r.passed for r in L.evaluate(spec, lambda n: vm if n == "vm.c" else None)
           if r.check.part == "C"}
    assert got == {"walk_leaf": False, "walkaddr_offset": False, "uvmalloc_mega": False,
                   "megapage_zero": False}


def test_memctl_is_gini_s_system_call_67(patched):
    root, k = patched
    assert "#define SYS_gini_memctl      67" in (k / "gini_syscall.h").read_text()
    sysc = (k / "gini_sys.c").read_text()
    assert "case SYS_gini_memctl:\n    return gini_memctl();" in sysc
    assert "uint64 memctl(int op, uint64 a, uint64 b);" in (root / "user" / "gini.h").read_text()
    assert "memctl:\n li a7, SYS_gini_memctl" in (root / "user" / "gini_usys.S").read_text()


def test_the_shared_declarations_and_the_build(patched):
    root, k = patched
    riscv = (k / "riscv.h").read_text()
    for m in ("#define MEGAPGSIZE (1L << 21)", "#define MEGAROUNDUP(sz)", "#define MEGAROUNDDOWN(a)",
              "#define PTE_LEAF(pte)"):
        assert m in riscv, m
    defs = (k / "defs.h").read_text()
    for d in ("void*           kalloc_block(uint64) __attribute__((weak));",
              "void            gini_buddy_booted(void);", "uint64          gini_memctl(void);",
              "pte_t *         walk_level(pagetable_t, uint64, int, int);",
              "int             demote(pagetable_t, uint64);"):
        assert d in defs, d
    mk = (root / "Makefile").read_text()
    assert "  $K/kalloc.o \\\n  $K/gini_mem.o \\\n" in mk
    assert "\t$U/_buddytest\\\n" in mk and "\t$U/_megatest\\\n" in mk
    for f in ("buddy.h", "gini_mem.h", "gini_mem.c"):
        assert (k / f).is_file(), f
    assert (root / "user" / "buddytest.c").is_file() and (root / "user" / "megatest.c").is_file()


def test_the_test_programs_refuse_politely_on_another_lab_s_kernel():
    for name in ("_BUDDYTEST", "_MEGATEST"):
        prog = name[1:].lower()
        assert f'"{prog}: memctl failed -- is this GINI\'s A-Lab 03 kernel?\\n"' in \
            _patcher_literal(name), prog


def test_section_six_is_idempotent(tmp_path):
    k = _tree(tmp_path)
    assert _patch(tmp_path).returncode == 0
    first = {p.name: p.read_text() for p in k.iterdir() if p.is_file()}
    assert _patch(tmp_path).returncode == 0
    vm = (k / "vm.c").read_text()
    assert vm.count("walk_level(pagetable_t pagetable") == 1
    assert vm.count("No 2 MB block") == 1
    assert (k / "defs.h").read_text().count("A-Lab 03: a block of 4096") == 1
    assert (k / "gini_mem.c").read_text() == first["gini_mem.c"]


def test_a_moved_megapage_anchor_stops_the_image_build(tmp_path):
    """A kernel without GINI's uvmcopy builds and boots, and its fork of a megapage would copy a
    level-1 leaf as if it were a page table. That must not ship quietly."""
    _tree(tmp_path, **{"vm.c": VM_C.replace("  char *mem;\n\n  for (i = 0;",
                                            "  char *mem;\n  for (i = 0;")})
    r = _patch(tmp_path)
    assert r.returncode != 0
    assert "FAILED: kernel/vm.c: anchor for No 2 MB block" in r.stdout


def test_the_vl_line_carries_the_leaf_level():
    src = PATCHER.read_text(encoding="utf-8")
    assert 'PRINTF("VL %d %p %p %d %d\\\\n", pid, (void*)cva, (void*)PTE2PA(pte), ' \
           '(int)(pte & 0x3FF),\n             level);' in src


# --------------------------------------------------------------------------- #
# the Memory face reads megapages (1.7)
# --------------------------------------------------------------------------- #

def test_a_vl_line_with_a_level_is_a_megapage_and_one_without_is_4k():
    procs = V.parse_vmall("VP 3 megatest 0x0000000000a00000\n"
                          "VL 3 0x0000000000000000 0x0000000087f00000 31\n"
                          "VL 3 0x0000000000200000 0x0000000087a00000 23 1\n"
                          "VL 3 0x0000000000001000 0x0000000087f01000 23 0\n")
    leaves = procs[3].leaves
    assert [x.size for x in leaves] == [V.PGSIZE, V.MEGAPGSIZE, V.PGSIZE]
    assert leaves[1].mega and not leaves[0].mega
    assert procs[3].resident_pages == 1 + 512 + 1


def test_vmprint_s_level_one_leaf_is_one_megapage():
    text = ("page table 0x0000000087f6d000\n"
            "..0: pte 0x0000000021fd8801 pa 0x0000000087f62000\n"
            ".. ..0: pte 0x0000000021fd8401 pa 0x0000000087f61000\n"
            ".. .. ..0: pte 0x0000000021fd805b pa 0x0000000087f61000\n"
            ".. ..1: pte 0x0000000021e80017 pa 0x0000000087a00000\n")
    vm = V.parse_vmprint(text)
    assert [(hex(x.va), x.size) for x in vm.leaves] == [("0x0", V.PGSIZE),
                                                        ("0x200000", V.MEGAPGSIZE)]


def test_a_heap_ending_in_a_megapage_is_drawn_to_its_end():
    leaves = [V.Pte(0x0, 0x87f00000, "r-xu", flags=0x1b),
              V.Pte(0x1000, 0x87f01000, "rw--", flags=0x07),      # the guard
              V.Pte(0x2000, 0x87f02000, "rw-u", flags=0x17),      # the stack
              V.Pte(0x200000, 0x87a00000, "rw-u", flags=0x17, size=V.MEGAPGSIZE)]
    heap = next(r for r in V.regions_from_leaves(leaves) if r.name == "heap")
    assert heap.end == 0x400000 - 1


def test_a_fault_inside_a_megapage_finds_its_leaf():
    from types import SimpleNamespace
    pv = V.ProcVm(pid=3, sz=0x400000,
                  leaves=[V.Pte(0x200000, 0x87a00000, "r--u", flags=0x13, size=V.MEGAPGSIZE)])
    f = SimpleNamespace(pid=3, va=0x2345678 & 0x3fffff, scause=15, cause="store", kind="")
    V.classify_faults([f], {3: pv})
    assert f.kind == "cow-write"                  # present, not writable: not "lazy-alloc"


# --------------------------------------------------------------------------- #
# the User Code panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def alab03_panel(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "gini"))
    from PySide6.QtWidgets import QApplication

    from gini.services import xv6_lab as X
    from gini.ui.theme import ThemeManager
    from gini.ui.user_code import UserCode
    app = QApplication.instance() or QApplication([])
    spec = L.get("a-lab-03")
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")

    class _Dev:
        name, type_key, properties = "M1", "xv6", {}

    class _P:
        kernel_rejects = None

    w = UserCode(None, ThemeManager(app), device=_Dev(), provider=_P(), spec=spec, live=True)
    w.show()
    app.processEvents()
    yield w, spec, app
    w.close()
    w.deleteLater()
    app.processEvents()


def test_the_panel_has_a_check_button_per_part_instead_of_check_my_work(alab03_panel):
    w, _, _ = alab03_panel
    assert [b.text().strip() for b in w._check_btns] == ["Check Part A", "Check Part B",
                                                         "Check Part C", "Check all"]
    assert all(b.isEnabled() for b in w._check_btns)
    assert not w._grade_btn.isVisible()


def test_read_only_files_have_no_revert_button(alab03_panel):
    from PySide6.QtWidgets import QPushButton
    w, _, _ = alab03_panel
    reverts = [b for b in w.findChildren(QPushButton) if b.text() == "Revert"]
    assert len(reverts) == 3                       # kalloc.c, buddy.h, vm.c
    assert len(w._rows) == 5                       # but all five are listed


def test_a_check_fills_in_each_stage_row_and_its_transcript(alab03_panel):
    w, spec, app = alab03_panel
    out = {"0": (["overlaps: 0", "unaccounted: 0"], True),
           "A1": (["init: FAIL memory was not given out"], True)}
    check = S.run(S.plan(spec, "A"), lambda st, c: out[st.id], 99, "A")
    w._on_grade_result(check)
    app.processEvents()
    verdict, summary, transcript, out_btn = w._stage_rows["A1"]
    assert verdict.text() == "FAIL"
    assert summary.text() == "init: FAIL memory was not given out"
    assert transcript.toPlainText() == "$ buddytest init\ninit: FAIL memory was not given out"
    assert out_btn.isEnabled()
    a2 = w._stage_rows["A2"]
    assert a2[0].text() == "not run" and a2[1].text() == "needs A1" and not a2[3].isEnabled()
    log = w._log.toPlainText()
    assert log.startswith("Check Part A — seed 99: 1 ok, 1 FAIL, 1 not run")


def test_a_part_check_clears_the_rows_it_did_not_run(alab03_panel):
    w, spec, app = alab03_panel
    every = S.run(spec.stages, lambda st, c: ([f"{st.key}: ok", "overlaps: 0", "unaccounted: 0"],
                                              True), 1)
    w._on_grade_result(every)
    assert w._stage_rows["C4"][0].text() == "ok"
    part_a = S.run(S.plan(spec, "A"), lambda st, c: ([f"{st.key}: ok", "overlaps: 0",
                                                      "unaccounted: 0"], True), 2, "A")
    w._on_grade_result(part_a)
    assert w._stage_rows["A2"][0].text() == "ok"
    assert w._stage_rows["C4"][0].text() == "·"
    assert w._stage_rows["C4"][1].text() == spec.stage("C4").label


def test_the_stage_being_run_is_shown_live(alab03_panel):
    w, _, _ = alab03_panel
    w._on_grade_step("B2: buddytest pieces 5")
    assert w._stage_rows["B2"][0].text() == "running…"
    assert "B2: buddytest pieces 5" in w._log.toPlainText()


# --------------------------------------------------------------------------- #
# Load says when the kernel built but did not boot (section 5)
# --------------------------------------------------------------------------- #

def test_load_watches_the_boot_and_reports_a_panic(monkeypatch):
    from gini.services import xv6_lab as X
    monkeypatch.setattr("time.sleep", lambda s: None)
    prov = _Prov({})
    prov.stream = "old output\n$ "
    cursor = len(prov.stream)
    prov.stream += "\nxv6 kernel is booting\n\nhart 1 starting\npanic: kvmmap\n"
    assert X.watch_boot(prov, cursor, timeout=5) == ("panic", "panic: kvmmap")
    prov.stream = prov.stream[:cursor] + "\nxv6 kernel is booting\ninit: starting sh\n$ "
    assert X.watch_boot(prov, cursor, timeout=5) == ("booted", "")


def test_the_panel_says_a_loaded_kernel_did_not_boot(alab03_panel):
    w, _, _ = alab03_panel
    w._on_boot_seen("panic", "panic: kvmmap")
    log = w._log.toPlainText()
    assert log.startswith("Loaded, but the kernel did not boot") and "panic: kvmmap" in log
    w._log.setPlainText("Loaded — the machine is running your kernel.")
    w._on_boot_seen("booted", "")
    assert w._log.toPlainText() == "Loaded — the machine is running your kernel."
