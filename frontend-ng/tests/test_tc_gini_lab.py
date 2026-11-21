"""A course's lab id names its GINI lab, and hand-in checks that lab and no other.

A code is minted for one Teaching Center lab, and its questions travel with it, so a student always
got the right questions. The checks do not: A-Lab 04's stages are gBuilder's. So at hand-in
gBuilder graded every A-Lab armed on the student's machines — a student with A-Lab 03 on M1 and
A-Lab 04 on M2 had both graded into one hand-in, and one with A-Lab 04 armed from earlier had it
graded, as failures, into T-Lab 02.

The course names its labs after GINI's ("t-lab-02", "a-lab-01a"), so the lab id is the link, read
by the same gini-core rule on both sides. 6.22.16 also added a "GINI lab" menu to the lab editor —
a second place to say it, never set, silently winning where the two disagreed — and 6.22.20
removed it. A lab id that names no GINI lab ("c-lab-01") is for none, and no A-Lab is graded in.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from gini.domain import gini_labs
from gini.domain import grader_view as G
from gini.domain import lab_spec as L
from gini.domain import proof_events as ev

from gini.services import xv6_lab as X

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from test_tc_handin import tc  # noqa: F401  (a fixture)
from test_tc_questions import HOUR, QS, _rc, course  # noqa: F401  (course is a fixture)

needs_tc = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")

A03, A04 = L.get("a-lab-03"), L.get("a-lab-04")


def _e(built):
    kind, data = built
    return {"kind": kind, "data": data, "t": 0.0, "seq": 0}


# --------------------------------------------------------------------------- #
# the list of GINI labs
# --------------------------------------------------------------------------- #

def test_every_a_lab_and_released_t_lab_can_be_chosen():
    ids = [c["id"] for c in gini_labs.choices()]
    assert {"a-lab-03", "a-lab-04", "t-lab-02", "t-lab-03", "t-lab-04"} <= set(ids)
    assert gini_labs.kind("a-lab-04") == gini_labs.A_LAB
    assert gini_labs.kind("t-lab-02") == gini_labs.T_LAB and gini_labs.kind("lab4") == ""


def test_the_lab_id_is_the_link():
    assert gini_labs.resolve("t-lab-02") == "t-lab-02"
    assert gini_labs.resolve("a-lab-03") == "a-lab-03"
    assert gini_labs.resolve("A-Lab-04") == "a-lab-04"


def test_a_second_run_with_a_suffix_is_the_same_lab():
    assert gini_labs.resolve("a-lab-01a") == "syscall-sysinfo"
    assert gini_labs.resolve("t-lab-02-late") == "t-lab-02"
    assert gini_labs.resolve("a-lab-03b") == "a-lab-03"


def test_a_lab_01_is_reached_by_the_courses_name_for_it():
    """Its own id predates the naming, and students' armed state is keyed by it."""
    assert gini_labs.resolve("a-lab-01") == "syscall-sysinfo"
    assert gini_labs.title(gini_labs.resolve("a-lab-01")).startswith("A-Lab 01")


def test_an_id_that_names_no_gini_lab_is_for_none():
    for lab in ("c-lab-01", "c-lab-02", "lab4", "", "a-lab-010", "t-lab-021"):
        assert gini_labs.resolve(lab) == "", lab


# --------------------------------------------------------------------------- #
# hand-in: which armed A-Lab is graded
# --------------------------------------------------------------------------- #

def test_with_two_a_labs_armed_only_the_codes_lab_is_graded():
    chain = [_e(ev.build("M1", "a-lab-03", True)), _e(ev.build("M2", "a-lab-04", True))]
    assert X.grade_at_hand_in(A04, chain, "a-lab-04") is True
    assert X.grade_at_hand_in(A03, chain, "a-lab-04") is False


def test_a_t_lab_code_grades_no_a_lab_even_one_worked_on_in_the_same_recording():
    chain = [_e(ev.build("M1", "a-lab-04", True))]
    assert X.grade_at_hand_in(A04, chain, "t-lab-02") is False


def test_a_code_for_no_gini_lab_grades_no_a_lab():
    """A C-Lab code with A-Lab 04 still armed: the original mixup, closed for good."""
    assert X.grade_at_hand_in(A04, [_e(ev.build("M1", "a-lab-04", True))], "") is False


def test_a_code_whose_lab_is_not_known_yet_falls_back_to_the_recording():
    """Armed offline, its lab id never arrived."""
    assert X.grade_at_hand_in(A04, [], None) is True
    assert X.grade_at_hand_in(A04, [_e(ev.lab_open("M1", "t-lab-02"))], None) is False


# --------------------------------------------------------------------------- #
# what the student and the marker are shown
# --------------------------------------------------------------------------- #

def _measured(title, ok):
    return _e(ev.measure(f"{title} · stage A1", {"ok": ok}))


