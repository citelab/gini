"""The Multicast HUD's map: each group's real tree, drawn from what the routers report.

Every fixture here was read off real gRouters running mcast_tree.lua: three routers, two groups,
a switched LAN. H1 sends 239.1.1.1, H5 sends 239.2.2.2; H2 (on the LAN) joined the first, H3 (on
the same LAN) the second, H4 both; nobody behind R3 joined, so R1 pruned that branch. Delivered:
H2 32, H3 17, H4 100. The tests pin that the picture says exactly that, and nothing a router did
not report.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from gini.domain.mcast import McastEvent, McastTracker, parse_cp_status
from gini.domain.mcast_map import (
    PALETTE,
    assign_colours,
    build,
    describe_event,
    parse_igmp,
    parse_sources,
    runs_forwarder,
    segments,
    shared_lans,
)
from gini.domain.topology import Topology

from gini.services.compiler import address_map

R1 = ("MCAST v1 2\nG 239.2.2.2 IF 1 CP 1:15\nG 239.1.1.1 IF 1 CP 1:30\n"
      "S 10.0.3.10 G 239.1.1.1 RPF 3 OIF 1 PRUNED 2 DROPS 0 COPIES 34\n"
      "S 10.0.4.10 G 239.2.2.2 RPF 4 OIF 1 PRUNED 2 DROPS 0 COPIES 17\n")
R2 = ("MCAST v1 2\nG 239.2.2.2 IF 2,3 CP 2:16,3:16\nG 239.1.1.1 IF 2,3 CP 2:31,3:31\n"
      "S 10.0.3.10 G 239.1.1.1 RPF 1 OIF 2,3 PRUNED - DROPS 0 COPIES 62\n"
      "S 10.0.4.10 G 239.2.2.2 RPF 1 OIF 2,3 PRUNED - DROPS 0 COPIES 32\n")
R3 = ("MCAST v1 0\nS 10.0.3.10 G 239.1.1.1 RPF 1 OIF - PRUNED - DROPS 0 COPIES 0\n"
      "S 10.0.4.10 G 239.2.2.2 RPF 1 OIF - PRUNED - DROPS 0 COPIES 0\n")
IGMP_H4 = ("Idx\tDevice    : Count Querier\tGroup    Users Timer\tReporter\n"
           "1\tlo        :     1      V2\n\t\t\t\t010000E0     1 0:00000000\t\t0\n"
           "469\teth0      :     1      V2\n\t\t\t\t010000E0     1 0:00000000\t\t0\n"
           "11\tgini0     :     3      V2\n\t\t\t\t020202EF     1 0:00000000\t\t1\n"
           "\t\t\t\t010101EF     1 0:00000000\t\t1\n\t\t\t\t010000E0     1 0:00000000\t\t0\n")
POS = {"R1": (300, 200), "R2": (550, 120), "R3": (550, 320), "S1": (750, 100),
       "H1": (100, 120), "H5": (100, 300), "H2": (900, 40), "H3": (900, 160),
       "H4": (650, 0), "H6": (750, 380)}
G1, G2 = "239.1.1.1", "239.2.2.2"


@pytest.fixture(scope="module")
def addressing():
    """The same network, built the same way, so the interface numbers are the real ones."""
    t = Topology("mmap")
    d = {}
    for kind, name in (("router", "R1"), ("router", "R2"), ("router", "R3"), ("switch", "S1")):
        d[name] = t.add_device(kind, name)
    for n in ("H1", "H5", "H2", "H3", "H4", "H6"):
        d[n] = t.add_device("host", n, properties={"Toolkit": "full"})
    for a, b in (("R1", "R2"), ("R1", "R3"), ("H1", "R1"), ("H5", "R1"), ("S1", "R2"),
                 ("H2", "S1"), ("H3", "S1"), ("H4", "R2"), ("H6", "R3")):
        t.add_link(d[a].id, d[b].id)
    return address_map(t)


def _all(text_by_router):
    rows = [r for name, txt in text_by_router.items() for r in parse_cp_status(txt, name)]
    trees = [s for name, txt in text_by_router.items() for s in parse_sources(txt, name)]
    return rows, trees


JOINED = {"H2": {G1}, "H3": {G2}}


# ---- reading what is reported ----------------------------------------------------------------- #
def test_source_lines_are_read_with_their_interfaces():
    s = parse_sources(R1, "R1")
    assert [(t.source, t.group, t.rpf, t.oifs, t.pruned, t.copies) for t in s] == [
        ("10.0.3.10", G1, 3, (1,), (2,), 34), ("10.0.4.10", G2, 4, (1,), (2,), 17)]
    pruned_branch = parse_sources(R3, "R3")[0]
    assert pruned_branch.oifs == () and pruned_branch.rpf == 1


def test_a_machines_own_memberships_are_read_from_proc_net_igmp():
    """Hex in host byte order; only the fabric interfaces; never the all-hosts group."""
    assert parse_igmp(IGMP_H4) == {G1, G2}
    assert parse_igmp("") == set()


def test_a_forwarder_with_no_groups_is_still_a_forwarder():
    assert runs_forwarder(R3) and runs_forwarder("MCAST v1 0\n")
    assert not runs_forwarder("no control modules loaded")


def test_segments_are_subnets_and_a_switch_is_their_hub(addressing):
    lan = next(s for s in segments(addressing).values() if "H2" in s.hosts)
    assert sorted(lan.hosts) == ["H2", "H3"] and lan.hub == "S1"
    assert lan.routers == [("R2", 2)]                 # R2's second interface, as the router says


# ---- the trees -------------------------------------------------------------------------------- #
def test_each_group_is_drawn_from_its_source_to_exactly_its_members(addressing):
    rows, trees = _all({"R1": R1, "R2": R2, "R3": R3})
    m = build(addressing, rows, trees, JOINED)
    g1, g2 = m.groups[G1], m.groups[G2]
    assert g1.sources == ["H1"] and sorted(g1.members) == ["H2", "H4"]
    assert g2.sources == ["H5"] and sorted(g2.members) == ["H3", "H4"]
    data = {(e.a, e.b) for e in g1.edges if e.kind == "data"}
    assert data == {("H1", "R1"), ("R1", "R2"), ("R2", "H2"), ("R2", "H4")}
    assert {(e.a, e.b) for e in g2.edges if e.kind == "data"} == {
        ("H5", "R1"), ("R1", "R2"), ("R2", "H3"), ("R2", "H4")}


def test_the_lan_branch_runs_through_the_switch(addressing):
    rows, trees = _all({"R1": R1, "R2": R2, "R3": R3})
    e = next(e for e in build(addressing, rows, trees, JOINED).groups[G1].edges
             if e.b == "H2")
    assert e.via == "S1" and (e.router, e.iface) == ("R2", 2)


def test_the_branch_nobody_wanted_is_drawn_pruned(addressing):
    rows, trees = _all({"R1": R1, "R2": R2, "R3": R3})
    m = build(addressing, rows, trees, JOINED)
    for g in (G1, G2):
        assert [(e.a, e.b) for e in m.groups[g].edges if e.kind == "pruned"] == [("R1", "R3")]
    assert "H6" not in m.groups[G1].members + m.groups[G1].maybe


def test_on_a_shared_lan_the_router_alone_does_not_name_the_member(addressing):
    """R2 copies BOTH groups onto the LAN — it cannot know H2 wants one and H3 the other. Without
    the machines' own word they are 'a member is on this LAN', never members."""
    rows, trees = _all({"R1": R1, "R2": R2, "R3": R3})
    m = build(addressing, rows, trees, joined=None)
    assert "H2" not in m.groups[G1].members and "H3" not in m.groups[G1].members
    assert {"H2", "H3"} <= set(m.groups[G1].maybe)
    assert m.groups[G1].members == ["H4"]             # alone on its segment: exact
    assert sorted(shared_lans(addressing, rows)) == ["H2", "H3"]


