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


def test_panel_renders_one_row_per_objective():
    _app()
    clock = [0.0]
    m = Mission(_lesson(), now=lambda: clock[0]); m.start()
    p = MissionPanel(); p.set_mission(m)
    p.refresh(O.TopologyWorld(Topology()))            # empty world → all unmet
    assert p._obj_box.count() == len(m.lesson.objectives)


def test_panel_shows_band_when_complete():
    _app()
    clock = [0.0]
    m = Mission(_lesson(), now=lambda: clock[0]); m.start()
    p = MissionPanel(); p.set_mission(m)
    clock[0] = 60.0
    p.refresh(_complete_world())                      # completes → witnessed gold
    assert m.last_band == "gold"
    assert not p._band.isHidden()                     # shown (panel itself isn't shown in-test)
    assert "GOLD" in p._band.text()


def test_panel_survives_repeated_refresh_without_stacking_rows():
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    for _ in range(4):
        p.refresh(O.TopologyWorld(Topology()))
    assert p._obj_box.count() == len(m.lesson.objectives)   # cleared each time, not stacked
