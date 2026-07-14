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


def _tiles(panel):
    """The level ribbon."""
    from PySide6.QtWidgets import QPushButton
    return [panel._ribbon.itemAt(i).widget() for i in range(panel._ribbon.count())
            if isinstance(panel._ribbon.itemAt(i).widget(), QPushButton)]


def _rows(panel):
    """The task lines on screen (minus the LEVEL n header / peek note)."""
    from PySide6.QtWidgets import QLabel
    labels = [panel._obj_box.itemAt(i).widget() for i in range(panel._obj_box.count())
              if isinstance(panel._obj_box.itemAt(i).widget(), QLabel)]
    return [w for w in labels
            if not w.text().startswith("LEVEL") and not w.text().startswith("Looking")]


def test_only_the_level_you_are_on_is_on_screen():
    """The whole point: no clutter of finished or future tasks — just the next move, big enough to
    read. The ribbon carries the shape of the journey instead."""
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    p.refresh(O.TopologyWorld(Topology()))            # empty world → you're on L1
    assert p._shown == 1
    assert len(_tiles(p)) == 2                        # every level has a tile, always
    assert len(_rows(p)) == 3                         # only L1's tasks are listed
    assert all("Place" in r.text() or "Add" in r.text() for r in _rows(p))


def test_finishing_a_level_brings_the_next_one_to_life():
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    p.refresh(O.TopologyWorld(Topology()))
    l1_rows = [r.text() for r in _rows(p)]

    t = Topology()                                    # place the elements, but wire nothing
    for tk, n in (("host", "M1"), ("host", "M2"), ("switch", "S1"), ("router", "R1")):
        t.add_device(tk, n)
    p.refresh(O.TopologyWorld(t))

    assert p._shown == 2                              # the indicator moved on by itself
    assert [r.text() for r in _rows(p)] != l1_rows     # L1's tasks are GONE from the board
    assert _tiles(p)[0].text().startswith("✓")        # ...and ticked off in the ribbon


def test_you_can_look_ahead_but_it_snaps_back_when_you_advance():
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    p.refresh(O.TopologyWorld(Topology()))

    _tiles(p)[1].click()                              # peek at the locked L2
    assert p._peek == 2 and p._shown == 1             # looking ahead — but still ON level 1
    from PySide6.QtWidgets import QLabel
    notes = [p._obj_box.itemAt(i).widget().text() for i in range(p._obj_box.count())
             if isinstance(p._obj_box.itemAt(i).widget(), QLabel)]
    assert any("Looking ahead" in n for n in notes)   # …and told so, so it can't be confused for progress

    t = Topology()                                    # now actually finish L1
    for tk, n in (("host", "M1"), ("host", "M2"), ("switch", "S1"), ("router", "R1")):
        t.add_device(tk, n)
    p.refresh(O.TopologyWorld(t))
    assert p._peek is None and p._shown == 2          # advancing cancels the peek


def test_run_check_button_only_exists_when_there_is_something_to_run():
    _app()
    p = MissionPanel()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()   # basic-lan: no live objectives
    p.set_mission(m)
    assert not p._run_btn.isVisibleTo(p)

    live = Mission(L.from_archetype("reachability-boundary", {}, id="rb"), now=lambda: 0.0)
    live.start()
    p.set_mission(live)
    assert p._run_btn.isVisibleTo(p)                  # this one has Live: tasks


def test_panel_survives_repeated_refresh_without_stacking_rows():
    _app()
    m = Mission(_lesson(), now=lambda: 0.0); m.start()
    p = MissionPanel(); p.set_mission(m)
    for _ in range(4):
        p.refresh(O.TopologyWorld(Topology()))
    assert len(_rows(p)) == 3                         # cleared each time, not stacked
    assert len(_tiles(p)) == 2
