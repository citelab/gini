"""A T-Lab 02 transcript says what the lab asked the student to do, not only the goals.

Reported 2026-11-21: T-Lab 02 transcripts in the Teaching Center were "quite minimal". They were —
a student who worked all four levels, some 370 actions, left nine entries, because only a goal
reached and Try every on a version that can be proved were recorded. The lab's own first question
("how many orders end at 5? Guess, then switch to Lines of C and guess again") is answered by Try
every on a version that cannot be proved, and left nothing; a level never opened read the same as
one tried and given up.

Now: each level opened, Try every on any version (once per version and code), and how many
schedules the student ran on each level — still never one entry per click.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain import grader_view as G
from gini.domain import narration as N
from gini.domain import proof as P
from gini.domain import proof_events as ev
from gini.domain import schedlab as S
from gini.domain.ticket import mint
from gini.domain.topology import Topology

from gini.services.proof_recorder import ProofRecorder
from gini.ui import sched_lab as V

CODE = mint(lambda n: bytes((i * 11 + 5) % 256 for i in range(n))).pretty


def _wired(tmp_path):
    """A recorder and a session joined the way `user_code_lab._tlab_event` joins them."""
    class Ctx:
        topology = Topology()
    rec = ProofRecorder(Ctx(), store=P.ChainStore(tmp_path))
    assert rec.arm(CODE)[0]

    def on_event(kind, p):
        if kind == "goal":
            rec.note_schedule(p["lab"], p["level"], p["version"], p["start"], p["code"],
                              p["moves"], p["goal"], p["result"], p["threads"], p["played"])
        elif kind == "proved":
            rec.note_proved(p["lab"], p["level"], p["version"], p["code"], p["ok"], p["reason"],
                            p["starts"], p["summary"], p["played"])
        elif kind == "explored":
            rec.note_explored(p["lab"], p["level"], p["version"], p["code"], p["starts"],
                              p["summary"], p["played"])
        elif kind == "level":
            rec.note_lab_open("", f"{p['lab']}/{p['level']}")
    return rec, V.Session(S.get("t-lab-02"), on_event=on_event)


def _entries(rec):
    return [{"kind": e.kind, "data": e.data, "t": e.t, "seq": e.seq}
            for e in rec._chain.entries]


def _level(s, level_id, version=""):
    s.pick_level(next(i for i, lv in enumerate(s.lab.levels) if lv.id == level_id))
    if version:
        s.pick_version(version)


def test_the_counting_question_is_recorded_with_its_counts_once(tmp_path):
    rec, s = _wired(tmp_path)
    s.pick_version("line")
    s.explore_all()
    s.pick_version("instr")
    s.explore_all()
    s.explore_all()                                        # again, same code: nothing new
    got = [e for e in _entries(rec) if e["kind"] == ev.EXPLORED]
    assert [e["data"]["version"] for e in got] == ["line", "instr"]
    assert got[1]["data"]["summary"]["0"]["total"] == 20
    line = N.narrate(rec._chain.entries)
    assert "level lost (instr) — 20 schedule(s): 2 ends at 5, 9 ends at 4, 9 ends at 6" in line


def test_each_level_opened_is_recorded_once(tmp_path):
    rec, s = _wired(tmp_path)
    _level(s, "rdv")
    _level(s, "buffer")
    _level(s, "rdv")
    faces = [e["data"]["face"] for e in _entries(rec) if e["kind"] == ev.LAB_OPEN]
    assert faces == ["t-lab-02/rdv", "t-lab-02/buffer"]
    assert "On t-lab-02, opened level rdv." in N.narrate(rec._chain.entries)


def test_schedules_run_are_counted_and_carried_on_the_next_entry(tmp_path):
    rec, s = _wired(tmp_path)
    s.show((0, 0, 0, 1, 1, 1))                             # ends at 5, no goal
    s.show((1, 1, 1, 0, 0, 0))                             # ends at 5, no goal
    s.show((0, 0, 1, 1, 0, 1))                             # ends at 4: the goal
    s.show((0, 0, 1, 1, 0, 1))                             # the same run again: not counted
    sched = next(e for e in _entries(rec) if e["kind"] == ev.SCHEDULE)
    assert sched["data"]["played"] == 3 and s.played == 3


def test_the_marker_and_the_student_see_it(tmp_path):
    from gini.ui.handin_preview import summary_lines
    rec, s = _wired(tmp_path)
    s.explore_all()
    s.show((0, 0, 1, 1, 0, 1))
    _level(s, "rdv")
    c = G.preview(_entries(rec), gini_lab="t-lab-02")
    lab = c["tlabs"]["labs"]["t-lab-02"]
    assert lab["opened"] == ["rdv"] and lab["played"] == {"lost": 1}
    assert lab["explored"][0]["outcomes"] == ["2 ends at 5", "9 ends at 4", "9 ends at 6"]
    lines = summary_lines(c)
    assert "Try every on lost (instr): 2 ends at 5, 9 ends at 4, 9 ends at 6" in lines
    assert "T-Lab 02: levels opened — rdv" in lines
    assert c["tlabs"]["proofs"] == 0, "an exploration is not a proof attempt"


def test_a_level_that_counts_no_schedules_shows_its_outcomes_without_zeros(tmp_path):
    rec, s = _wired(tmp_path)
    _level(s, "lockvar", "plain")
    s.explore_all()
    assert "level lockvar (plain) — 29 state(s): both finish." in N.narrate(rec._chain.entries)
    assert G.preview(_entries(rec))["tlabs"]["labs"]["t-lab-02"]["explored"][0]["outcomes"] == [
        "both finish"]


def test_the_server_does_not_call_an_exploration_unconfirmed():
    import pytest
    ACT = pytest.importorskip("gini_teaching_center.activities")
    kind, data = ev.explored("t-lab-02", "lost", "instr", [["p1"], ["c1"]], (0,), {})
    assert ACT.recheck_tlabs({"entries": [{"kind": kind, "data": data, "seq": 0}]}) == []


# --------------------------------------------------------------------------- #
# all nine goals, and a goal met while nothing was recording (reported 2026-11-21)
# --------------------------------------------------------------------------- #
# A marker saw "5 goal(s) reached" for a student whose chain held all nine of T-Lab 02's: the four
# proof goals are recorded as Try every verdicts, and only the played ones were counted. And a
# goal met before the code was entered, or while paused, lit its chip and never reached the
# chain, even when met again — the window kept "once per goal" for as long as it was open.

def _all_nine(s):
    s.show((0, 0, 1, 1, 0, 1)); s.show((1, 1, 0, 0, 1, 0))          # lost: ends at 4, at 6
    s.pick_version("locked"); s.explore_all()                        # lost: proof
    _level(s, "lockvar", "plain"); s.show((0, 1, 0, 1))              # both inside
    s.pick_version("tas"); s.explore_all()
    _level(s, "rdv", "bad"); s.show((0, 0, 1, 1))                    # deadlock
    s.pick_version("fix"); s.pick_tile(0, 1); s.move_sel(+1); s.explore_all()
    _level(s, "buffer", "swapped"); s.show((0, 0, 1, 1))             # deadlock
    s.pick_version("fix"); s.pick_tile(0, 0); s.move_sel(+1); s.explore_all()


def test_all_nine_goals_count_and_the_missing_are_named(tmp_path):
    from gini.ui.handin_preview import summary_lines
    rec, s = _wired(tmp_path)
    _all_nine(s)
    lab = G.preview(_entries(rec), gini_lab="t-lab-02")["tlabs"]["labs"]["t-lab-02"]
    assert (lab["goals"], lab["of"], lab["missing"]) == (9, 9, [])
    assert "T-Lab 02: 9 of 9 goals reached" in summary_lines(
        G.preview(_entries(rec), gini_lab="t-lab-02"))
    without_rdv = [e for e in _entries(rec) if e["data"].get("level") != "rdv"]
    lab = G.preview(without_rdv)["tlabs"]["labs"]["t-lab-02"]
    assert lab["goals"] == 7 and lab["missing"] == ["rdv · Deadlock", "rdv · Fix: never stuck"]


def test_a_failed_proof_reaches_no_goal(tmp_path):
    rec, s = _wired(tmp_path)
    _level(s, "rdv", "fix")
    s.explore_all()                                                  # not fixed: no schedule ends
    assert G.preview(_entries(rec))["tlabs"]["labs"]["t-lab-02"]["goals"] == 0


def test_a_goal_met_before_the_code_was_entered_is_recorded_when_met_again(tmp_path):
    class Ctx:
        topology = Topology()
    rec = ProofRecorder(Ctx(), store=P.ChainStore(tmp_path))
    recorded = []

    def on_event(kind, p):
        if kind == "goal":
            rec.note_schedule(p["lab"], p["level"], p["version"], p["start"], p["code"],
                              p["moves"], p["goal"], p["result"], p["threads"], p["played"])
    s = V.Session(S.get("t-lab-02"), on_event=on_event)
    _level(s, "rdv")
    s.show((0, 0, 1, 1))                                             # nothing armed yet
    assert s.goal_met("dl")
    assert rec.arm(CODE)[0]
    s.reset(); s.show((0, 0, 1, 1))
    s.reset(); s.show((0, 0, 1, 1))                                  # and again: still one
    recorded = [e for e in _entries(rec) if e["kind"] == ev.SCHEDULE]
    assert [e["data"]["goal"] for e in recorded] == ["dl"]


def test_t_lab_03_and_04_goals_are_once_per_recording_too(tmp_path):
    rec, _s = _wired(tmp_path)
    p = {"lab": "t-lab-03", "level": "graph", "start": 0, "version": "free",
         "moves": [[2, "step"]], "goal": "deadlock", "result": "deadlock: P1, P2, P3"}
    rec.note_tlab("allocate", p)
    rec.note_tlab("allocate", p)
    orders = {"lab": "t-lab-03", "level": "claims", "version": "graph", "start": 0, "ok": True}
    rec.note_tlab("orders", orders)
    rec.note_tlab("orders", orders)                                   # a verdict: every press
    kinds = [e["kind"] for e in _entries(rec) if e["kind"] in (ev.ALLOCATE, ev.ORDERS)]
    assert kinds == [ev.ALLOCATE, ev.ORDERS, ev.ORDERS]
