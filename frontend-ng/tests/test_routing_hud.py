"""Routing HUD widget — renders a RoutingModel and highlights the authentic forwarding tree."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain.routetable import RouteEntry
from gini.domain.routing_model import Edge, RouterNode, RoutingModel, forwarding_tree

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _model():
    R1 = RouterNode("R1", "R1", {"10.0.1.1"}, [
        RouteEntry(0, "10.0.1.0", "255.255.255.0", "0.0.0.0", "tun0"),
        RouteEntry(1, "10.0.2.0", "255.255.255.0", "10.0.1.2", "tun0")])
    R2 = RouterNode("R2", "R2", {"10.0.1.2", "10.0.2.1"}, [
        RouteEntry(0, "10.0.1.0", "255.255.255.0", "0.0.0.0", "tun0"),
        RouteEntry(1, "10.0.2.0", "255.255.255.0", "0.0.0.0", "tun1")])
    R3 = RouterNode("R3", "R3", {"10.0.2.2"}, [
        RouteEntry(0, "10.0.2.0", "255.255.255.0", "0.0.0.0", "tun0")])
    return RoutingModel([R1, R2, R3], [Edge("R1", "R2", 5), Edge("R2", "R3", 10)])


def _hud(app):
    from gini.ui.routing_hud import RoutingHud
    pos = {"R1": (0, 0), "R2": (100, 0), "R3": (200, 0)}
    return RoutingHud(None, _theme(app), _model(), pos)


def test_hud_renders_without_error(app):
    h = _hud(app); h.resize(360, 300)
    h.grab()                                            # forces a paint pass offscreen
    assert h._model is not None and len(h._fit()) == 3   # all three routers laid out
    h.close()


def test_a_click_highlights_the_authentic_forwarding_tree(app):
    """A plain CLICK traces. Tracing is what the panel is for, so it is the easy gesture —
    it used to need a 380 ms hold, which is how a live session ran for minutes with no root
    ever set and no way to tell that apart from a genuinely dark network."""
    h = _hud(app)
    h._press_rid = "R1"
    h._lp.start(9999)                                   # timer armed by the press…
    h.mouseReleaseEvent(None)                            # …released → a click
    assert h._root == "R1"
    expect = forwarding_tree(h._model, "R1").edges_used
    assert h._trace.edges_used == expect                # HUD highlights exactly the traced edges
    assert ("R1", "R2") in h._trace.edges_used and ("R2", "R3") in h._trace.edges_used
    h.close()


def test_holding_shows_the_table(app):
    """The secondary gesture: hold rather than click."""
    h = _hud(app)
    h._press_rid = "R2"; h._fire_longpress()             # the hold timer fired
    assert h._table_rid == "R2"                          # R2's table card is shown
    h.clear_highlight()
    assert h._table_rid is None and h._root is None
    h.close()


def test_controller_builds_model_from_injected_seams(app):
    from gini.ui.routing_hud import RoutingHudController
    routes = {"R1": "[0] 10.0.1.0 255.255.255.0 0.0.0.0 tun0\n"
                    "[1] 10.0.2.0 255.255.255.0 10.0.1.2 tun0",
              "R2": "[0] 10.0.1.0 255.255.255.0 0.0.0.0 tun0\n"
                    "[1] 10.0.2.0 255.255.255.0 0.0.0.0 tun1"}
    ifaces = {"R1": "eth0 10.0.1.1/24 m", "R2": "eth0 10.0.1.2/24 m\neth1 10.0.2.1/24 m"}
    ctrl = RoutingHudController(
        None, _theme(app),
        router_devices=lambda: [("R1", "R1"), ("R2", "R2")],
        query=lambda name, cmd: routes[name] if "route" in cmd else ifaces[name],
        delay_prop=lambda rid, k: "5 0 0",
        positions_of=lambda: {"R1": (0, 0), "R2": (100, 0)})
    model, pos, ctrls = ctrl._build()                    # synchronous build (what the worker runs)
    assert set(model.routers) == {"R1", "R2"} and pos["R2"] == (100, 0)
    assert model.edge_latency("R1", "R2") == 10         # 5 egress + 5 ingress
    # No SDN seams injected, so this takes the router-only path: adjacency still inferred from
    # the routers' own connected routes, and nothing about the switch side is required.
    assert model.ovs == {} and ctrls == {}
    ctrl.hud.set_model(model, pos)
    assert len(ctrl.hud._fit()) == 2


def test_tap_empty_space_clears_the_highlight(app):
    from PySide6.QtCore import QPointF
    h = _hud(app)
    h._press_rid = "R1"; h._lp.start(9999); h.mouseReleaseEvent(None)   # click → trace
    assert h._trace is not None

    class _FarEv:                                       # a tap far from any node
        def position(self):
            return QPointF(9999, 9999)
    h.mousePressEvent(_FarEv())
    assert h._trace is None and h._root is None and h._table_rid is None   # switched off
    h.close()


def test_live_refresh_keeps_the_spt_root(app):
    # convergence: set_model re-runs the trace for the same root so the highlight persists
    h = _hud(app)
    h._press_rid = "R1"; h._lp.start(9999); h.mouseReleaseEvent(None)   # click → trace
    h.set_model(_model())                               # a fresh snapshot (as if re-polled)
    assert h._root == "R1" and h._trace is not None
    h.close()


# --- the flicker: a flaky poll must not put the picture out ---------------------------------- #
# Reported live: "the links were lighting up based on the flow table entries, then it stopped
# working... some amount of flakiness." Two independent causes, both reproduced here.
_ENTRIES = ("Entry 0\nMatch:\n\tEthernet destination MAC address: 02:00:00:0a:0a:0a\n"
            "Actions:\n\t\tOutput port: 3\nPriority: 100\n")
_IFCONFIG = ("Int.\tState/Mode\tDevice\tIP\tMAC\tMTU\tSock\tTID\n"
             "1\tUN\t\ttun1\t169.254.0.1\t02:00:fe:00:00:01\t1500\t/tmp/a\t7\n"
             "2\tUN\t\ttun2\t169.254.0.2\t02:00:fe:00:00:02\t1500\t/tmp/b\t8\n")


def _sdn_controller(app, fail_polls):
    """Two switches; OVS1 fails to answer on the polls listed in `fail_polls`."""
    from gini.ui.routing_hud import RoutingHudController
    state = {"n": 0}

    def query(name, cmd):
        if "verbose" in cmd:
            return _IFCONFIG
        # element_query NEVER raises — it returns this string. That is the whole bug.
        if name == "OVS1" and state["n"] in fail_polls:
            return "(query failed: Command timed out)"
        return _ENTRIES

    ctrl = RoutingHudController(
        None, _theme(app),
        router_devices=lambda: [],
        query=query,
        delay_prop=lambda rid, k: "",
        positions_of=lambda: {"ovs1": (0, 0), "ovs2": (100, 0)},
        switch_devices=lambda: [("ovs1", "OVS1", None), ("ovs2", "OVS2", None)],
        neighbours_of=lambda rid: ["m1", "ovs2"] if rid == "ovs1" else ["ovs1", "m9"],
        topo_links=lambda: [("ovs1", "ovs2")],
        passthrough_of=lambda: set())
    return ctrl, state


def _poll(ctrl, state):
    model, pos, ctrls = ctrl._build()
    ctrl._on_model(model, pos, ctrls)
    state["n"] += 1
    return model


def test_a_timed_out_poll_does_not_put_the_lit_path_out(app):
    """The switch's rules did not vanish because we failed to read them, so the picture must
    not go dark — it carries the last known rules and marks the node stale instead."""
    ctrl, state = _sdn_controller(app, fail_polls={2, 3})
    _poll(ctrl, state)
    ctrl.hud._root = "ovs1"
    ctrl.hud.set_model(ctrl.hud._model)                  # as a long-press would
    assert ctrl.hud._trace.edges_used, "baseline: the path is lit"

    for _ in range(5):
        _poll(ctrl, state)
        assert ctrl.hud._trace is not None and ctrl.hud._trace.edges_used, \
            "a failed poll must not blank the highlight"
    assert ctrl.hud._model.ovs["ovs1"].reachable is True, "recovered by the last poll"


def test_a_flaky_poll_does_not_fill_the_timeline_with_phantom_events(app):
    """Every blanked flow table used to change the forwarding projection, so RouteHistory
    recorded a convergence tick — which is the dense tick cluster seen on the live timeline."""
    ctrl, state = _sdn_controller(app, fail_polls={1, 3, 5, 7})
    for _ in range(9):
        _poll(ctrl, state)
    assert len(ctrl.history) == 1, \
        f"forwarding never changed, so exactly one snapshot — got {len(ctrl.history)}"


def test_the_root_survives_a_poll_that_fails_outright(app):
    """`_on_model(None)` used to clear the root, so the highlight vanished for good and only a
    fresh long-press brought it back. The root is the user's choice, not a fact about a poll."""
    ctrl, state = _sdn_controller(app, fail_polls=set())
    _poll(ctrl, state)
    ctrl.hud._root = "ovs1"
    ctrl.hud.set_model(ctrl.hud._model)

    ctrl._on_model(None, {"ovs1": (0, 0)}, {})           # a whole rebuild failed
    assert ctrl.hud._root == "ovs1", "the selection must survive"
    _poll(ctrl, state)                                   # …and re-light on recovery
    assert ctrl.hud._trace is not None and ctrl.hud._trace.edges_used


