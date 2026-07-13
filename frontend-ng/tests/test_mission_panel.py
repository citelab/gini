"""The Missions panel renders a mission's live objective tracker, clock, lives, and — once
witnessed — a band badge. Offscreen Qt (no model needed for the panel mechanics)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.agent.mission import Mission
from gini.domain import lesson as L, objectives as O
from gini.domain.topology import Topology
from gini.ui.mission_panel import MissionPanel, _fmt_clock


def _app():
    return QApplication.instance() or QApplication([])


def _lesson():
    return L.from_archetype("basic-lan", {"h1": "M1", "h2": "M2", "sw": "S1", "gw": "R1"},
                            id="lab01", title="A basic switched LAN", time_limit="20m")


def _complete_world():
    t = Topology()
    m1 = t.add_device("host", "M1"); m2 = t.add_device("host", "M2")
    s1 = t.add_device("switch", "S1"); r1 = t.add_device("router", "R1")
    t.add_link(m1.id, s1.id); t.add_link(m2.id, s1.id); t.add_link(s1.id, r1.id)
    return O.TopologyWorld(t)


def test_clock_formatting():
    assert _fmt_clock(1500) == "25:00"
    assert _fmt_clock(65) == "1:05"
    assert _fmt_clock(0) == "0:00"
    assert _fmt_clock(None) == "—:—"


def _headers(panel):
    from PySide6.QtWidgets import QPushButton
    return [panel._obj_box.itemAt(i).widget() for i in range(panel._obj_box.count())
            if isinstance(panel._obj_box.itemAt(i).widget(), QPushButton)]


def _rows(panel):
    from PySide6.QtWidgets import QLabel
    return [panel._obj_box.itemAt(i).widget() for i in range(panel._obj_box.count())
            if isinstance(panel._obj_box.itemAt(i).widget(), QLabel)]


def test_ladder_shows_only_the_active_level_expanded():
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    p.refresh(O.TopologyWorld(Topology()))            # empty world → L1 is where you are
    assert len(_headers(p)) == 2                      # one collapsible header per rung
    assert len(_rows(p)) == 3                         # ONLY the active rung's tasks are listed


def test_completed_levels_fold_away():
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    p.refresh(_complete_world())                      # everything done
    assert len(_rows(p)) == 0                         # every rung folds to its summary line
    assert all("✓" in h.text() for h in _headers(p))  # ...shown as complete


def test_a_folded_level_can_be_opened_by_clicking_it():
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    p.refresh(O.TopologyWorld(Topology()))
    before = len(_rows(p))
    _headers(p)[1].click()                            # peek at the folded L2
    assert len(_rows(p)) > before


def test_panel_survives_repeated_refresh_without_stacking_rows():
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    for _ in range(4):
        p.refresh(O.TopologyWorld(Topology()))
    assert len(_rows(p)) == 3                         # cleared each time, not stacked