def test_a_source_reaches_its_first_router_even_with_nobody_joined(addressing):
    lone = "MCAST v1 0\nS 10.0.3.10 G 239.9.9.9 RPF 3 OIF - PRUNED - DROPS 0 COPIES 0\n"
    rows, trees = _all({"R1": lone})
    v = build(addressing, rows, trees).groups["239.9.9.9"]
    assert [(e.a, e.b) for e in v.edges] == [("H1", "R1")] and v.members == []


def test_reverse_path_refusals_are_counted_per_router(addressing):
    bad = "MCAST v1 0\nS 10.0.3.10 G 239.1.1.1 RPF 1 OIF - PRUNED - DROPS 7 COPIES 0\n"
    rows, trees = _all({"R2": bad})
    assert build(addressing, rows, trees).drops == {"R2": 7}


def test_a_groups_colour_never_changes_when_another_group_arrives():
    c = assign_colours([G2], {})
    c = assign_colours([G1, G2, "239.0.0.5"], c)
    assert c[G2] == PALETTE[0] and len(set(c.values())) == 3


def test_ticker_events_say_what_happened_behind_the_interface(addressing):
    """A dense-mode re-flood toward R3 is not R3 joining anything."""
    join_r3 = McastEvent(1.0, "R1", G1, 2, "join")
    leave_r3 = McastEvent(1.0, "R1", G1, 2, "leave")
    host = McastEvent(1.0, "R2", G1, 3, "join")
    assert describe_event(addressing, join_r3) == f"R1 re-floods {G1} toward R3"
    assert "R3 pruned" in describe_event(addressing, leave_r3)
    assert describe_event(addressing, host) == f"H4 joined {G1} (via R2)"


