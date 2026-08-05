"""A project holds a FAMILY of experiments that share one AI context.

The point of grouping them: a student working through a lab (build a LAN → route between two LANs →
convert it to SDN) keeps one tutor conversation across all three, instead of the assistant losing
its memory every time they move on. These tests pin that, plus the v1→v2 migration so existing
single-experiment projects keep opening.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from PySide6.QtWidgets import QApplication

from gini.domain.topology import Topology
from gini.services import project as P
from gini.services.project import FIRST_EXPERIMENT  # noqa: F401  (referenced as P.FIRST_EXPERIMENT)


def _app():
    return QApplication.instance() or QApplication([])


# -- the format -------------------------------------------------------------- #
def test_a_project_holds_several_experiments(tmp_path):
    proj = tmp_path / "Lab1"
    t1 = Topology("Basic LAN"); t1.add_device("switch", "S1")
    P.save_project_dir(proj, t1, name="Lab1", brief="a lab", ai_state={"messages": [["GINI", "hi"]]},
                       experiment="Basic LAN")
    t2 = Topology("Routed LAN"); t2.add_device("router", "R1")
    P.save_project_dir(proj, t2, name="Lab1", brief="a lab", ai_state={"messages": [["GINI", "hi"]]},
                       experiment="Routed LAN")

    assert sorted(e["name"] for e in P.list_experiments(proj)) == ["Basic LAN", "Routed LAN"]
    # each experiment keeps its own topology…
    assert any(d.type_key == "switch" for d in P.load_experiment(proj, "Basic LAN").devices.values())
    assert any(d.type_key == "router" for d in P.load_experiment(proj, "Routed LAN").devices.values())
    # …while the brief and the AI conversation are stored ONCE, at the project level
    assert (proj / "ai.json").exists() and not (proj / "experiments" / "ai.json").exists()
    data = P.load_project_dir(proj)
    assert data["brief"] == "a lab" and data["ai_state"]["messages"] == [["GINI", "hi"]]
    assert data["experiment"] == "Routed LAN"           # the last one saved is current


def test_switching_experiments_keeps_one_shared_conversation(tmp_path):
    proj = tmp_path / "Lab2"
    ai = {"messages": [["you", "what is a switch?"], ["GINI", "a layer-2 device"]]}
    P.save_project_dir(proj, Topology("A"), name="Lab2", brief="b", ai_state=ai, experiment="A")
    P.save_project_dir(proj, Topology("B"), name="Lab2", brief="b", ai_state=ai, experiment="B")
    for exp in ("A", "B"):
        P.save_project_dir(proj, P.load_experiment(proj, exp), name="Lab2", brief="b",
                           ai_state=ai, experiment=exp)
        assert P.load_project_dir(proj)["ai_state"]["messages"] == ai["messages"]


def test_rename_and_delete_experiment(tmp_path):
    proj = tmp_path / "Lab3"
    P.save_project_dir(proj, Topology("A"), name="Lab3", experiment="A")
    P.save_project_dir(proj, Topology("B"), name="Lab3", experiment="B")
    assert P.rename_experiment(proj, "A", "A2")
    assert sorted(e["name"] for e in P.list_experiments(proj)) == ["A2", "B"]
    assert not P.rename_experiment(proj, "A2", "B")     # refuses to clobber an existing one
    assert P.delete_experiment(proj, "A2")
    assert [e["name"] for e in P.list_experiments(proj)] == ["B"]


def test_deleting_the_current_experiment_leaves_a_loadable_project(tmp_path):
    proj = tmp_path / "Lab4"
    P.save_project_dir(proj, Topology("A"), name="Lab4", experiment="A")
    P.save_project_dir(proj, Topology("B"), name="Lab4", experiment="B")   # B is current
    P.delete_experiment(proj, "B")
    data = P.load_project_dir(proj)                     # must not point at the deleted file
    assert data["experiment"] == "A"


def test_names_with_path_characters_are_made_safe(tmp_path):
    proj = tmp_path / "Lab5"
    P.save_project_dir(proj, Topology("x"), name="Lab5", experiment="LAN / v2: draft")
    names = [e["name"] for e in P.list_experiments(proj)]
    assert names and "/" not in names[0] and ":" not in names[0]


# -- migration --------------------------------------------------------------- #
def test_a_v1_project_migrates_in_place_and_still_opens(tmp_path):
    """The old layout put one `topology.gini` at the project root. Opening it must move that into
    experiments/ without the user noticing — and without losing the brief or the conversation."""
    from gini.services.persistence import save_project as save_topology
    proj = tmp_path / "OldLab"
    proj.mkdir()
    t = Topology("OldLab"); t.add_device("host", "M1")
    save_topology(t, proj / "topology.gini")
    (proj / "project.json").write_text('{"name": "OldLab", "brief": "old brief"}')
    (proj / "ai.json").write_text('{"messages": [["GINI", "remembered"]]}')

    assert P.is_project_dir(proj)                       # recognised before migration
    data = P.load_project_dir(proj)
    assert not (proj / "topology.gini").exists()        # moved…
    assert (proj / "experiments" / f"{P.FIRST_EXPERIMENT}.gini").exists()
    assert data["experiment"] == P.FIRST_EXPERIMENT     # neutral name, not the project's
    assert any(d.type_key == "host" for d in data["topology"].devices.values())
    assert data["brief"] == "old brief"                 # nothing lost
    assert data["ai_state"]["messages"] == [["GINI", "remembered"]]
    P.load_project_dir(proj)                            # idempotent — a second open is fine


# -- the UI ------------------------------------------------------------------ #
def test_new_experiment_keeps_the_conversation_but_new_project_clears_it(monkeypatch):
    _app()
    from gini.ui.main_window import MainWindow
    w = MainWindow(QApplication.instance())

    w.assistant._post("you", "remember this")
    before = len(w.assistant.ai_state()["messages"])

    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        staticmethod(lambda *a, **k: ("Second", True)))
    w._new_experiment()
    assert w._experiment == "Second"
    # the conversation carried over (plus a marker noting the canvas changed)
    msgs = w.assistant.ai_state()["messages"]
    assert len(msgs) > before and any("remember this" in str(m) for m in msgs)
    assert any("Second" in str(m) for m in msgs)        # the switch is visible in the transcript

    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        staticmethod(lambda *a, **k: ("OtherProject", True)))
    w._new_project()
    assert w.assistant.ai_state()["messages"] == []     # a NEW PROJECT is a fresh context


def test_no_path_ever_names_an_experiment_after_its_project(monkeypatch):
    """The invariant, pinned once instead of per-code-path. Three different routes create a
    project (New project, Save-As, and the auto-created Default), and every one of them used to
    name the first experiment after the project — so the experiment list read as though the
    project were an item inside itself. `_persist_current_project` now falls back to the neutral
    name, so no caller can reintroduce it by forgetting to set `_experiment`."""
    _app()
    from gini.ui.main_window import MainWindow
    w = MainWindow(QApplication.instance())

    # (a) the auto-created Default project
    w._open_or_create_default()
    assert w._experiment == P.FIRST_EXPERIMENT
    assert "Default" not in w._experiments()

    # (b) New project…
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        staticmethod(lambda *a, **k: ("Project-01", True)))
    w._new_project()
    assert w._experiments() == [P.FIRST_EXPERIMENT]
    assert "Project-01" not in w._experiments()

    # (c) Save project as… — even with `_experiment` cleared, the fallback holds the line
    w._experiment = None
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        staticmethod(lambda *a, **k: ("Project-02", True)))
    w._save_project_as()
    assert w._experiments() == [P.FIRST_EXPERIMENT]
    assert "Project-02" not in w._experiments()


def test_experiments_round_trip_through_the_window(monkeypatch):
    _app()
    from gini.ui.main_window import MainWindow
    w = MainWindow(QApplication.instance())

    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        staticmethod(lambda *a, **k: ("Lab", True)))
    w._new_project()
    w.ctx.add_device("switch", 10, 10)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        staticmethod(lambda *a, **k: ("Routed", True)))
    w._new_experiment()
    w.ctx.add_device("router", 20, 20)

    assert sorted(w._experiments()) == [P.FIRST_EXPERIMENT, "Routed"]
    assert "Lab" not in w._experiments()                # the PROJECT is never an experiment
    w._switch_experiment(P.FIRST_EXPERIMENT)            # back to the first
    assert w._experiment == P.FIRST_EXPERIMENT
    kinds = {d.type_key for d in w.ctx.topology.devices.values()}
    assert "switch" in kinds and "router" not in kinds  # its own topology came back
