"""Quitting is blocked while a topology is running (⌘Q / menu / window close)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _win():
    # GiniApplication, not a bare QApplication: the ⌘Q guard now lives on the application object
    # rather than in an app-wide event filter, so this is what production actually runs.
    from gini.ui.app import GiniApplication
    app = QApplication.instance() or GiniApplication([])
    return app, MainWindow(app)


def test_close_is_blocked_while_running():
    app, w = _win()
    logs = []
    w.ctx.bus.log.connect(lambda lvl, msg: logs.append(msg))
    w._running = True
    ev = QCloseEvent(); ev.accept()
    w.closeEvent(ev)                                    # ⌘Q / File▸Quit route here (via self.close)
    assert not ev.isAccepted()                          # quit vetoed
    assert any("Stop" in m for m in logs)               # told the user in the console
    w.close  # noqa: B018


def test_close_proceeds_when_idle():
    app, w = _win()
    w._running = False
    ev = QCloseEvent(); ev.accept()
    w.closeEvent(ev)
    assert ev.isAccepted()                              # idle → normal quit


def test_the_guard_blocks_app_quit_while_running():
    app, w = _win()
    w._running = True
    assert w._quit_blocked() is True                    # ⌘Q backstop consumes the quit
    w._running = False
    assert w._quit_blocked() is False                   # idle → let it through


def test_the_application_consults_the_guard():
    """End to end: a real QEvent.Quit through GiniApplication.event()."""
    from gini.ui.app import GiniApplication
    app = QApplication.instance()
    if not isinstance(app, GiniApplication):
        import pytest
        pytest.skip("another QApplication already owns this process")
    blocked = []
    app.quit_guard = lambda: (blocked.append(1), True)[1]
    assert app.event(QEvent(QEvent.Type.Quit)) is True
    assert blocked, "the application did not consult quit_guard"
    app.quit_guard = None


def test_a_broken_guard_cannot_trap_the_user_in_the_app():
    """A guard that raises must fall through to Qt, not propagate — otherwise a bug anywhere in
    the guard leaves the user unable to quit."""
    from gini.ui.app import GiniApplication
    app = QApplication.instance()
    if not isinstance(app, GiniApplication):
        import pytest
        pytest.skip("another QApplication already owns this process")
    app.quit_guard = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        app.event(QEvent(QEvent.Type.Quit))         # must not raise
    finally:
        app.quit_guard = None


def test_main_window_installs_no_application_wide_event_filter():
    """The regression that cost a day: an app-wide filter taps EVERY event for EVERY QObject, and
    PySide segfaults marshalling QtQuick objects it has no bindings for — which is what the OS Zoo
    and headful Desktop screen produce via QWebEngineView's internal QQuickWidget.

    Checks the source, because the damage is done at install time and there is no Qt API to ask
    'what filters are on the application'.
    """
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "gini" / "ui" / "main_window.py"
    tree = ast.parse(src.read_text())
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "installEventFilter"
           and "app" in ast.dump(n.func.value).lower()]
    assert not bad, (
        f"main_window.py installs an application-wide event filter at line(s) {bad}. "
        f"That segfaults on QWebEngineView hover — use GiniApplication.quit_guard (ui/app.py).")

    assert not any(isinstance(n, ast.FunctionDef) and n.name == "eventFilter"
                   for n in ast.walk(tree)), (
        "MainWindow defines eventFilter again — if it is installed on the application this "
        "reintroduces the QWebEngineView segfault.")
