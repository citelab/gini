"""Does the student's system call tell the truth?

The wiring checklist answers "did you connect it up?" by pattern-matching source, and a student
can pass all sixteen of A-Lab 01's checks with a `sysinfo` that returns zero. These metrics answer
the other question, and every one has the same shape: **two readings of the same quantity, from
two places that cannot see each other.** Their program calls their system call and prints a
number; GINI reads the same quantity off the kernel's own structures over the serial dump path,
where the student's code cannot reach. Agreement is evidence.

The rule that matters most here is the one a wiring check already taught us the hard way: **a
metric that cannot be measured must not read as a pass.** `freemem` compared on an idle machine is
zero against zero, which a constant would satisfy — so that comes back "not measured", never
green. `test_a_constant_freemem_does_not_pass_on_an_idle_machine` is that lesson in test form.
"""
from __future__ import annotations

import pytest

from gini.domain import lab_grade as G
from gini.domain import lab_spec as L

GOOD = """free bytes:  33501184
processes:   3
runnable:    1
sleeping:    2
mem in use:  1048576
uptime:      120 ticks
load  5s: 0.40
load 15s: 0.20
load 30s: 0.10"""


def _spec():
    s = L.get("syscall-sysinfo")
    assert s is not None and s.grade, "A-Lab 01 must still declare its metrics"
    return s


def _item(metric: str, **kw):
    """One GradeItem, so a metric can be exercised without the whole assignment."""
    return L.GradeItem(id=metric, describe="", metric=metric, part="B", **kw)


def _one(metric: str, before, after=None, names=None, **kw):
    class _S:
        grade = (_item(metric, **kw),)
    return G.grade(_S(), before, after, names)[0]


# --------------------------------------------------------------------------- #
# reading what they printed
# --------------------------------------------------------------------------- #

def test_the_seeded_programs_output_is_understood():
    got = G.parse_report(GOOD.splitlines())
    assert got == {"freemem": 33501184.0, "nproc": 3.0, "nrunnable": 1.0, "nsleeping": 2.0,
                   "memused": 1048576.0, "uptime": 120.0,
                   "load5": 0.4, "load15": 0.2, "load30": 0.1}


def test_it_reads_the_label_not_the_line_number():
    """The file is theirs and they will reformat it. The handout fixes the labels."""
    got = G.parse_report(["sleeping: 9", "free bytes:     4096", "processes:2"])
    assert got == {"nsleeping": 9.0, "freemem": 4096.0, "nproc": 2.0}


def test_a_word_after_the_number_does_not_break_it():
    assert G.parse_report(["uptime:      41 ticks"])["uptime"] == 41.0


def test_lines_it_does_not_recognise_are_ignored_not_guessed_at():
    got = G.parse_report(["hello world", "", "my own note: 7", "free bytes: 8192"])
    assert got == {"freemem": 8192.0}


def test_nothing_in_gives_nothing_out():
    assert G.parse_report([]) == {} and G.parse_report(None) == {}


# --------------------------------------------------------------------------- #
# the metrics, when they can be measured
# --------------------------------------------------------------------------- #

def test_a_correct_submission_passes_every_metric():
    before = G.Reading(G.parse_report(GOOD.splitlines()),
                       free_pages=8179, procs=3, runnable=1, sleeping=2, ticks=120)
    after = G.Reading(G.parse_report(
        "free bytes: 33419264\nprocesses: 5\nrunnable: 3\nsleeping: 2\n"
        "uptime: 180 ticks\nload  5s: 1.90\nload 30s: 0.30".splitlines()),
        free_pages=8159, procs=5, runnable=3, sleeping=2, ticks=180)
    res = G.grade(_spec(), before, after, {23: "sysinfo"})
    assert G.tally(res) == {"ok": 7, "failed": 0, "pending": 0, "total": 7}


def test_freemem_in_bytes_is_compared_against_pages():
    """The commonest way to build a metric that never agrees: the assignment's header reports
    BYTES and GINI counts PAGES."""
    r = G.Reading({"freemem": 8192.0}, free_pages=2)
    assert _one("sysinfo_freemem_vs_free_pages", r, tolerance=0).ok is True


