"""Projects: a project is a folder (topology + AI conversation + brief); switching
projects swaps the Ask GINI conversation and the model's memory, and feeds the
per-project brief into the AI context."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtWidgets import QApplication

from gini.agent.api import GiniAPI
from gini.agent.llm.backend import Message
from gini.agent.llm.fake import ScriptedBackend
from gini.agent.loop import AgentLoop
from gini.agent.tools.registry import build_registry
from gini.app import AppContext
from gini.domain import Topology
from gini.services import (
    is_project_dir, list_projects, load_project_dir, save_project_dir,
)


def _app():
    return QApplication.instance() or QApplication([])


# ---- storage layer (no Qt) ----------------------------------------------- #
def test_project_folder_round_trip(tmp_path):
    topo = Topology("Lab1")
    ai = {"messages": [["You", "hi", False, False]], "history": [{"role": "user", "content": "hi"}]}
    d = tmp_path / "Lab1"
    save_project_dir(d, topo, name="Lab1", brief="Build a VPC", ai_state=ai)

    assert is_project_dir(d)
    assert (d / "topology.gini").exists() and (d / "project.json").exists() and (d / "ai.json").exists()
    got = load_project_dir(d)
    assert got["name"] == "Lab1" and got["brief"] == "Build a VPC"
    assert got["ai_state"]["messages"][0][1] == "hi"
    assert isinstance(got["topology"], Topology)


def test_list_projects_sorted_by_mtime(tmp_path):
    save_project_dir(tmp_path / "A", Topology("A"), name="A")
    save_project_dir(tmp_path / "B", Topology("B"), name="B")
    names = [p["name"] for p in list_projects(tmp_path)]
    assert set(names) == {"A", "B"}
    # a bare file is NOT a project dir
    (tmp_path / "loose.gini").write_text("{}")
    assert {p["name"] for p in list_projects(tmp_path)} == {"A", "B"}


# ---- assistant AI-state + brief ------------------------------------------ #
def test_assistant_ai_state_and_brief_round_trip():
    from gini.ui.assistant import Assistant
    from gini.ui.theme.manager import ThemeManager
    app = _app()
    ctx = AppContext(); api = GiniAPI(ctx)
    a = Assistant(ctx, api, ThemeManager(app, "Dark"))
    a.set_loop(AgentLoop(ScriptedBackend([]), build_registry(api)))

    a._messages = [("You", "q1", False, False), ("GINI", "a1", False, True)]
    a._loop.history.append(Message("user", "q1"))
    a.set_brief("Teach subnetting")
    assert a._loop.brief == "Teach subnetting"        # brief reaches the model
    state = a.ai_state()

    # a different conversation, then restore the first
    a.clear_conversation()
    assert a._messages == [] and a.brief() == ""
    a.load_ai_state(state)
    assert [m[1] for m in a._messages] == ["q1", "a1"]
    assert any(m.content == "q1" for m in a._loop.history)


def test_brief_injected_into_model_context():
    api = GiniAPI(AppContext())
    loop = AgentLoop(ScriptedBackend([]), build_registry(api))
    loop.brief = "This lab is about VPC isolation."
    msgs = loop._messages()
    assert any(m.role == "system" and "VPC isolation" in m.content for m in msgs)


# ---- main window: switching projects swaps everything -------------------- #
def _window_with_loop():
    from gini.ui.main_window import MainWindow
    w = MainWindow(_app())
    w.assistant.set_loop(AgentLoop(ScriptedBackend([]), build_registry(w.api)))
    return w


def test_switching_projects_swaps_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    projs = tmp_path / "projects"
    w = _window_with_loop()
    assert hasattr(w, "_nav_btn")

    # project Alpha
    w._project_dir = str(projs / "Alpha")
    w.api.add_device("router")
    w.assistant._messages = [("You", "a1", False, False), ("GINI", "a2", False, False)]
    w.assistant.set_brief("Alpha framing")
    w._persist_current_project()

    # project Beta (fresh)
    w._set_topology(Topology("Beta")); w.assistant.clear_conversation()
    w._project_dir = str(projs / "Beta")
    w.api.add_device("switch")
    w.assistant._messages = [("You", "b1", False, False)]
    w.assistant.set_brief("Beta framing")
    w._persist_current_project()

    # switch back to Alpha -> Beta saved, Alpha restored
    w._switch_project(str(projs / "Alpha"))
    assert w._project_dir == str(projs / "Alpha")
    assert [m[1] for m in w.assistant._messages] == ["a1", "a2"]
    assert w.assistant.brief() == "Alpha framing" and w.assistant._loop.brief == "Alpha framing"
    assert any(d.type_key == "router" for d in w.ctx.topology.devices.values())
    assert w._nav_btn.text() == "Alpha"
    # Beta was persisted on the way out
    beta_ai = json.loads((projs / "Beta" / "ai.json").read_text())
    assert beta_ai["messages"][0][1] == "b1"


def test_cannot_switch_project_while_running(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    projs = tmp_path / "projects"
    w = _window_with_loop()
    w._project_dir = str(projs / "Alpha")
    w.api.add_device("router")
    w._persist_current_project()

    w._running = True
    w._update_status()
    assert not w._nav_btn.isEnabled()                 # navigator is ghosted while running
    before = w._project_dir
    w._switch_project(str(projs / "Beta"))            # refused — would yank a running lab
    assert w._project_dir == before

    w._running = False
    w._update_status()
    assert w._nav_btn.isEnabled()                     # back on once stopped


def test_dashboard_meter_resets_on_project_change():
    from gini.domain import Topology
    w = _window_with_loop()
    w.dashboard._accrued = 7.5
    w.dashboard._render()
    assert "7.50" in w.dashboard.total_lbl.text()
    w._set_topology(Topology("Fresh"))                # load a different project's topology
    assert w.dashboard._accrued == 0.0
    assert w.dashboard.total_lbl.text().strip() == "GINI $ 0.00"


def test_default_project_persists_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    # first-ever launch: no prior project -> should land in a persistent "Default"
    w = _window_with_loop()
    w.restore_last_project()
    assert w._project_dir is not None and w._nav_btn.text() == "Default"

    # chat in the default session, then quit (closeEvent -> persist)
    w.assistant._messages = [("You", "keep me", False, False), ("GINI", "ok", False, False)]
    w.api.add_device("router")
    w._persist_current_project()

    # restart: a brand-new window restores the Default project's conversation
    w2 = _window_with_loop()
    w2.restore_last_project()
    assert w2._nav_btn.text() == "Default"
    assert [m[1] for m in w2.assistant._messages] == ["keep me", "ok"]
    assert any(d.type_key == "router" for d in w2.ctx.topology.devices.values())


def test_restore_last_project(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from gini.app.paths import remember_project
    d = tmp_path / "projects" / "Saved"
    save_project_dir(d, Topology("Saved"), name="Saved", brief="hi",
                     ai_state={"messages": [["You", "hello", False, False]], "history": []})
    remember_project(str(d))

    w = _window_with_loop()
    w.restore_last_project()
    assert w._project_dir == str(d)
    assert w._nav_btn.text() == "Saved"
    assert [m[1] for m in w.assistant._messages] == ["hello"]


# ---- rename / delete ----------------------------------------------------- #
def test_rename_project(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from PySide6.QtWidgets import QInputDialog
    projs = tmp_path / "projects"
    w = _window_with_loop()
    w._project_dir = str(projs / "Old")
    w.api.add_device("router")
    w._persist_current_project()
    assert (projs / "Old").is_dir()

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("New", True)))
    w._rename_project()
    assert (projs / "New").is_dir() and not (projs / "Old").exists()
    assert w._nav_btn.text() == "New" and w._project_dir == str(projs / "New")
    assert any(d.type_key == "router" for d in w.ctx.topology.devices.values())  # work kept


def test_delete_project(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from PySide6.QtWidgets import QMessageBox
    projs = tmp_path / "projects"
    w = _window_with_loop()
    w._project_dir = str(projs / "Doomed")
    w.api.add_device("router")
    w._persist_current_project()
    assert (projs / "Doomed").is_dir()

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    w._delete_project()
    assert not (projs / "Doomed").exists() and w._project_dir is None
    w._persist_current_project()                       # must NOT recreate the deleted folder
    assert not (projs / "Doomed").exists()


def test_delete_declined_keeps_project(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    from PySide6.QtWidgets import QMessageBox
    projs = tmp_path / "projects"
    w = _window_with_loop()
    w._project_dir = str(projs / "Keep")
    w.api.add_device("router")
    w._persist_current_project()

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    w._delete_project()
    assert (projs / "Keep").is_dir() and w._project_dir == str(projs / "Keep")
