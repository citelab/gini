"""A course's lab names its GINI lab, and hand-in checks that lab and no other.

A code is minted for one Teaching Center lab, and its questions travel with it, so a student always
got the right questions. The checks do not: A-Lab 04's stages are gBuilder's, and the Teaching
Center lab never said which GINI lab it was (its id is whatever the teacher typed, "lab4"). So
at hand-in gBuilder graded every A-Lab armed on the student's machines — a student with A-Lab 03
on M1 and A-Lab 04 on M2 had both graded into one hand-in, and one with A-Lab 04 armed from
earlier had it graded, as failures, into T-Lab 02.

Now the lab names its GINI lab in the console, the arm reply carries it like the questions, and
the hand-in grades that lab; the cooked view reads that lab's checks and says when it was never
checked. A lab that has not been told still works as before.
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


def test_the_teachers_choice_wins_then_a_lab_id_that_is_a_gini_lab_id():
    assert gini_labs.resolve("a-lab-04", "lab4") == "a-lab-04"
    assert gini_labs.resolve("", "t-lab-02") == "t-lab-02"
    assert gini_labs.resolve("", "lab4") == ""
    assert gini_labs.resolve("nonsense", "lab4") == ""


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


def test_a_code_whose_course_did_not_say_falls_back_to_the_recording():
    assert X.grade_at_hand_in(A04, [], "") is True
    assert X.grade_at_hand_in(A04, [_e(ev.lab_open("M1", "t-lab-02"))], "") is False


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


def test_with_no_lab_named_the_view_is_what_it_was():
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
    r.note_activity("comp310/lab4", "A-Lab 04", gini_lab="a-lab-04")
    assert r.gini_lab == "a-lab-04"
    assert r.arm(T.mint().pretty)[0]                     # offline: no reply follows
    assert r.gini_lab == ""


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
def test_the_console_lists_the_gini_labs(course):
    _url, tok, call = course
    ids = [g["id"] for g in call("/api/gini_labs", session=tok)]
    assert "a-lab-04" in ids and "t-lab-02" in ids


@needs_tc
def test_the_teacher_sets_it_and_an_older_console_leaves_it_alone(course):
    _url, tok, call = course
    assert _save(call, tok, gini_lab="a-lab-04")["ok"]
    assert _row(call, tok)["gini_lab"] == "a-lab-04"
    _save(call, tok)                                           # no gini_lab at all
    assert _row(call, tok)["gini_lab"] == "a-lab-04"
    _save(call, tok, gini_lab="")
    assert _row(call, tok)["gini_lab"] == ""


@needs_tc
def test_a_lab_this_server_does_not_know_is_refused(course):
    _url, tok, call = course
    r = _save(call, tok, gini_lab="a-lab-99")
    assert not r["ok"] and "GINI lab" in r["error"]


@needs_tc
def test_the_code_carries_its_gini_lab_like_its_questions(course):
    _url, tok, call = course
    _save(call, tok, gini_lab="t-lab-02")
    vend = call("/api/activity?course=comp535&lab=lab1&rc=" + _rc())
    code = vend["code"].replace("-", "").replace(" ", "")
    armed = call(f"/api/activity?code={code}")
    assert armed["ok"] and armed["gini_lab"] == "t-lab-02" and armed["questions"]
    assert vend["gini_lab"] == "t-lab-02"


@needs_tc
def test_a_lab_whose_id_is_a_gini_lab_id_needs_no_setting(course):
    _url, tok, call = course
    call("/api/activities/save", {"course": "comp535", "lab": "a-lab-04", "title": "Sems",
                                  "vend_until": time.time() + HOUR, "questions": QS,
                                  "show_n": 1}, tok)
    call("/api/activities/release", {"course": "comp535", "lab": "a-lab-04"}, tok)
    vend = call("/api/activity?course=comp535&lab=a-lab-04&rc=" + _rc("a-lab-04"))
    assert vend["gini_lab"] == "a-lab-04"