def test_a_syscall_that_returns_a_constant_is_caught():
    before = G.Reading({"freemem": 4096.0}, free_pages=8000, ticks=10)
    after = G.Reading({"freemem": 4096.0}, free_pages=7900, ticks=20)
    out = _one("sysinfo_freemem_delta_vs_free_pages_delta", before, after, tolerance=2)
    assert out.ok is False
    assert "0 pages" in out.summary and "-100" in out.summary


def test_a_process_count_that_disagrees_by_one_fails_at_zero_tolerance():
    r = G.Reading({"nproc": 4.0}, procs=3)
    assert _one("sysinfo_nproc_vs_procs", r, tolerance=0).ok is False


def test_states_report_the_worst_of_the_two():
    r = G.Reading({"nrunnable": 1.0, "nsleeping": 7.0}, runnable=1, sleeping=2)
    out = _one("sysinfo_states_vs_procs", r, tolerance=1)
    assert out.ok is False and out.detail["off_by"] == 5


def test_uptime_that_never_moves_fails_even_though_the_kernels_did():
    before = G.Reading({"uptime": 50.0}, ticks=100)
    after = G.Reading({"uptime": 50.0}, ticks=160)
    out = _one("sysinfo_uptime_advances", before, after, tolerance=1)
    assert out.ok is False and "did not move" in out.summary


def test_the_thirty_second_average_must_lag_the_five():
    before = G.Reading({"load5": 0.1}, runnable=1)
    after = G.Reading({"load5": 2.0, "load30": 3.0}, runnable=4)
    out = _one("sysinfo_load_responds", before, after)
    assert out.ok is False and "wrong way round" in out.summary


def test_the_call_has_to_be_named_what_the_assignment_asked():
    r = G.Reading({})
    assert _one("syscall_named", r, names={23: "sysinfo"}, expect="sysinfo").ok is True
    wrong = _one("syscall_named", r, names={23: "sysinf"}, expect="sysinfo")
    assert wrong.ok is False and "sysinf" in wrong.summary


# --------------------------------------------------------------------------- #
# ...and when they cannot. NONE of these may read as a pass.
# --------------------------------------------------------------------------- #

def test_a_constant_freemem_does_not_pass_on_an_idle_machine():
    """THE guard. Nothing allocated means zero against zero, and a `sysinfo` returning a constant
    satisfies that. The same shape as a wiring check that is green before the student starts —
    worse than no check at all."""
    before = G.Reading({"freemem": 4096.0}, free_pages=8000)
    after = G.Reading({"freemem": 4096.0}, free_pages=8000)
    out = _one("sysinfo_freemem_delta_vs_free_pages_delta", before, after, tolerance=2)
    assert out.ok is None, "a constant passed on an idle machine"
    assert out.pending is True
    assert "nothing was allocated" in out.summary


def test_a_load_average_is_not_judged_if_nothing_became_runnable():
    before = G.Reading({"load5": 0.1}, runnable=2)
    after = G.Reading({"load5": 0.1}, runnable=2)
    assert _one("sysinfo_load_responds", before, after).ok is None


def test_uptime_is_not_judged_if_the_kernel_tick_did_not_advance():
    before = G.Reading({"uptime": 5.0}, ticks=100)
    after = G.Reading({"uptime": 5.0}, ticks=100)
    assert _one("sysinfo_uptime_advances", before, after).ok is None


def test_a_line_their_program_never_printed_is_not_measured():
    r = G.Reading({}, free_pages=8000, procs=3)
    for metric in ("sysinfo_freemem_vs_free_pages", "sysinfo_nproc_vs_procs",
                   "sysinfo_states_vs_procs"):
        out = _one(metric, r)
        assert out.ok is None, metric
        assert "did not print" in out.summary or "neither" in out.summary


def test_a_quantity_gini_could_not_read_is_not_measured():
    r = G.Reading({"freemem": 4096.0, "nproc": 3.0}, free_pages=None, procs=None)
    assert _one("sysinfo_freemem_vs_free_pages", r).ok is None
    assert _one("sysinfo_nproc_vs_procs", r).ok is None