# ---- the panel -------------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _hud(theme, addressing, texts=None, forwarders=("R1", "R2", "R3"), moving=True):
    from gini.ui.mcast_hud import McastHud
    h = McastHud(None, theme)
    rows, trees = _all(texts if texts is not None else {"R1": R1, "R2": R2, "R3": R3})
    tr = McastTracker()
    for k in range(3):                      # three polls, counters moving if `moving`
        step = 2 * k if moving else 0
        tr.ingest([type(r)(r.router, r.group, r.ifaces,
                           {i: c + step for i, c in r.copies.items()}) for r in rows],
                  tnow=10.0 + 1.2 * k)
    h.set_state(build(addressing, rows, trees, JOINED), tr, addressing, POS,
                set(forwarders), ["R1", "R2", "R3"])
    return h


def _paints(h) -> None:
    from PySide6.QtGui import QPixmap
    pm = QPixmap(h.size())
    h.render(pm)                            # a paintEvent that raised would abandon the panel


def test_the_map_paints_in_every_state(app, theme, addressing):
    _paints(_hud(theme, addressing))
    _paints(_hud(theme, addressing, texts={}, forwarders=()))            # nothing loaded
    _paints(_hud(theme, addressing, texts={"R1": "MCAST v1 0\n"}))       # loaded, no groups
    h = _hud(theme, addressing)
    h._focus = G2
    _paints(h)
    h.motion = False
    _paints(h)
    from gini.ui.mcast_hud import McastHud
    _paints(McastHud(None, theme))                                       # nothing at all


def test_the_summary_says_why_the_map_is_empty(app, theme, addressing):
    none = _hud(theme, addressing, texts={}, forwarders=())
    assert "No multicast forwarder is running" in none.summary()
    idle = _hud(theme, addressing, texts={"R1": "MCAST v1 0\n"}, forwarders=("R1",))
    assert "no group yet" in idle.summary() and "not running it: R2, R3" in idle.summary()


def test_focusing_a_group_names_its_source_and_members(app, theme, addressing):
    h = _hud(theme, addressing)
    h._focus = G1
    s = h.summary()
    assert s.startswith(G1) and "from H1" in s and "H2" in s and "H4" in s


def test_dots_move_only_where_copies_are_flowing(app, theme, addressing):
    """Animation costs paint time, so it runs only for branches carrying copies, and not at all
    with motion off."""
    assert _hud(theme, addressing, moving=True).animating()
    still = _hud(theme, addressing, moving=False)
    assert not still.animating()
    h = _hud(theme, addressing)
    h.motion = False
    assert not h.animating()


def test_the_animation_timer_stops_when_the_panel_hides(app, theme, addressing):
    h = _hud(theme, addressing)
    h.show()
    app.processEvents()
    assert h._anim.isActive()
    h.hide()
    assert not h._anim.isActive()


class _Click:
    def __init__(self, pt):
        self._pt = pt

    def position(self):
        return self._pt


def test_clicking_a_chip_focuses_its_group_and_clicking_again_lets_go(app, theme, addressing):
    h = _hud(theme, addressing)
    _paints(h)                                         # lays the chips out
    rect = {g: r for r, g in h._chip_rects}[G2]
    h.mousePressEvent(_Click(rect.center()))
    assert h._focus == G2
    h.mousePressEvent(_Click(rect.center()))
    assert h._focus is None


def test_the_motion_chip_turns_the_dots_off(app, theme, addressing):
    h = _hud(theme, addressing)
    _paints(h)
    h.mousePressEvent(_Click(h._motion_rect.center()))
    assert h.motion is False and not h.animating()


def test_machines_are_asked_only_on_shared_lans_and_an_empty_answer_is_no_answer(app, theme,
                                                                                   addressing):
    """The machines are the only ones who know which of them joined on a LAN — and only there is
    it worth a docker exec. A shell that failed returns "", which must not erase a member."""
    import time as _t

    from PySide6.QtWidgets import QWidget

    from gini.ui.mcast_hud import McastHudController
    asked = []

    def shell(h, cmd):
        asked.append(h)
        return IGMP_H4.replace("020202EF", "00000000") if h == "H2" else ""

    texts = {"R1": R1, "R2": R2, "R3": R3}
    parent = QWidget()                                 # held: the controller is its child
    ctl = McastHudController(parent, theme, routers=lambda: ["R1", "R2", "R3"],
                             query=lambda n, c: texts[n], addressing=lambda: addressing,
                             positions=lambda: POS, host_shell=shell)
    ctl.refresh()
    deadline = _t.monotonic() + 5
    while (ctl._busy or ctl.hud._map is None) and _t.monotonic() < deadline:
        app.processEvents()
        _t.sleep(0.01)
    app.processEvents()
    assert sorted(set(asked)) == ["H2", "H3"]          # never H4 (alone), never H1/H5/H6
    g1 = ctl.hud._map.groups[G1]
    assert "H2" in g1.members                          # H2 said so
    assert "H3" in g1.maybe and "H3" not in g1.members  # H3 said nothing: unknown, not "no"
    ctl.close()