def test_the_view_reads_only_the_codes_lab_checks():
    chain = [_measured(A03.title, False), _measured(A04.title, True)]
    c = G.preview(chain, gini_lab="a-lab-04")
    assert (c["grading"]["passed"], c["grading"]["total"]) == (1, 1)
    assert c["lab"]["title"] == A04.title
    assert not any("nothing in this recording checks" in x for x in c["look_at"])


def test_an_a_lab_code_that_was_never_checked_says_so_before_hand_in():
    c = G.preview([_measured(A03.title, True)], gini_lab="a-lab-04")
    assert "grading" not in c
    assert any(f"This code is for {A04.title}, and nothing in this recording checks it" in x
               for x in c["look_at"])


def test_a_t_lab_code_shows_no_a_lab_grading_and_says_when_no_goal_was_reached():
    c = G.preview([_measured(A04.title, False)], gini_lab="t-lab-02")
    assert "grading" not in c
    assert any("no T-Lab 02 goal was reached" in x for x in c["look_at"])
    reached = _e(ev.schedule("t-lab-02", "race", "plain", 0, [], [0], "lost", "1", ("A",)))
    assert not G.preview([reached], gini_lab="t-lab-02")["look_at"]


def test_the_preview_names_the_lab_first():
    from gini.ui.handin_preview import summary_lines
    assert summary_lines(G.preview([], gini_lab="a-lab-04"))[0] == f"Lab: {A04.title}"


def test_a_code_for_no_gini_lab_shows_no_a_lab_checks_or_loads():
    chain = [_measured(A04.title, False), _e(ev.build("M1", "a-lab-04", True)),
             _e(ev.measure("rtt h1→h2", {"ok": True}))]
    c = G.preview(chain, gini_lab="")
    assert c["grading"]["total"] == 1 and "builds" not in c and "lab" not in c
    assert any("not part of this lab: 1 Load (A-Lab 04)" in x for x in c["look_at"])


def test_with_the_lab_not_known_the_view_is_what_it_was():
    chain = [_measured(A03.title, False), _measured(A04.title, True)]
    c = G.preview(chain)
    assert c["grading"]["total"] == 1 and "lab" not in c      # one metric name, last reading wins


# --------------------------------------------------------------------------- #
# the recorder keeps it per code
# --------------------------------------------------------------------------- #

def test_arming_a_new_code_forgets_the_previous_codes_lab(tmp_path):
    from gini.domain import proof as P
    from gini.domain import ticket as T
    from gini.domain.topology import Topology

    from gini.services.proof_recorder import ProofRecorder

    class Ctx:
        topology = Topology()
    r = ProofRecorder(Ctx(), store=P.ChainStore(tmp_path))
    assert r.arm(T.mint().pretty)[0]
    assert r.gini_lab is None
    r.note_activity("comp310/a-lab-04", "Sems")
    assert r.gini_lab == "a-lab-04"
    r.note_activity("comp310/c-lab-01", "Catching the kernel")
    assert r.gini_lab == ""
    assert r.arm(T.mint().pretty)[0]                     # offline: no reply follows
    assert r.gini_lab is None and r.activity_title == ""


# --------------------------------------------------------------------------- #
# the Teaching Center, over the wire
# --------------------------------------------------------------------------- #

def _save(call, tok, **extra):
    body = {"course": "comp535", "lab": "lab1", "title": "Multi-LAN",
            "vend_until": time.time() + HOUR, "questions": QS, "show_n": 2, **extra}
    return call("/api/activities/save", body, tok)


def _row(call, tok, lab="lab1"):
    return next(x for x in call("/api/activities?course=comp535", session=tok) if x["lab"] == lab)


@needs_tc
def test_the_list_shows_which_gini_lab_each_lab_id_names(course):
    _url, tok, call = course
    for lab in ("t-lab-02", "a-lab-01a", "c-lab-01"):
        call("/api/activities/save", {"course": "comp535", "lab": lab, "title": lab,
                                      "vend_until": time.time() + HOUR}, tok)
    got = {x["lab"]: x["gini_lab_title"]
           for x in call("/api/activities?course=comp535", session=tok)}
    assert got["t-lab-02"] == "T-Lab 02" and got["a-lab-01a"] == "A-Lab 01"
    assert got["c-lab-01"] == "" and got["lab1"] == ""


@needs_tc
def test_there_is_no_menu_any_more_and_an_old_consoles_choice_is_ignored(course):
    _url, tok, call = course
    assert _save(call, tok, gini_lab="a-lab-04")["ok"]       # a console from 6.22.16-19
    assert _row(call, tok)["gini_lab"] == ""                  # "lab1" names none
    assert "error" in (call("/api/gini_labs", session=tok) or {"error": 1})