def test_one_reading_leaves_the_delta_metrics_unmeasured_and_the_rest_measured():
    """Grading with a single reading is legitimate — it just cannot see anything move."""
    before = G.Reading(G.parse_report(GOOD.splitlines()),
                       free_pages=8179, procs=3, runnable=1, sleeping=2, ticks=120)
    res = {r.id: r for r in G.grade(_spec(), before, None, {23: "sysinfo"})}
    assert res["freemem_tracks"].ok is None and res["uptime_agrees"].ok is None
    assert res["load_responds"].ok is None
    assert res["nproc_agrees"].ok is True and res["freemem_plausible"].ok is True


def test_a_metric_nobody_implemented_is_never_a_green_tick():
    """An assignment can declare a metric before the measurement exists. Reporting that as a
    pass would credit work nothing checked."""
    out = _one("no_such_metric_yet", G.Reading({}))
    assert out.ok is None and "no measurement is implemented" in out.summary


def test_one_metric_blowing_up_does_not_lose_the_others():
    class _Bad:
        grade = (_item("sysinfo_nproc_vs_procs"), _item("sysinfo_freemem_vs_free_pages"))

    class _Explodes(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    res = G.grade(_Bad(), G.Reading(_Explodes(), procs=3, free_pages=1))
    assert len(res) == 2
    assert all(r.ok is None for r in res)
    assert "boom" in res[0].summary


# --------------------------------------------------------------------------- #
# what a marker is handed
# --------------------------------------------------------------------------- #

def test_the_numbers_travel_with_the_verdict():
    """A metric is not a mark. "out by three pages" is what lets a teacher decide what it is
    worth, and a bare pass/fail would hide it."""
    before = G.Reading({"freemem": 4096.0 * 100}, free_pages=103)
    out = _one("sysinfo_freemem_vs_free_pages", before, tolerance=0)
    assert out.ok is False
    assert out.detail == {"student_pages": 100.0, "gini_pages": 103, "off_by": 3.0}
    assert "out by 3" in out.summary


def test_nothing_here_computes_a_score():
    res = G.grade(_spec(), G.Reading({}), None, {})
    flat = repr(res) + repr(G.tally(res))
    for word in ("score", "grade=", "percent", "mark"):
        assert word not in flat.lower(), f"{word!r} appeared in something that only measures"


def test_the_tally_separates_unmeasured_from_failed():
    """Three of five failing and three of five never running are different submissions."""
    made = [G.GradeResult("a", "A", "", "m", True, ""), G.GradeResult("b", "A", "", "m", False, ""),
            G.GradeResult("c", "A", "", "m", None, "")]
    assert G.tally(made) == {"ok": 1, "failed": 1, "pending": 1, "total": 3}
    assert G.tally([]) == {"ok": 0, "failed": 0, "pending": 0, "total": 0}


def test_every_metric_the_shipped_assignment_declares_is_implemented():
    """A-Lab 01 declares seven. If one has no measurement behind it, a student passes it by
    default and nobody finds out until marking."""
    unimplemented = [g.metric for g in _spec().grade if g.metric not in G._METRICS]
    assert not unimplemented, f"declared but not measured: {unimplemented}"


# --------------------------------------------------------------------------- #
# taking the readings — the impure half
#
# Two readings with REAL WORK between them, because half the metrics are about movement. The
# workload is `alloc N`, which grows the heap touching each page so it genuinely faults in, and
# then spins — so it moves the free list AND stays runnable, which is what both the delta metrics
# and the load average need.
# --------------------------------------------------------------------------- #

def _raw(lines):
    """What a console delta actually looks like: the shell's echo, the output, the prompt."""
    return "sysinfotest\r\n" + "".join(f"{ln}\r\n" for ln in lines) + "$ "


BEFORE = _raw(["free bytes:  33501184", "processes: 3", "runnable: 1", "sleeping: 2",
               "uptime: 120 ticks", "load  5s: 0.40", "load 30s: 0.10"])
AFTER = _raw(["free bytes:  33419264", "processes: 5", "runnable: 3", "sleeping: 2",
              "uptime: 180 ticks", "load  5s: 1.90", "load 30s: 0.30"])


class _Proc:
    def __init__(self, pid, state, name):
        self.pid, self.state, self.name = pid, state, name


class _Snap:
    def __init__(self, procs, ticks):
        self.procs, self.ticks = procs, ticks


class _Vm:
    def __init__(self, free_pages):
        self.free_pages = free_pages


class _Provider:
    """A kernel that allocates when told to, and answers honestly about it."""
    last_run_error = ""

    def __init__(self, before=BEFORE, after=AFTER, runs=True):
        self.before, self.after, self.runs = before, after, runs
        self.reads, self.stirred, self.killed, self.launched = 0, False, [], []

    def run_and_capture(self, prog, args=""):
        self.reads += 1
        return True, (self.before if self.reads == 1 else self.after)

    def run(self, prog, args="", foreground=False):
        self.launched.append((prog, args))
        if prog == "alloc":
            self.stirred = self.runs
        return self.runs

    def kill(self, pid):
        self.killed.append(pid)

    def snapshot(self):
        procs = [_Proc(1, "sleeping", "init"), _Proc(2, "sleeping", "sh"),
                 _Proc(3, "runnable", "x")]
        if self.stirred:
            procs += [_Proc(4, "runnable", "alloc"), _Proc(5, "runnable", "y")]
        return _Snap(procs, 180 if self.stirred else 120)


class _VmReader:
    def __init__(self, provider):
        self.p = provider

    def snapshot(self):
        return _Vm(8159 if self.p.stirred else 8179)


@pytest.fixture
def armed(tmp_path, monkeypatch):
    from gini.services import xv6_lab as X
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    monkeypatch.delenv("GINI_LAB", raising=False)
    spec = _spec()
    X.arm("M1", spec.id)
    X.seed_host_files(spec, "M1")
    (X.lab_dir("M1", spec.id) / "syscall.h").write_text(
        "#define SYS_sync 22\n#define SYS_sysinfo 23\n")
    return spec


def test_a_reading_pairs_their_output_with_ginis_own_numbers(armed):
    from gini.services import xv6_lab as X
    prov = _Provider()
    r = X.take_reading(armed, "M1", prov, _VmReader(prov))
    assert r.said("freemem") == 33501184.0          # theirs
    assert r.free_pages == 8179 and r.procs == 3     # GINI's, read independently
    assert r.runnable == 1 and r.sleeping == 2 and r.ticks == 120


def test_a_whole_grading_run_measures_every_metric(armed):
    from gini.services import xv6_lab as X
    prov = _Provider()
    res = X.grade_now(armed, "M1", prov, _VmReader(prov))
    assert G.tally(res) == {"ok": 7, "failed": 0, "pending": 0, "total": 7}


def test_the_workload_is_let_go_afterwards(armed):
    """`alloc` spins forever by design. Leaving it running would quietly skew every later
    reading the student takes, and they would have no idea why."""
    from gini.services import xv6_lab as X
    prov = _Provider()
    X.grade_now(armed, "M1", prov, _VmReader(prov))
    assert prov.launched[0] == ("alloc", str(X.STIR_PAGES))
    assert prov.killed == [4], "the allocator was left running"


def test_a_machine_that_will_not_run_the_workload_reports_honestly(armed):
    """No allocation means nothing for freemem to track. That is "not measured", never a fail."""
    from gini.services import xv6_lab as X
    prov = _Provider(after=BEFORE, runs=False)
    res = {r.id: r for r in X.grade_now(armed, "M1", prov, _VmReader(prov))}
    assert res["freemem_tracks"].ok is None
    assert res["nproc_agrees"].ok is True, "the metrics that need no movement still measured"


def test_half_a_reading_is_still_a_reading(armed):
    """A machine that stopped answering still leaves what the program printed, and vice versa."""
    from gini.services import xv6_lab as X

    class _Mute(_Provider):
        def snapshot(self):
            raise RuntimeError("machine is gone")

    prov = _Mute()
    r = X.take_reading(armed, "M1", prov, None)
    assert r.said("nproc") == 3.0 and r.procs is None and r.free_pages is None


def test_an_assignment_with_no_metrics_grades_to_nothing(armed):
    from gini.services import xv6_lab as X
    assert X.grade_now(None, "M1", _Provider(), None) == ()

    class _NoGrade:
        grade = ()
        test_prog = "x"
    assert X.grade_now(_NoGrade(), "M1", _Provider(), None) == ()
