"""The Missions mode button: model-gated like Wizard/Coach, part of the exclusive mode switch,
and entering it shows a lesson picker whose chips launch a mission with the HUD."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QPushButton

from gini.ui.main_window import MainWindow


class _Chunk:
    def __init__(self, t=""):
        self.text = t
        self.tool_call = None


class _Backend:
    def chat(self, messages, tools=None, stream=False):
        yield _Chunk("Build it — clock's running!")


def _win_with_model():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.assistant._loop = SimpleNamespace(backend=_Backend(), brief="")
    w.assistant._refresh_mode_availability()
    return app, w


def test_missions_button_is_a_model_gated_mode():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    a = w.assistant
    assert a._missions_btn in a._mode_group.buttons()
    assert not a._missions_btn.isEnabled()               # no model → disabled
    a._loop = SimpleNamespace(backend=_Backend(), brief="")
    a._refresh_mode_availability()
    assert a._missions_btn.isEnabled()


def test_entering_missions_shows_a_vertical_picker():
    app, w = _win_with_model()
    a = w.assistant
    a._missions_btn.setChecked(True)
    assert a.missions_mode
    # missions are listed in the dedicated VERTICAL picker (not the horizontal follow-up row,
    # which would run off-screen with long labels)
    buttons = [a._picker_lay.itemAt(i).widget() for i in range(a._picker_lay.count())]
    buttons = [b for b in buttons if isinstance(b, QPushButton)]
    assert len(buttons) >= 8                                # one per catalog archetype
    assert a._follow_lay.count() == 0                       # NOT crammed into the horizontal row


def test_picking_a_mission_starts_it_with_the_hud():
    app, w = _win_with_model()
    a = w.assistant
    a._missions_btn.setChecked(True)
    a._start_preview_mission("basic-lan")
    assert a._mission_ctrl is not None and a._mission_ctrl.active
    assert a._mission_panel.isVisibleTo(a) or not a._mission_panel.isHidden()


def test_leaving_missions_ends_the_mission():
    app, w = _win_with_model()
    a = w.assistant
    a._missions_btn.setChecked(True)
    a._start_preview_mission("basic-lan")
    a._chat_btn.setChecked(True)                          # switch away → mission ends
    assert a._mission_ctrl is None
    assert a._mission_panel.isHidden()
