"""M7 polish: anti-spam dedupe of game-master lines, empty-line guard, and the hint command."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _win():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.assistant._loop = SimpleNamespace(backend=SimpleNamespace(
        chat=lambda *A, **K: iter([SimpleNamespace(text="ok", tool_call=None)])), brief="")
    w.assistant._refresh_mode_availability()
    return app, w


def _gini_lines(a):
    return [t for r, t, *_ in a._messages if r == "GINI"]


def test_duplicate_and_empty_game_master_lines_are_dropped():
    app, w = _win()
    a = w.assistant
    before = len(_gini_lines(a))
    a._apply_mission_ui(("say", "Warmer — nice progress."))
    a._apply_mission_ui(("say", "Warmer — nice progress."))   # exact duplicate → dropped
    a._apply_mission_ui(("say", "   "))                        # empty → dropped
    a._apply_mission_ui(("say", "Now add the router."))        # new → posted
    lines = _gini_lines(a)[before:]
    assert lines == ["Warmer — nice progress.", "Now add the router."]


def test_hint_command_routes_to_the_game_master():
    app, w = _win()
    a = w.assistant
    a._missions_btn.setChecked(True)
    a._start_preview_mission("basic-lan")
    seen = []
    a._dispatch_mission = lambda method, *args: seen.append((method, args))
    a._handle("hint")
    assert seen and seen[0][0] == "ask" and "hint" in seen[0][1][0].lower()
