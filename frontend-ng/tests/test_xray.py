"""X-ray mode: long-press a node and its valid neighbours appear as ghost previews you
can tap to add (already connected)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.canvas import GhostItem
from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def test_long_press_spawns_ghost_ring_of_valid_neighbours():
    app, w = _win()
    router = w.api.add_device("router", x=200, y=200)["id"]
    app.processEvents()
    view = w.canvas
    view._lp_node = view.scene_.nodes[router]
    view._fire_xray()

    assert view._xray_on
    assert view.scene_.nodes[router]._xray == "target"     # the pressed node is ringed
    types = {g.type_key for g in view._ghosts}
    assert {"switch", "host", "router"} <= types           # valid router neighbours
    assert "pod" not in types                              # a Pod can't attach to a router
    # every ghost + its connector line is a live scene item
    assert all(g.scene() is view.scene_ for g in view._ghosts)


def test_tap_ghost_creates_a_connected_element():
    app, w = _win()
    router = w.api.add_device("router", x=200, y=200)["id"]
    app.processEvents()
    view = w.canvas
    n0 = len(w.ctx.topology.devices)

    view._lp_node = view.scene_.nodes[router]
    view._fire_xray()
    ghost = next(g for g in view._ghosts if g.type_key == "switch")
    view._activate_ghost(ghost)

    devs = w.ctx.topology.devices
    assert len(devs) == n0 + 1
    new = [d for d in devs.values() if d.type_key == "switch"][-1]
    # the new element is wired to the long-pressed router
    linked = {(l.source_id, l.target_id) for l in w.ctx.topology.links.values()}
    assert (router, new.id) in linked or (new.id, router) in linked
    assert w.ctx.selected_id == new.id                     # new element gets selected
    assert not view._xray_on                               # overlay cleared after adding


def test_new_element_lands_where_the_ghost_was():
    app, w = _win()
    router = w.api.add_device("router", x=200, y=200)["id"]
    app.processEvents()
    view = w.canvas
    view._lp_node = view.scene_.nodes[router]
    view._fire_xray()
    ghost = view._ghosts[0]
    gx, gy = ghost.pos().x(), ghost.pos().y()
    view._activate_ghost(ghost)
    new = [d for d in w.ctx.topology.devices.values() if d.id != router][-1]
    assert abs(new.x - gx) < 1 and abs(new.y - gy) < 1


def test_many_neighbours_are_capped_with_a_more_chip():
    app, w = _win()
    wa = w.api.add_device("web_app", x=200, y=200)["id"]   # ~16 valid partners
    app.processEvents()
    view = w.canvas
    view._lp_node = view.scene_.nodes[wa]
    view._fire_xray()

    assert len(view._ghosts) <= view.MAX_GHOSTS
    mores = [it for it in view._xray_items
             if isinstance(it, GhostItem) and it.type_key is None]
    assert len(mores) == 1                                 # exactly one '+N more' chip
    assert mores[0].why.startswith("+")


def test_clear_removes_ghosts_and_unrings():
    app, w = _win()
    router = w.api.add_device("router", x=200, y=200)["id"]
    app.processEvents()
    view = w.canvas
    view._lp_node = view.scene_.nodes[router]
    view._fire_xray()
    view.clear_xray()

    assert view._xray_items == [] and view._ghosts == []
    assert view.scene_.nodes[router]._xray is None
    assert not view._xray_on


def test_no_xray_overlay_for_a_grouping_box():
    # Region/VPC/Subnet are grouping boxes, not X-ray targets: hit-testing over a box
    # finds no node, so long-press never starts an overlay there.
    from PySide6.QtCore import QPointF
    app, w = _win()
    rg = w.api.add_device("region", x=200, y=200)["id"]
    app.processEvents()
    view = w.canvas
    assert rg in view.scene_.groups and rg not in view.scene_.nodes
    g = view.scene_.groups[rg]
    center = g.scenePos() + QPointF(g.inst.w / 2, g.inst.h / 2)
    assert view._node_at(view.mapFromScene(center)) is None     # a box is not a NodeItem
    assert not view._xray_on and view._ghosts == []
