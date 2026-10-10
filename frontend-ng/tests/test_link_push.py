"""Changing a link's cost while the lab runs reaches the routers (Phase 2).

Two things must happen, and only those: both routers on the link learn the new cost (that is all
dynamic mode needs -- RIP reads it next tick), and in static mode the routes are recomputed and
only the DIFFERENCES pushed -- re-add what moved, delete by network what is no longer wanted, leave
the rest and anything a student typed alone. These check the plan, then the real MainWindow
executing it against a recording console.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain import link_props as LP
from gini.domain.topology import Topology
from gini.services import link_push as LPU
from gini.services.compiler import RuntimeCompiler


def _triangle():
    """R1-R2 (direct), R1-R3, R3-R2, and H2 behind R2."""
    t = Topology("tri")
    r1, r2, r3 = (t.add_device("router", n) for n in ("R1", "R2", "R3"))
    h2 = t.add_device("host", "H2")
    direct = t.add_link(r1.id, r2.id)
    left, right = t.add_link(r1.id, r3.id), t.add_link(r3.id, r2.id)
    t.add_link(h2.id, r2.id)
    return t, direct, left, right


def _cfg(t):
    return RuntimeCompiler().compile(t)


def test_both_ends_learn_the_new_cost():
    t, direct, _l, _r = _triangle()
    before = _cfg(t)
    LP.set_prop(direct, "cost", 10)
    cmds, _inst, _msgs = LPU.plan(_cfg(t), LPU.installed_routes(before), {direct.id: 10},
                                  static=False)
    assert sorted(cmds) == [("R1", "ifconfig mod tun1 -metric 10"),
                            ("R2", "ifconfig mod tun1 -metric 10")]


def test_dynamic_mode_pushes_costs_and_nothing_else():
    t, direct, _l, _r = _triangle()
    installed = LPU.installed_routes(_cfg(t))
    LP.set_prop(direct, "cost", 10)
    cmds, new_installed, _m = LPU.plan(_cfg(t), installed, {direct.id: 10}, static=False)
    assert all(c.startswith("ifconfig mod") for _r, c in cmds)
    assert new_installed == installed


def test_static_mode_moves_only_the_routes_that_changed():
    t, direct, left, right = _triangle()
    installed = LPU.installed_routes(_cfg(t))
    LP.set_prop(left, "cost", 2)                         # make the detour cheap first, so only
    LP.set_prop(right, "cost", 3)                        # the direct link's change is under test
    installed = LPU.installed_routes(_cfg(t))
    LP.set_prop(direct, "cost", 10)                      # now R1 -> H2 should go via R3
    new = _cfg(t)
    cmds, new_installed, msgs = LPU.plan(new, installed, {direct.id: 10}, static=True)
    route_cmds = [(r, c) for r, c in cmds if c.startswith("route")]
    assert route_cmds, "the cheaper detour must be pushed"
    r3_ip = next(i.ip.split("/")[0] for i in next(r for r in new.routers if r.name == "R3").ifaces
                 if i.link_id == left.id)
    assert ("R1", f"route add -dev tun2 -net 10.0.4.0 -netmask 255.255.255.0 -gw {r3_ip}") \
        in route_cmds
    # nothing re-sent that did not change
    for rname, routes in new_installed.items():
        for key, val in routes.items():
            if installed.get(rname, {}).get(key) == val:
                assert not any(r == rname and f"-net {key[0]} " in c for r, c in route_cmds)
    assert any("now via" in m for m in msgs)


def test_a_route_no_longer_wanted_is_deleted_by_network():
    t, direct, _l, _r = _triangle()
    cfg = _cfg(t)
    installed = LPU.installed_routes(cfg)
    installed["R1"][("10.0.99.0", "255.255.255.0")] = ("10.0.1.2", 1)   # gBuilder put it there
    cmds, new_installed, _m = LPU.plan(cfg, installed, {direct.id: 1}, static=True)
    assert ("R1", "route del -net 10.0.99.0 -netmask 255.255.255.0") in cmds
    assert ("10.0.99.0", "255.255.255.0") not in new_installed["R1"]


def test_only_routes_gbuilder_installed_are_ever_deleted():
    """A route a student typed is not in `installed`, so nothing in the plan can remove it."""
    t, direct, _l, _r = _triangle()
    installed = LPU.installed_routes(_cfg(t))
    LP.set_prop(direct, "cost", 10)
    cmds, _n, _m = LPU.plan(_cfg(t), installed, {direct.id: 10}, static=True)
    deleted = {c.split("-net ")[1].split()[0] for _r, c in cmds if c.startswith("route del")}
    known = {net for routes in installed.values() for (net, _m2) in routes}
    assert deleted <= known


def test_a_structural_change_is_detected():
    t, *_ = _triangle()
    before = LPU.structure(t)
    t.add_device("host")
    assert LPU.structure(t) != before


# ---- the real window, executing the plan ------------------------------------------- #
def _running_window(t):
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w._set_topology(t)
    cfg = w._gloader.compile(w.ctx.topology)
    w._run_links = (LPU.structure(w.ctx.topology), LPU.link_costs(w.ctx.topology),
                    LPU.installed_routes(cfg),
                    w.ctx.topology.routing_mode != "dynamic")
    w._running, w._workdir = True, "/nonexistent"
    sent = []
    w.element_query = lambda name, cmd: (sent.append((name, cmd)), "")[1]
    return app, w, sent


def _wait(app, sent, n=1, tries=200):
    import time
    for _ in range(tries):
        app.processEvents()
        if len(sent) >= n:
            return
        time.sleep(0.01)


def test_editing_a_cost_while_running_sends_it_to_both_routers():
    t, direct, _l, _r = _triangle()
    t.routing_mode = "dynamic"
    app, w, sent = _running_window(t)
    w.api.set_link_property(direct.id, "cost", 7)
    _wait(app, sent, 2)
    assert sorted(sent) == [("R1", "ifconfig mod tun1 -metric 7"),
                            ("R2", "ifconfig mod tun1 -metric 7")]


def test_static_mode_also_pushes_the_recomputed_routes():
    t, direct, left, right = _triangle()
    for l, c in ((left, 2), (right, 3)):
        LP.set_prop(l, "cost", c)
    app, w, sent = _running_window(t)
    w.api.set_link_property(direct.id, "cost", 10)
    _wait(app, sent, 3)
    assert any(c.startswith("route add") and n == "R1" for n, c in sent)
    sent.clear()
    w.api.set_link_property(direct.id, "fail_after", 60)        # not a cost: nothing to push
    _wait(app, sent, 1, tries=30)
    assert sent == []


def test_a_cost_edit_after_the_topology_changed_is_not_pushed():
    t, direct, _l, _r = _triangle()
    app, w, sent = _running_window(t)
    w.api.add_device("host")                                    # the lab no longer matches
    w.api.set_link_property(direct.id, "cost", 9)
    _wait(app, sent, 1, tries=30)
    assert sent == []


def test_the_hud_labels_edges_with_cost_once_weighted():
    t, direct, left, right = _triangle()
    app, w, _sent = _running_window(t)
    r1, r2 = w.ctx.topology.find_by_name("R1").id, w.ctx.topology.find_by_name("R2").id
    assert w._hud_cost_of(r1, r2) is None                      # unweighted: no label at all
    w.api.set_link_property(direct.id, "cost", 4)
    assert w._hud_cost_of(r1, r2) == 4
    assert w._hud_cost_of(r2, r1) == 4
