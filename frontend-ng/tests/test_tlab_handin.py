"""A T-Lab hand-in is about the T-Lab — not about an A-Lab that happens to be armed.

What students saw: they recorded T-Lab 02 under its own code with A-Lab 04 still armed on their
machine from earlier, pressed Hand in, and the preview — the marker's own card — said "Measured:
1/12 agreed — failed: stage A1", listed ten A-Lab 04 stages as "not run", and pointed the marker
at every one of them. Their T-Lab goals were nowhere on it. Two causes: hand-in graded every armed
A-Lab whatever the code was for, and the cooked view had no line for T-Lab work.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from gini.domain import grader_view as G
from gini.domain import lab_spec as L
from gini.domain import proof_events as ev

from gini.services import xv6_lab as X


def _e(built, t=0.0):
    kind, data = built
    return {"kind": kind, "data": data, "t": t, "seq": 0}


def _tlab_chain():
    return [_e(ev.lab_open("M1", "t-lab-02"), 1.0),
            _e(ev.schedule("t-lab-02", "race", "plain", 0, [], [0, 1], "lost", "1", ("A", "B")), 2.0),
            _e(ev.schedule("t-lab-02", "deadlock", "naive", 0, [], [0, 1], "deadlock", "deadlock",
                           ("A", "B")), 3.0),
            _e(ev.proved("t-lab-02", "deadlock", "fixed", [], True), 4.0),
            _e(ev.proved("t-lab-02", "race", "locked", [], False, "a schedule ends at 1"), 5.0)]


ALAB = L.get("a-lab-04")


# --------------------------------------------------------------------------- #
# which armed A-Lab the hand-in grades
# --------------------------------------------------------------------------- #

def test_an_armed_a_lab_is_not_graded_into_a_t_lab_hand_in():
    assert X.grade_at_hand_in(ALAB, _tlab_chain()) is False


def test_an_a_lab_hand_in_is_graded_even_with_no_load_in_it():
    """Edit, Stop, Run, hand in: nothing in the chain names the lab, and it is still graded."""
    assert X.grade_at_hand_in(ALAB, [_e(ev.lab_open("M1", "Process Scheduler"))]) is True
    assert X.grade_at_hand_in(ALAB, []) is True


@pytest.mark.parametrize("trace", [
    lambda: ev.build("M1", "a-lab-04", True),
    lambda: ev.tune("M1", "assignment", "", "a-lab-04"),
    lambda: ev.measure(f"{ALAB.title} · stage A1", {"ok": False}),
], ids=["a Load of it", "picking it", "a check of it"])
def test_t_lab_work_beside_real_work_on_the_a_lab_is_graded(trace):
    assert X.grade_at_hand_in(ALAB, [*_tlab_chain(), _e(trace())]) is True


def test_work_on_a_different_a_lab_does_not_count_for_this_one():
    other = _e(ev.build("M1", "a-lab-03", True))
    assert X.grade_at_hand_in(ALAB, [*_tlab_chain(), other]) is False


def test_hand_in_skips_the_armed_a_lab_and_says_so(tmp_path, monkeypatch):
    """Through the main window's own hand-in hook, with A-Lab 04 armed on a placed machine."""
    from PySide6.QtWidgets import QApplication

    from gini.ui.main_window import MainWindow
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "gini"))
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    dev = w.ctx.topology.add_device("xv6", "M1")
    monkeypatch.setattr(X, "active_spec", lambda name="": ALAB if name == dev.name else None)
    graded = []
    monkeypatch.setattr(X, "record_grades", lambda *a, **k: graded.append(a))
    monkeypatch.setattr(X, "check_stages", lambda *a, **k: "results")

    class Chain:
        entries = _tlab_chain()
    w.proof_recorder._chain = Chain()
    said = []
    w.ctx.bus.log.connect(lambda lvl, msg: said.append(msg))
    w._measure_armed_labs()
    assert graded == []
    assert any("not graded" in m for m in said)

    Chain.entries = [*_tlab_chain(), _e(ev.build("M1", "a-lab-04", True))]
    w._measure_armed_labs()
    assert len(graded) == 1

    # When the code's lab id names its GINI lab, that decides it, whatever the recording holds.
    graded.clear()
    w.proof_recorder._activity = "comp310/t-lab-02"
    w._measure_armed_labs()
    assert graded == []
    Chain.entries = _tlab_chain()
    w.proof_recorder._activity = "comp310/a-lab-04"
    w._measure_armed_labs()
    assert len(graded) == 1
    w.proof_recorder._activity = "comp310/c-lab-01"         # for no GINI lab: nothing graded
    graded.clear()
    w._measure_armed_labs()
    assert graded == []
    w.close()


# --------------------------------------------------------------------------- #
# the student sees their T-Lab work before handing in
# --------------------------------------------------------------------------- #

def test_the_cooked_view_counts_t_lab_goals_and_proofs():
    tl = G.preview(_tlab_chain())["tlabs"]
    assert tl["goals"] == 2 and (tl["proved"], tl["proofs"]) == (1, 2)
    assert tl["labs"]["t-lab-02"]["levels"] == ["race", "deadlock"]


def test_the_same_goal_reached_in_two_sittings_counts_once():
    chain = [*_tlab_chain(), _tlab_chain()[1]]
    assert G.preview(chain)["tlabs"]["goals"] == 2


def test_the_preview_says_what_the_t_lab_work_was():
    from gini.ui.handin_preview import summary_lines
    lines = summary_lines(G.preview(_tlab_chain()))
    assert any(x.startswith("T-Lab 02: 2 of 9 goals reached — not yet: ") for x in lines)
    assert "Try every: held 1 of 2 time(s)" in lines


def test_an_a_lab_chain_has_no_t_lab_line():
    assert "tlabs" not in G.preview([_e(ev.build("M1", "a-lab-04", True))])
