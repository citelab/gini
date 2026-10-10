"""Editing a link's cost and failure model, end to end through the app (Phase 1 of
docs/design/link-properties.md).

Before this a link could be selected and deleted, and that was all: selecting one left the
inspector on "No selection", nothing about it could be set, and the compiler lost which drawn link
each UDP pair belonged to. These walk the whole path a teacher's edit takes -- select the link on
the canvas, set its cost in the inspector, see it on the canvas, have it recorded in the proof
chain, and have it reach both ends of the link in the runtime config -- plus the same edits made
by the AI through its tools.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QSpinBox

from gini.domain import link_props as LP
from gini.ui.main_window import MainWindow


def _win() -> MainWindow:
    app = QApplication.instance() or QApplication([])
    return MainWindow(app)


def _lab(w):
    """R1 -- R2 -- S1 -- M1: two router links and one host-to-switch cable."""
    api = w.api
    r1, r2 = api.add_device("router", name="R1")["id"], api.add_device("router", name="R2")["id"]
    s1, m1 = api.add_device("switch", name="S1")["id"], api.add_device("host", name="M1")["id"]
    rr = api.connect(r1, r2)["id"]
    rs = api.connect(r2, s1)["id"]
    sm = api.connect(m1, s1)["id"]
    return rr, rs, sm


def _settle():
    QApplication.processEvents()                 # the inspector rebuilds on a deferred timer
    QApplication.processEvents()


def _select_edge(w, link_id):
    sc = w.canvas.scene_
    sc.clearSelection()
    sc.edges[link_id].setSelected(True)
    _settle()


def _spin(w, name):
    return w.inspector.findChild(QSpinBox, name) or w.inspector.findChild(QDoubleSpinBox, name)


# ---- the API the inspector and the AI share ------------------------------------ #
def test_a_link_is_found_by_id_or_by_its_two_ends():
    w = _win()
    rr, _rs, _sm = _lab(w)
    for ref in (rr, "R1-R2", "R2 <-> R1", "R1,R2", ("R1", "R2")):
        assert w.api.get_link(ref)["id"] == rr


def test_names_with_hyphens_still_resolve():
    w = _win()
    a = w.api.add_device("router", name="core-1")["id"]
    b = w.api.add_device("router", name="edge-2")["id"]
    lid = w.api.connect(a, b)["id"]
    assert w.api.get_link("core-1-edge-2")["id"] == lid
    assert w.api.get_link("core-1 <-> edge-2")["id"] == lid


def test_unlinked_or_ambiguous_ends_are_refused_in_words():
    w = _win()
    _lab(w)
    with pytest.raises(KeyError, match="not linked"):
        w.api.get_link("R1-M1")
    r1 = w.ctx.topology.find_by_name("R1").id
    r2 = w.ctx.topology.find_by_name("R2").id
    w.api.connect(r1, r2)                                  # a second, parallel link
    with pytest.raises(KeyError, match="give the link id"):
        w.api.get_link("R1-R2")


def test_setting_a_property_validates_and_announces_it():
    w = _win()
    rr, _rs, _sm = _lab(w)
    seen = []
    w.ctx.bus.link_changed.connect(seen.append)
    out = w.api.set_link_property("R1-R2", "cost", 4)
    assert out["cost"] == 4 and w.ctx.topology.links[rr].props == {"cost": 4}
    assert seen == [rr]
    with pytest.raises(ValueError, match="1 to 15"):
        w.api.set_link_property("R1-R2", "cost", 16)
    assert w.ctx.topology.links[rr].props == {"cost": 4}   # a refused value changes nothing


def test_an_attachment_has_no_link_properties():
    w = _win()
    m = w.api.add_device("host", name="H")["id"]
    p = w.api.add_device("ping_probe", name="P")["id"]
    att = w.api.connect(p, m)["id"]
    assert w.ctx.topology.links[att].kind == "attach"
    with pytest.raises(ValueError, match="not a cable"):
        w.api.set_link_property(att, "cost", 3)


def test_the_ai_can_read_and_set_link_properties():
    from gini.agent.tools.registry import build_registry
    w = _win()
    rr, _rs, _sm = _lab(w)
    reg = build_registry(w.api)
    out = reg.execute("set_link_property", {"link": "R1-R2", "key": "fail_after", "value": "90"})
    assert out["fail_after"] == 90.0
    info = reg.execute("inspect_link", {"link": rr})
    assert info["a"] == "R1" and info["b"] == "R2" and info["cost_applies"] is True
    assert "fails after ≈ 90 s" in info["summary"]


def test_the_mcp_server_declares_the_same_link_tools():
    """Tools are declared twice, in the registry and the MCP server; keep the link ones in step."""
    import gini.agent.mcp_server as mcp
    src = open(mcp.__file__, encoding="utf-8").read()
    for name in ("set_link_property", "inspect_link"):
        assert f'call("{name}"' in src


# ---- selecting a link on the canvas, editing it in the inspector --------------- #
def test_selecting_a_link_opens_it_in_the_inspector():
    w = _win()
    rr, _rs, _sm = _lab(w)
    _select_edge(w, rr)
    assert w.ctx.selected_link_id == rr and w.ctx.selected_id is None
    assert w.inspector.name_lbl.text() == "R1 ↔ R2"
    assert w.inspector.type_lbl.text() == "Network link"
    visible = [w.inspector.tabs.tabText(i) for i in range(w.inspector.tabs.count())
               if w.inspector.tabs.isTabVisible(i)]
    assert visible == ["Properties"]                       # a link has nothing else to show
    cost = _spin(w, "LinkCost")
    assert cost is not None and cost.isEnabled() and cost.value() == 1


def test_a_host_to_switch_cable_has_no_cost_to_set():
    w = _win()
    _rr, _rs, sm = _lab(w)
    _select_edge(w, sm)
    assert not _spin(w, "LinkCost").isEnabled()            # no router, no routing cost
    assert _spin(w, "LinkFailAfter").isEnabled()           # but it can still fail


def test_editing_in_the_inspector_changes_the_link():
    w = _win()
    rr, _rs, _sm = _lab(w)
    _select_edge(w, rr)
    cost = _spin(w, "LinkCost")
    cost.setValue(6)
    _settle()
    assert w.ctx.topology.links[rr].props == {"cost": 6}
    assert _spin(w, "LinkCost") is cost or _spin(w, "LinkCost").value() == 6
    fail, repair = _spin(w, "LinkFailAfter"), _spin(w, "LinkRepairAfter")
    assert not repair.isEnabled()                          # nothing to repair until it can fail
    fail.setValue(120)
    _settle()
    assert w.ctx.topology.links[rr].props == {"cost": 6, "fail_after": 120.0}
    assert _spin(w, "LinkRepairAfter").isEnabled()


def test_selecting_a_device_again_restores_the_device_view():
    w = _win()
    rr, _rs, _sm = _lab(w)
    _select_edge(w, rr)
    r1 = w.ctx.topology.find_by_name("R1").id
    w.canvas.scene_.clearSelection()
    w.canvas.scene_.nodes[r1].setSelected(True)
    _settle()
    assert w.ctx.selected_link_id is None and w.ctx.selected_id == r1
    assert w.inspector.name_lbl.text() == "R1"
    assert all(w.inspector.tabs.isTabVisible(i) for i in range(w.inspector.tabs.count()))


def test_deleting_the_selected_link_clears_the_inspector():
    w = _win()
    rr, _rs, _sm = _lab(w)
    _select_edge(w, rr)
    w._delete_selected()
    _settle()
    assert rr not in w.ctx.topology.links and w.ctx.selected_link_id is None
    assert w.inspector.name_lbl.text() == "No selection"


def test_removing_a_device_drops_a_selected_link_of_its():
    w = _win()
    rr, _rs, _sm = _lab(w)
    _select_edge(w, rr)
    w.ctx.remove_device(w.ctx.topology.find_by_name("R1").id)
    assert w.ctx.selected_link_id is None


# ---- on the canvas -------------------------------------------------------------- #
def test_a_plain_lab_shows_no_cost_labels():
    w = _win()
    _lab(w)
    sc = w.canvas.scene_
    assert not sc.link_labels
    assert all(e._label == "" for e in sc.edges.values())


def test_once_weighted_every_router_link_shows_its_cost():
    w = _win()
    rr, rs, sm = _lab(w)
    w.api.set_link_property(rr, "cost", 4)
    sc = w.canvas.scene_
    assert sc.link_labels
    assert sc.edges[rr]._label == "4"
    assert sc.edges[rs]._label == "1"                      # the cost-1 router link is labelled too
    assert sc.edges[sm]._label == ""                       # no router: no cost to show
    w.api.set_link_property(rr, "cost", 1)                 # unweighted again: labels go away
    assert not sc.link_labels and sc.edges[rs]._label == ""


def test_a_link_set_to_fail_is_marked_and_its_tooltip_says_why():
    w = _win()
    _rr, _rs, sm = _lab(w)
    w.api.set_link_property(sm, "fail_after", 60)
    e = w.canvas.scene_.edges[sm]
    assert e._label == "↯"
    assert "M1 ↔ S1" in e.toolTip() and "fails after ≈ 60 s" in e.toolTip()
    assert "cost" not in e.toolTip()                       # meaningless without a router


# ---- the proof chain ----------------------------------------------------------- #
def test_a_link_edit_is_recorded_and_narrated_as_a_configure(tmp_path):
    from gini.domain import narration
    from gini.domain import proof as P
    from gini.domain import proof_events as ev
    from gini.services.proof_recorder import ProofRecorder
    from test_proof_events import CODE, FakeCtx
    ctx = FakeCtx()
    rec = ProofRecorder(ctx, store=P.ChainStore(tmp_path))
    rec.attach()
    rec.arm(CODE)
    a, b = ctx.add_device("router"), ctx.add_device("router")
    link = ctx.add_link(a, b)
    LP.set_prop(link, "cost", 4)
    ctx.bus.link_changed.emit(link.id)
    last = rec._chain.entries[-1]
    assert last.kind == ev.CONFIGURE and last.data["edge"] == "link"
    assert last.data["changes"] == {"cost": "4"}
    assert narration.describe(last) == f"Configured {a.name} ↔ {b.name}: cost = 4."
    n = rec.count
    ctx.bus.link_changed.emit(link.id)                     # nothing changed: nothing recorded
    assert rec.count == n


# ---- the runtime config ------------------------------------------------------- #
def _runtime(w, docker=True):
    from gini.services.compiler import RuntimeCompiler
    return RuntimeCompiler().compile(w.ctx.topology).to_runtime(docker=docker)


def test_both_ends_of_a_link_learn_its_properties():
    w = _win()
    rr, rs, sm = _lab(w)
    w.api.set_link_property(rr, "cost", 4)
    w.api.set_link_property(rr, "fail_after", 120)
    rt = _runtime(w)
    by_name = {r["name"]: r for r in rt["routers"]}
    r1_link = by_name["r1"]["ifaces"][0]["port"]["link"]
    r2_links = [i["port"]["link"] for i in by_name["r2"]["ifaces"]]
    assert r1_link == {"id": rr, "cost": 4, "fail_after": 120.0, "repair_after": 0.0}
    assert r1_link in r2_links                              # the same record at the far end
    sw_ids = {p["link"]["id"] for p in rt["switches"][0]["ports"]}
    assert sw_ids == {rs, sm}                               # switch ports know their cables
    assert rt["machines"][0]["ifaces"][0]["port"]["link"]["id"] == sm


def test_the_compose_file_still_parses():
    """The configs are JSON inside single-quoted YAML; a link id or value must not break that."""
    yaml = pytest.importorskip("yaml")
    from gini.services.compiler import RuntimeCompiler
    from gini.services.orchestrator import _compose
    w = _win()
    rr, _rs, _sm = _lab(w)
    w.api.set_link_property(rr, "cost", 7)
    w.ctx.topology.links[rr].id = "link'9"                 # a hostile id from a hand-edited file
    doc = yaml.safe_load(_compose(RuntimeCompiler().compile(w.ctx.topology)))
    assert doc["services"]
    rt = _runtime(w)
    ids = [i["port"]["link"]["id"] for r in rt["routers"] for i in r["ifaces"]]
    assert "link9" in ids and not any("'" in i for i in ids)


def test_a_recipe_link_can_carry_properties(monkeypatch):
    from gini.domain import recipes
    w = _win()
    base = recipes.RECIPES[0]
    pair = base.links[0]
    weighted = recipes.Recipe(**{**base.__dict__,
                                 "links": ((pair[0], pair[1], {"props": {"fail_after": 45}}),)
                                 + tuple(base.links[1:])})
    monkeypatch.setattr(recipes, "get_recipe", lambda rid: weighted)
    w.api.apply_recipe(base.id)
    assert any(l.props.get("fail_after") == 45.0 for l in w.ctx.topology.links.values())
