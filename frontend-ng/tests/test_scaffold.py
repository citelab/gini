"""Scaffold authoring — build a dependent fragment on top of a loaded provider.

A router fragment can't be built on an empty canvas; it needs an L2 LAN present. So the teacher
loads a provider (simple-lan) as a locked SCAFFOLD, builds the delta on top, and only the delta is
captured/saved. This covers the domain core: materializing a provider board, and delta-aware
objective derivation (the scaffold is excluded; a delta→scaffold link is kept).
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication

from gini.app.context import AppContext
from gini.domain import authoring as AU

LAN_OBJS = [
    {"id": "h", "say": "2 hosts", "check": "count(host) >= 2", "level": 1},
    {"id": "s", "say": "switch", "check": "exists(switch)", "level": 1},
    {"id": "l", "say": "wire", "check": "link(host, switch)", "level": 2},
]


def _app():
    return QApplication.instance() or QApplication([])


def _switch(ctx):
    return next(d for d in ctx.topology.devices.values() if d.type_key == "switch")


def test_materialize_builds_the_provider_board():
    _app()
    ctx = AppContext()
    ids = AU.materialize(ctx, LAN_OBJS)
    hosts = [d for d in ctx.topology.devices.values() if d.type_key == "host"]
    sw = [d for d in ctx.topology.devices.values() if d.type_key == "switch"]
    assert len(hosts) == 2 and len(sw) == 1 and len(ids) == 3
    assert len(ctx.topology.net_links()) == 2          # both hosts wired to the switch (star)


def test_delta_capture_excludes_the_scaffold_but_keeps_the_bridging_link():
    _app()
    ctx = AppContext()
    scaffold = set(AU.materialize(ctx, LAN_OBJS))       # the LAN dependency
    router = ctx.add_device("router", 300, 60)          # the DELTA: a router...
    ctx.add_link(router.id, _switch(ctx).id)            # ...wired to the LAN's switch

    delta = {o["check"] for o in AU.derive_objectives(ctx.topology, exclude=scaffold)}
    assert delta == {"exists(router)", "link(router, switch)"}   # ONLY the router's part

    full = {o["check"] for o in AU.derive_objectives(ctx.topology)}
    assert {"count(host) >= 2", "exists(switch)", "link(host, switch)"} <= full   # scaffold visible


def test_derive_contract_excludes_the_scaffold_from_provides():
    _app()
    ctx = AppContext()
    scaffold = set(AU.materialize(ctx, LAN_OBJS))
    router = ctx.add_device("router", 300, 60)
    ctx.add_link(router.id, _switch(ctx).id)
    provides, requires = AU.derive_contract(ctx.topology, exclude=scaffold)
    assert "router-gateway" in provides                 # the delta provides the router role...
    assert "switched-segment" not in provides           # ...not the scaffold's L2 role


def test_router_authoring_produces_two_slots_and_scoped_delta():
    _app()
    from gini.ui.fragment_manager import FragmentManager
    from gini.ui.main_window import MainWindow

    w = MainWindow(QApplication.instance())
    ctx = w.ctx
    fm = FragmentManager(w, ctx, author="prof")
    fm._create()
    fm.fid.setText("router")
    fm.spirit.setText("route between two LANs")

    # two LAN slots A and B (what _add_dependency does: materialize + tag + track the slot)
    a = set(AU.materialize(ctx, LAN_OBJS))
    for i in a:
        ctx.topology.devices[i].slot = "A"
    b = set(AU.materialize(ctx, LAN_OBJS))
    for i in b:
        ctx.topology.devices[i].slot = "B"
    fm._scaffold_ids = a | b
    fm._slots = [{"name": "A", "role": "network"}, {"name": "B", "role": "network"}]

    router = ctx.add_device("router", 400, 60)          # the delta, wired to BOTH switches
    swa = next(d for d in ctx.topology.devices.values() if d.type_key == "switch" and d.slot == "A")
    swb = next(d for d in ctx.topology.devices.values() if d.type_key == "switch" and d.slot == "B")
    ctx.add_link(router.id, swa.id); ctx.add_link(router.id, swb.id)
    fm._read_once()

    assert {s["check"] for s in fm._steps} == {
        "exists(router)", "link(router, switch@A)", "link(router, switch@B)"}   # slot-scoped delta
    d = fm._current_dict()
    assert "router-gateway" in d["provides"]
    assert "network" in d["requires"]                   # requires two `network` slots
    assert [s["name"] for s in d["slots"]] == ["A", "B"]  # the two named dependency sockets travel
    # the FULL authoring board (scaffold + delta) is saved with its slot tags, so the fragment can be
    # reloaded and re-certified — the slot-scoped predicates need the slotted devices present.
    stage = d.get("stage")
    assert stage and {dev.get("slot", "") for dev in stage["devices"]} == {"", "A", "B"}


def test_recorder_captures_only_the_delta_over_a_scaffold():
    _app()
    ctx = AppContext()
    scaffold = set(AU.materialize(ctx, LAN_OBJS))
    rec = AU.Recorder(exclude=scaffold)
    rec.capture(ctx.topology)
    assert rec.result() == []                           # nothing new beyond the scaffold yet

    router = ctx.add_device("router", 300, 60)
    ctx.add_link(router.id, _switch(ctx).id)
    rec.capture(ctx.topology)
    assert {o["check"] for o in rec.result()} == {"exists(router)", "link(router, switch)"}
