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


def test_longpress_highlights_the_authentic_forwarding_tree(app):
    h = _hud(app)
    h._press_rid = "R1"; h._fire_longpress()            # simulate a long-press on R1
    assert h._root == "R1"
    expect = forwarding_tree(h._model, "R1").edges_used
    assert h._trace.edges_used == expect                # HUD highlights exactly the traced edges
    assert ("R1", "R2") in h._trace.edges_used and ("R2", "R3") in h._trace.edges_used
    h.close()


def test_tap_shows_the_routing_table(app):
    h = _hud(app)
    h._press_rid = "R2"
    h._lp.start(9999)                                   # pretend the long-press timer is armed…
    h.mouseReleaseEvent(None)                            # …released first → it's a tap
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
    model, pos = ctrl._build()                          # synchronous build (what the worker runs)
    assert set(model.routers) == {"R1", "R2"} and pos["R2"] == (100, 0)
    assert model.edge_latency("R1", "R2") == 10         # 5 egress + 5 ingress
    ctrl.hud.set_model(model, pos)
    assert len(ctrl.hud._fit()) == 2


def test_tap_empty_space_clears_the_highlight(app):
    from PySide6.QtCore import QPointF
    h = _hud(app)
    h._press_rid = "R1"; h._fire_longpress()            # highlight the tree
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
    h._press_rid = "R1"; h._fire_longpress()
    h.set_model(_model())                               # a fresh snapshot (as if re-polled)
    assert h._root == "R1" and h._trace is not None
    h.close()
