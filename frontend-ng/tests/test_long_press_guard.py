"""A click must not become a long press when the GUI thread stalls.

Reported on a slow Linux box: "When I click on the router it is immediately registered as a long
press and the X-ray menu shows up. I never long-pressed."

Nothing was wrong with the press detection. A long press is a 480ms timer started on press and
stopped on release — which is correct only while events are delivered promptly. When the GUI
thread stalls (something expensive being built on the main thread), the press is processed, the
stall happens, and by the time the loop runs again BOTH the timer expiry and the mouse release
are queued. Qt delivers the timer first, so the X-ray fires and the release arrives too late to
stop it.

The fix asks the mouse what it is doing NOW instead of trusting the event backlog:
QApplication.mouseButtons() is live state, not a queued event. Button already up => that was a
click the stall made look slow.

Note this is a SYMPTOM guard. It makes the gesture honest under stalls; it does not make the
stall go away, and the stall is worth fixing on its own.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _view(app):
    from gini.ui import MainWindow
    w = MainWindow(app)
    w.api.add_device("router", x=0, y=0)
    return w, w.canvas


def test_the_timer_does_not_fire_the_xray_with_the_button_already_up(app):
    """The reported bug. No button is held in an offscreen test, which is exactly the state a
    released-too-late click leaves behind."""
    w, view = _view(app)
    did = next(iter(w.ctx.topology.devices))
    view._lp_node = view.scene_.nodes[did]
    fired = []
    view._fire_xray = lambda: fired.append(1)
    view._on_lp_timeout()
    assert not fired, "an ordinary click opened the X-ray ring"
    assert view._lp_node is None, "left the press armed; the next timer would fire it"


def test_a_held_button_still_opens_the_xray(app, monkeypatch):
    """The guard must not break the real gesture — that would trade one bug for a worse one."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    w, view = _view(app)
    did = next(iter(w.ctx.topology.devices))
    view._lp_node = view.scene_.nodes[did]
    fired = []
    view._fire_xray = lambda: fired.append(1)
    monkeypatch.setattr(QApplication, "mouseButtons", staticmethod(lambda: Qt.LeftButton))
    view._on_lp_timeout()
    assert fired, "a genuine long press no longer opens the X-ray"


def test_the_content_path_is_independent_of_the_gesture(app):
    """_fire_xray is what the X-ray tests drive, and it must stay free of button state — the
    ring's CONTENT has nothing to do with whether a finger is down. Keeping the gesture decision
    in _on_lp_timeout is what lets both be tested."""
    import inspect

    from gini.ui.canvas import CanvasView
    assert "mouseButtons" not in inspect.getsource(CanvasView._fire_xray), (
        "the gesture guard leaked into _fire_xray; the X-ray content tests would need a fake mouse")
    assert "mouseButtons" in inspect.getsource(CanvasView._on_lp_timeout)