def test_an_explicit_reset_DOES_drop_the_root(app):
    """The counterpart: a topology swap really does invalidate the selection."""
    ctrl, state = _sdn_controller(app, fail_polls=set())
    _poll(ctrl, state)
    ctrl.hud._root = "ovs1"
    ctrl.reset()
    assert ctrl.hud._root is None and ctrl._run_cache == {}


def test_the_port_map_is_not_re_read_on_every_poll(app):
    """Five switches x 2 commands on a 2.5 s timer is ten serial `docker compose exec` calls;
    the port wiring half of that is constant, and every extra call is another chance to time
    out and blank the picture."""
    calls = []
    from gini.ui.routing_hud import RoutingHudController
    ctrl = RoutingHudController(
        None, _theme(app), router_devices=lambda: [],
        query=lambda n, c: (calls.append((n, c)), _IFCONFIG if "verbose" in c else _ENTRIES)[1],
        delay_prop=lambda rid, k: "", positions_of=lambda: {},
        switch_devices=lambda: [("ovs1", "OVS1", None)],
        neighbours_of=lambda rid: ["m1", "ovs2"],
        topo_links=lambda: [], passthrough_of=lambda: set())
    for _ in range(4):
        ctrl._build()
    assert len([c for c in calls if "verbose" in c[1]]) == 1
    assert len([c for c in calls if "entry all" in c[1]]) == 4, "flows ARE re-read every poll"


