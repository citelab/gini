"""Wizard recipes: curated blueprints instantiate deterministically onto the canvas."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.agent.api import GiniAPI
from gini.app import AppContext
from gini.domain.recipes import RECIPES, get_recipe, suggest_recipes
from gini.ui.main_window import MainWindow


def _api():
    return GiniAPI(AppContext())


def test_every_recipe_uses_real_palette_elements():
    from gini.domain.devices import REGISTRY
    for r in RECIPES:
        refs = {e.ref for e in r.elements}
        for e in r.elements:
            assert e.type_key in REGISTRY, f"{r.id}: {e.type_key}"
        for a, b in r.links:                       # links reference declared elements
            assert a in refs and b in refs, f"{r.id}: bad link {a}-{b}"


def test_apply_recipe_builds_the_blueprint():
    api = _api()
    r = get_recipe("observability")
    res = api.apply_recipe("observability")
    assert res["recipe"] == "observability"
    assert len(res["added"]) == len(r.elements)
    assert res["links"] == len(r.links)
    # the canvas now has the elements, laid out (no two on the same spot)
    devs = list(api.ctx.topology.devices.values())
    assert len(devs) == len(r.elements)
    positions = {(d.x, d.y) for d in devs}
    assert len(positions) == len(devs)
    # and it includes the visualizers
    types = {d.type_key for d in devs}
    assert {"metrics", "dashboard", "web_app", "load_generator"} <= types


def test_apply_recipe_offsets_below_existing():
    api = _api()
    api.add_device("router", x=0, y=0)
    api.apply_recipe("load_test")
    ys = [d.y for d in api.ctx.topology.devices.values() if d.type_key != "router"]
    assert all(y > 0 for y in ys)                  # placed below the existing router


def test_suggest_recipes_matches_intent():
    # the offline ranker the LLM mirrors
    ids = [r.id for r in suggest_recipes("I want to visualize and monitor my system")]
    assert ids and ids[0] == "observability"
    ids2 = [r.id for r in suggest_recipes("stress test the throughput")]
    assert "load_test" in ids2


def test_inspector_shows_what_each_element_runs():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    h = w.api.add_device("host")["id"]
    w.ctx.select(h)
    # a NEW host is LEAN (Alpine) — that's the deliberate default, an order of magnitude smaller
    # than the Debian image. The Inspector must tell the truth about THIS host's image…
    note = w.inspector.runs_lbl.text()
    assert "LEAN" in note and "Alpine" in note
    assert "tcpdump" in note                              # the tools a student actually types
    assert "'full'" in note                               # …and how to get the heavy servers

    w.ctx.topology.devices[h].properties["Toolkit"] = "full"
    w.ctx.select(None); w.ctx.select(h)                   # re-render the Inspector
    full = w.inspector.runs_lbl.text()
    assert "tshark" in full and "Debian" in full          # batteries-included machine
    s3 = w.api.add_device("object_store")["id"]
    w.ctx.select(s3)
    assert "minio" in w.inspector.runs_lbl.text().lower()  # services show their image


def test_unknown_recipe_raises():
    api = _api()
    try:
        api.apply_recipe("nope")
        assert False, "should raise"
    except KeyError:
        pass


def test_wizard_mode_is_mutually_exclusive_with_explain():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    modes = []
    w.assistant.status_changed.connect(lambda m, b: modes.append(m))

    w.assistant._wizard_btn.setChecked(True)
    assert w.assistant.wizard_mode and not w.assistant.explain_mode
    assert modes[-1] == "Wizard mode"
    w.assistant._explain_btn.setChecked(True)        # entering Explain exits Wizard
    assert w.assistant.explain_mode and not w.assistant.wizard_mode
    assert not w.assistant._wizard_btn.isChecked()
