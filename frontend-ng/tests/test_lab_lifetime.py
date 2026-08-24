"""A closed or replaced lab window must stop polling.

Diagnosed from a py-spy dump taken while the Router Lab appeared to hang on a slow Linux box:

    Thread MainThread (idle):  main (gini/__main__.py) -> app.exec()
    Thread (work): ... element_query ... router_lab.py:486
    Thread (work): ... element_query ... router_lab.py:658
    Thread (work): ... element_query ... router_lab.py:486
    Thread (work): ... element_query ... router_lab.py:658

Four live query threads for what should be one round of two, and two still running after the
window was closed. The main thread was IDLE the whole time — the app was never stalled, the
machine was saturated.

The cause is lifetime, not concurrency. These dialogs are parented to MainWindow, so rebinding
self._router_lab does not free the old one: Qt keeps every dialog ever opened alive as a child,
with its 2.5s timer running, each firing three `docker compose exec` calls at a router nobody is
looking at. Closing the window did not help either — there was no closeEvent.

On Linux the window manager paints a busy cursor when the app misses its _NET_WM_PING deadlines,
which is the "spinner" that kept appearing while the route table filled in perfectly well. It got
worse the longer a session ran, and a slow machine crossed the threshold first — which is why it
looked like a regression from whatever had been touched most recently.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Dev:
    name = "r1"
    id = "d1"
    type_key = "router"


def _lab(app):
    from gini.domain.router_modules import RouterProgram
    from gini.ui.router_lab import RouterLab
    lab = RouterLab(None, _theme(app), _Dev(), RouterProgram(), query_fn=lambda c: "")
    return lab


def _theme(app):
    from gini.ui.theme import ThemeManager
    if not hasattr(_theme, "_t"):
        _theme._t = ThemeManager(app)
    return _theme._t


def test_closing_the_lab_stops_the_poll(app):
    """The one that mattered: a closed window kept querying docker forever."""
    lab = _lab(app)
    lab.show()
    assert lab._live_timer.isActive(), "the poll never started"
    lab.close()
    assert not lab._live_timer.isActive(), (
        "a CLOSED Router Lab is still polling docker every 2.5s, forever")


def test_hiding_the_lab_stops_the_poll(app):
    """Hidden without a close is the same waste — the window polls something nobody can see."""
    lab = _lab(app)
    lab.show()
    lab.hide()
    assert not lab._live_timer.isActive()


def test_showing_it_again_resumes_the_poll(app):
    """Stopping must not be one-way, or reopening a lab shows a frozen table."""
    lab = _lab(app)
    lab.show()
    lab.close()
    lab.show()
    assert lab._live_timer.isActive(), "reopened lab never resumed polling"
    lab.close()


def test_opening_a_second_lab_retires_the_first(app):
    """Rebinding self._router_lab does NOT free the old dialog: it is parented to MainWindow, so
    Qt keeps it alive with its timer running. Ten double-clicks used to leave ten pollers."""
    from gini.ui import MainWindow
    w = MainWindow(app)
    w.api.add_device("router", x=0, y=0)
    did = next(iter(w.ctx.topology.devices))
    # _open_router_lab only passes a query_fn when a topology is RUNNING, and without one the
    # poll timer never starts — so the first version of this test passed with the leak present.
    # Mutation testing caught it: removing _retire_lab changed nothing.
    w._running = True
    w._workdir = "/nonexistent"               # never used: the timer is stopped before it fires

    w._open_router_lab(did)
    first = w._router_lab
    assert first is not None
    w._open_router_lab(did)
    assert w._router_lab is not first, "did not open a new lab"
    try:
        still_polling = first._live_timer.isActive()
    except RuntimeError:
        still_polling = False                 # C++ object gone: retired properly
    assert not still_polling, "the previous Router Lab is still polling docker in the background"


def test_retiring_a_lab_that_was_never_opened_is_harmless(app):
    from gini.ui import MainWindow
    w = MainWindow(app)
    w._retire_lab("_router_lab")              # must not raise
    w._retire_lab("_zoo_lab")


def test_retiring_survives_an_already_deleted_window(app):
    """The user can close a lab with the window button; Qt may have destroyed it by the time we
    come to retire it. Raising there would break opening the next one."""
    from gini.ui import MainWindow
    w = MainWindow(app)
    w.api.add_device("router", x=0, y=0)
    did = next(iter(w.ctx.topology.devices))
    w._open_router_lab(did)
    from shiboken6 import delete
    delete(w._router_lab)
    w._retire_lab("_router_lab")              # must not raise
    w._open_router_lab(did)                   # and the next open must still work
    assert w._router_lab is not None
