"""A T-Lab trace is a certificate, and the Teaching Center checks it.

The scheduler lab runs in GINI's own simulator, and the engine is pure and ships in gini-core —
which this server depends on. So a `schedule` entry can be REPLAYED here, on the same level file,
and a `proved` entry's code can be explored again; the chain's claim either holds or it does not.
That is the property the professor most wanted from building the lab as a simulator rather than
on xv6, and these tests are it: a chain the lab produced re-checks, a chain somebody edited by
hand does not, and both are REPORTED beside the row — never refused, exactly as `check_sources`
treats a mismatched file.

The chains here are made by driving `sched_lab.Session` with its own event hook, so what the
server re-checks is what the client really records, not a hand-written imitation of it.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
from gini.domain import proof_events as ev
from gini.domain import schedlab as S
from gini.ui import sched_lab as V

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))


def _chain(entries) -> dict:
    return {"entries": [{"kind": k, "seq": i, "t": float(i), "data": d}
                        for i, (k, d) in enumerate(entries)]}


def _recorded():
    """A session whose events become chain entries through the real event builders."""
    entries = []

    def on_event(kind, p):
        if kind == "goal":
            entries.append(ev.schedule(p["lab"], p["level"], p["version"], p["start"], p["code"],
                                       p["moves"], p["goal"], p["result"], p["threads"]))
        elif kind == "proved":
            entries.append(ev.proved(p["lab"], p["level"], p["version"], p["code"], p["ok"],
                                     p["reason"], p["starts"], p["summary"]))
    s = V.Session(S.get("t-lab-02"), on_event=on_event)
    return s, entries


def _level(s, level_id, version=""):
    s.pick_level(next(i for i, lv in enumerate(s.lab.levels) if lv.id == level_id))
    if version:
        s.pick_version(version)


# --------------------------------------------------------------------------- #
# the rows a marker reads
# --------------------------------------------------------------------------- #
def test_a_goal_schedule_and_a_proof_land_in_the_one_measurements_list():
    from gini_teaching_center import activities as ACT
    s, entries = _recorded()
    _level(s, "rdv")
    s.show((0, 0, 1, 1))                        # the deadlock
    s.pick_version("fix")
    s.explore_all()                             # not fixed yet
    s.pick_tile(0, 1); s.move_sel(+1)
    s.explore_all()                             # fixed
    rows = ACT.measurements(_chain(entries))
    assert [(r["kind"], r["name"], r["ok"], r["detail"]) for r in rows] == [
        ("schedule", "rdv · dl", True, "deadlock in 4 steps"),
        ("proof", "rdv · fix", False, "no schedule finishes"),
        ("proof", "rdv · fix", True, "proved from 1 start(s)")]
    assert ACT.measurement_tally(rows) == {"schedule": {"ok": 1, "pending": 0, "total": 1},
                                           "proof": {"ok": 1, "pending": 0, "total": 2}}


# --------------------------------------------------------------------------- #
# the re-check
# --------------------------------------------------------------------------- #
def test_what_the_lab_recorded_re_checks_on_the_server():
    from gini_teaching_center import activities as ACT
    s, entries = _recorded()
    _level(s, "lost", "instr")
    s.show((0, 0, 1, 1, 0, 1))                  # ends at 4
    s.pick_version("locked"); s.explore_all()   # proved
    _level(s, "buffer")
    s.show((0, 0, 1, 1))                        # the deadlock from full
    s.pick_version("fix"); s.pick_tile(0, 1); s.move_sel(-1); s.explore_all()
    rows = ACT.recheck_tlabs(_chain(entries))
    assert [(r["kind"], r["level"], r["claimed"], r["confirmed"], r["why"]) for r in rows] == [
        ("schedule", "lost", "ends at 4", True, ""),
        ("proof", "lost", "proved", True, ""),
        ("schedule", "buffer", "deadlock", True, ""),
        ("proof", "buffer", "proved", True, "")]


def test_a_failed_attempt_re_checks_too_because_it_is_part_of_the_story():
    from gini_teaching_center import activities as ACT
    s, entries = _recorded()
    _level(s, "buffer", "fix")
    s.pick_tile(0, 1); s.move_sel(-1)
    s.pick_tile(0, 3); s.move_sel(-1)           # the early release: fails from half full
    s.explore_all()
    r = ACT.recheck_tlabs(_chain(entries))[0]
    assert r["claimed"].startswith("not proved: ")
    assert r["confirmed"] is True


def test_a_hand_edited_schedule_does_not_re_check():
    """The moves changed after the fact: the replay no longer reaches what the entry claims."""
    from gini_teaching_center import activities as ACT
    s, entries = _recorded()
    _level(s, "rdv")
    s.show((0, 0, 1, 1))
    edited = copy.deepcopy(entries)
    edited[0][1]["moves"] = [0, 1]
    r = ACT.recheck_tlabs(_chain(edited))[0]
    assert r["confirmed"] is False
    assert r["why"] == "replaying the schedule does not reach the goal 'dl'"


def test_a_proof_whose_code_was_changed_afterwards_does_not_re_check():
    from gini_teaching_center import activities as ACT
    s, entries = _recorded()
    _level(s, "rdv", "fix")
    s.pick_tile(0, 1); s.move_sel(+1)
    s.explore_all()
    edited = copy.deepcopy(entries)
    edited[0][1]["code"] = [["a1", "wb", "sa", "a2"], ["b1", "wa", "sb", "b2"]]   # the broken one
    r = ACT.recheck_tlabs(_chain(edited))[0]
    assert r["confirmed"] is False
    assert r["why"] == "exploring this code does not prove it: no schedule finishes"


def test_a_fix_that_moved_a_pinned_tile_is_not_one_the_face_allows():
    """`a2` above the wait IS proved by the engine, which is exactly why the face pins it. A
    chain claiming that fix was not made with the face."""
    from gini_teaching_center import activities as ACT
    chain = _chain([ev.proved("t-lab-02", "rdv", "fix",
                              [["a1", "a2", "sa", "wb"], ["b1", "sb", "wa", "b2"]], True)])
    r = ACT.recheck_tlabs(chain)[0]
    assert r["confirmed"] is False
    assert r["why"] == "the fix moved 'a2', which the level pins"


def test_a_shipped_version_must_be_the_shipped_code():
    from gini_teaching_center import activities as ACT
    chain = _chain([ev.proved("t-lab-02", "lost", "locked",
                              [["acq", "p1", "p2", "p3", "rel"], ["c1", "c2", "c3"]], True)])
    r = ACT.recheck_tlabs(chain)[0]
    assert (r["confirmed"], r["why"]) == (False, "the code is not the level's 'locked' version")


def test_a_tile_the_level_does_not_have_is_caught_before_the_engine_sees_it():
    from gini_teaching_center import activities as ACT
    chain = _chain([ev.schedule("t-lab-02", "rdv", "bad", 0, [["zz"], ["b1"]], [0], "dl",
                                "deadlock")])
    r = ACT.recheck_tlabs(chain)[0]
    assert (r["confirmed"], r["why"]) == (False, "the code names a tile the level does not have")


def test_a_level_this_server_does_not_ship_is_not_checked_rather_than_failed():
    from gini_teaching_center import activities as ACT
    chain = _chain([ev.schedule("t-lab-09", "x", "v", 0, [["a"], ["b"]], [0, 1], "g", "r")])
    r = ACT.recheck_tlabs(chain)[0]
    assert r["confirmed"] is None
    assert "does not ship the level file" in r["why"]


def test_a_server_without_the_engine_says_so_instead_of_failing_the_student(monkeypatch):
    import builtins

    from gini_teaching_center import activities as ACT
    real = builtins.__import__

    def no_engine(name, *a, **k):
        if name == "gini.domain" and a and a[2] and "schedlab" in a[2]:
            raise ImportError("no schedlab here")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_engine)
    chain = _chain([ev.schedule("t-lab-02", "rdv", "bad", 0, [["a1"], ["b1"]], [0], "dl", "x")])
    r = ACT.recheck_tlabs(chain)[0]
    assert r["confirmed"] is None
    assert r["why"] == "this server's gini-core has no scheduler engine"


def test_a_chain_with_no_t_lab_work_has_no_re_check_rows():
    from gini_teaching_center import activities as ACT
    assert ACT.recheck_tlabs(_chain([ev.lab_open("M1", "Locks")])) == []


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #
def test_the_report_carries_the_re_checks_and_flags_the_ones_that_failed():
    import json

    from gini_teaching_center import activities as ACT
    s, entries = _recorded()
    _level(s, "rdv")
    s.show((0, 0, 1, 1))
    edited = copy.deepcopy(entries)
    edited[0][1]["moves"] = [1, 1]
    proof = _chain(edited)
    row = {"receipt": "R1", "activity": "a", "data": json.dumps({"proof": proof}),
           "started": 0, "finished": 60}
    rep = ACT.report(row, {"id": "a", "title": "T-Lab 02"}, [])
    assert rep["rechecks"][0]["confirmed"] is False
    assert any(x.startswith("T-Lab: the schedule on level rdv (bad) does not re-check")
               for x in rep["cooked"]["look_at"])
    assert rep["measurements"][0]["kind"] == "schedule"