def test_a_near_miss_selects_the_node_instead_of_wiping_the_selection(app):
    """Nodes are ~32 px inside a panel that squeezes a whole topology into a corner, and a
    switch is drawn SQUARE — a circular test of radius NODE_R+3 missed its corners outright.
    Worse, a miss counted as "clicked empty space" and cleared the root, so being a few
    pixels off did not just fail to select, it destroyed what was already selected."""
    from PySide6.QtCore import QPointF
    h = _hud(app)
    fit = h._fit()
    pt = fit["R1"]

    class _Ev:
        def __init__(self, p): self._p = p
        def position(self): return self._p

    # a click NODE_R+8 away — outside the old radius, inside the node's visual reach
    h.mousePressEvent(_Ev(QPointF(pt.x() + 24, pt.y())))
    assert h._press_rid == "R1", "a near miss must still land on the node"
    h.mouseReleaseEvent(None)
    assert h._root == "R1"
    h.close()


def test_a_click_on_genuinely_empty_space_still_clears(app):
    from PySide6.QtCore import QPointF
    h = _hud(app)
    h._press_rid = "R1"; h._lp.start(9999); h.mouseReleaseEvent(None)
    assert h._root == "R1"

    class _Far:
        def position(self): return QPointF(9999, 9999)
    h.mousePressEvent(_Far())
    assert h._root is None
    h.close()


def test_the_panel_says_when_nothing_is_selected(app):
    """The gap that let a whole live session run with no root: an unselected panel and a dark
    network rendered identically — as silence."""
    h = _hud(app); h.resize(360, 300)
    h.clear_highlight()
    h.grab()                                            # must paint the hint without error
    assert h._root is None
    h.close()


# --- the Console is for things needing attention, not a running commentary ------------------- #
def test_a_healthy_network_logs_nothing_at_all(app):
    """The diagnostic started as a debugging aid that printed a line per poll. Most of what it
    said now appears ON the panel, where the person is already looking."""
    ctrl, state = _sdn_controller(app, fail_polls=set())
    logs = []
    ctrl._log = logs.append
    for _ in range(10):
        _poll(ctrl, state)
    assert logs == [], f"a healthy network must be silent — got {logs}"


def test_a_switch_going_quiet_is_reported_once_and_so_is_its_recovery(app):
    """Reported once, not once per poll: the two transitions the panel cannot show by itself."""
    ctrl, state = _sdn_controller(app, fail_polls=set(range(1, 6)))
    logs = []
    ctrl._log = logs.append
    for _ in range(10):
        _poll(ctrl, state)
    assert len(logs) == 2, f"one line down, one line back up — got {logs}"
    assert "no answer" in logs[0] and "answering again" in logs[1]
