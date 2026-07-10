"""Context-aware Missions entry: the picker adapts to a Teaching-Center assignment, the describe
box routes free-form text to the composer, and entering a mission archives/restores the chat."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QPushButton

from gini.domain import catalog
from gini.ui.main_window import MainWindow


class _Chunk:
    def __init__(self, t=""):
        self.text = t
        self.tool_call = None


class _Backend:
    def chat(self, messages, tools=None, stream=False):
        yield _Chunk("Build it — clock's running!")


def _win(model=True):
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    if model:
        w.assistant._loop = SimpleNamespace(backend=_Backend(), brief="")
        w.assistant._refresh_mode_availability()
    return app, w


def _picker_buttons(a):
    ws = [a._picker_lay.itemAt(i).widget() for i in range(a._picker_lay.count())]
    return [b for b in ws if isinstance(b, QPushButton)]


def test_practice_state_when_no_teaching_center():
    app, w = _win()
    a = w.assistant
    a._missions_btn.setChecked(True)
    assert "practice" in a._picker_header.text().lower()
    assert len(_picker_buttons(a)) == len(catalog.all_archetypes())


def test_assigned_state_shows_only_mandatory_missions():
    app, w = _win()
    a = w.assistant
    a.ctx.teaching_center = SimpleNamespace(
        available_lessons=lambda: [{"id": "hw1", "title": "Homework 1"},
                                   {"id": "hw2", "title": "Homework 2"}],
        fetch_lesson=lambda lid: None)
    a._missions_btn.setChecked(True)
    assert "mandatory" in a._picker_header.text().lower()
    buttons = _picker_buttons(a)
    assert len(buttons) == 2                              # ONLY the assignment, not the catalog
    assert {b.text() for b in buttons} == {"Homework 1", "Homework 2"}


def test_describe_box_routes_to_the_composer():
    app, w = _win()
    a = w.assistant
    a._missions_btn.setChecked(True)
    seen = {}
    a._describe_mission = lambda text: seen.setdefault("text", text)
    a._handle("a firewall that protects a server")       # in missions mode, no active mission
    assert seen.get("text") == "a firewall that protects a server"


def test_describe_command_still_launches_a_named_mission():
    app, w = _win()
    a = w.assistant
    a._missions_btn.setChecked(True)
    a._handle("/mission basic-lan")
    assert a._mission_ctrl is not None and a._mission_ctrl.active


def test_topic_cloud_is_hidden_outside_chat_mode():
    app, w = _win()
    a = w.assistant
    assert a._stack.currentIndex() == 0                   # empty Chat → topic cloud
    a._missions_btn.setChecked(True)
    assert a._stack.currentIndex() != 0                   # Missions → cloud gone, panel/picker shown
    a._chat_btn.setChecked(True)
    assert a._stack.currentIndex() == 0                   # back to Chat → cloud returns


def test_worker_llm_activity_lights_the_thinking_indicator():
    app, w = _win()
    a = w.assistant
    assert not a._busy
    a._apply_mission_ui(("busy", True))                   # a worker LLM call starts
    assert a._busy                                        # thinking indicator on
    a._apply_mission_ui(("busy", True))                   # a second overlapping call
    a._apply_mission_ui(("busy", False))
    assert a._busy                                        # still one in flight
    a._apply_mission_ui(("busy", False))
    assert not a._busy                                    # all done → indicator clears


def test_entering_a_mission_archives_then_restores_chat():
    app, w = _win()
    a = w.assistant
    a._post("You", "what is a VLAN?")
    a._post("GINI", "A VLAN is…")
    before = list(a._messages)                            # whatever the panel held pre-mission
    assert any("VLAN" in t for _, t, *_ in before)
    a._missions_btn.setChecked(True)
    a._start_preview_mission("basic-lan")
    # the pre-mission conversation is stashed; the panel is a clean game-master session
    assert a._chat_archive is not None
    assert all("VLAN" not in text for _role, text, *_ in a._messages)
    a._chat_btn.setChecked(True)                          # leave missions → mission ends
    assert a._chat_archive is None
    assert a._messages == before                          # restored exactly as it was