@needs_tc
def test_the_code_carries_the_gini_lab_its_lab_id_names(course):
    """For gBuilders 6.22.16-6.22.19, which read it from the reply."""
    _url, tok, call = course
    call("/api/activities/save", {"course": "comp535", "lab": "a-lab-04", "title": "Sems",
                                  "vend_until": time.time() + HOUR, "questions": QS,
                                  "show_n": 1}, tok)
    call("/api/activities/release", {"course": "comp535", "lab": "a-lab-04"}, tok)
    vend = call("/api/activity?course=comp535&lab=a-lab-04&rc=" + _rc("a-lab-04"))
    assert vend["gini_lab"] == "a-lab-04"
    code = vend["code"].replace("-", "").replace(" ", "")
    assert call(f"/api/activity?code={code}")["gini_lab"] == "a-lab-04"


# --------------------------------------------------------------------------- #
# what was still mixed up after 6.22.16 (reported 2026-11-21)
# --------------------------------------------------------------------------- #
# The course's labs are named for their GINI labs ("t-lab-02"), so every code already said which
# lab it was for. Two things still mixed them: the class view read its rows without the lab, and
# every view read kernel work done under a T-Lab code (Loads, runs) as that T-Lab's.

def _k(built, t):
    kind, data = built
    return {"kind": kind, "data": data, "t": float(t), "seq": 0}


def test_a_t_lab_view_leaves_out_kernel_work_done_under_its_code_and_says_so():
    chain = [_k(ev.schedule("t-lab-02", "lost", "instr", 0, [], [0], "g4", "ends at 4",
                            ("p", "c")), 1),
             _k(ev.build("M1", "a-lab-04", True), 2), _k(ev.spawn("M1", "semtest"), 3)]
    c = G.preview(chain, gini_lab="t-lab-02")
    assert "builds" not in c and "tests" not in c and c["tlabs"]["goals"] == 1
    assert c["other_work"]["loads"] == 1 and c["other_work"]["labs"] == ["A-Lab 04"]
    assert any(x.startswith("Also recorded under this code, and not part of T-Lab 02")
               for x in c["look_at"])
    assert "builds" in G.preview(chain)                       # no lab known: as it was


def test_an_a_lab_view_reads_only_its_own_loads():
    chain = [_k(ev.build("M1", "a-lab-03", True), 1), _k(ev.build("M2", "a-lab-04", False), 2)]
    c = G.preview(chain, gini_lab="a-lab-03")
    assert c["builds"]["loads"] == 1 and c["builds"]["last_ok"]
    assert c["other_work"]["labs"] == ["A-Lab 04"]


def _old_t_lab_hand_in(code, topo):
    """What a gBuilder before 6.22.15 sent for a T-Lab: the armed A-Lab's stages at the end."""
    from gini.domain import proof as P
    from gini_teaching_center import activities as ACT
    t0 = time.time()
    chain = P.Chain.start(ACT.normalize(code), assignment="comp310/t-lab-02",
                          gini_version="6.22.8", t=t0)
    for i, built in enumerate([
            ev.schedule("t-lab-02", "lost", "instr", 0, [], [0], "g4", "ends at 4", ("p", "c")),
            ev.build("M1", "a-lab-04", True),
            ev.measure(f"{A04.title} · stage A1", {"ok": False, "summary": "failed"})]):
        chain.append(*built, t=t0 + 60 * (i + 1))
    chain.append("submit", {"artifact": P.artifact_summary(topo)}, t=t0 + 300)
    return P.build_proof(chain)


@needs_tc
def test_a_lab_named_t_lab_02_shows_only_t_lab_02_in_the_receipt_and_the_class_view(tc):
    from test_tc_handin import _call, a_topology
    from test_tc_handin import _rc as release_code

    from gini.services import tc_submit
    _call(tc.url, "/api/activities/save",
          {"course": "comp535", "lab": "t-lab-02", "title": "You are the scheduler",
           "vend_until": time.time() + HOUR, "session_minutes": 60}, tc.token)
    _call(tc.url, "/api/activities/release", {"course": "comp535", "lab": "t-lab-02"}, tc.token)
    code = _call(tc.url, "/api/activity?course=comp535&lab=t-lab-02&rc=" + release_code("t-lab-02"))["code"]
    topo = a_topology()
    r = tc_submit.submit(tc.url, code, _old_t_lab_hand_in(code, topo), topo)
    assert r["ok"], r
    rep = tc.staff("/api/receipt?receipt=" + r["receipt"])
    assert rep["cooked"]["lab"]["id"] == "t-lab-02" and rep["gini_version"] == "6.22.8"
    assert "grading" not in rep["cooked"] and "builds" not in rep["cooked"]
    table = tc.staff("/api/activity/table?course=comp535&lab=t-lab-02")
    assert table["metrics"] == [] and table["rows"][0]["loads"] == 0
