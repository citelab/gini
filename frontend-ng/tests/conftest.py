"""Shared test configuration.

Keep the Ask GINI panel OFFLINE (no model attached) by default. Many UI tests assert the
panel's offline/deterministic behaviour — model-gated buttons disabled (Wizard / Coach /
Missions), deterministic replies, no async LLM path. On a developer machine with a configured
*and running* Ollama, `MainWindow` auto-connects a model on construction (`_wire_llm`), which
breaks those assumptions and makes the suite pass or fail depending on whether Ollama happens to
be up. CI has no model, so the tests were written for the offline state; this fixture forces that
state everywhere so results are environment-independent. Tests that need a model attach one
explicitly (e.g. `assistant.set_loop(...)` or a fake backend).
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_gini_home(tmp_path, monkeypatch):
    """Never let the DEVELOPER'S OWN GINI state decide whether the suite passes.

    `MainWindow` loads `~/.gini/config.json` on construction. On a machine that is enrolled in a
    course, that config carries `tc_url`/`tc_course`/`tc_student` — so the app connects to the
    Teaching Center, pulls the released lessons, and the Missions picker correctly shows
    "Assigned Missions (Mandatory)". Tests written for an un-enrolled student then fail, on a
    perfectly healthy app: 'assert "practice" in "assigned missions (mandatory)"'.

    Point GINI_HOME at a fresh temp dir for every test, so the suite always sees a brand-new,
    un-enrolled, offline student — regardless of who is running it. (Tests that WANT a Center wire
    one explicitly; see test_teaching_center.py.)"""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "gini-home"))


@pytest.fixture(autouse=True, scope="module")
def _reap_windows_between_modules():
    """Destroy leftover top-level widgets after each test FILE, or the suite crawls.

    Tests never destroy their MainWindows (~285 widgets each), and several things cost O(live
    widgets) per new window:

      * `theme.apply()` re-styling the whole application — now guarded in ui/theme/manager.py,
        which took window 15 from 15.2 s to 0.5 s on its own
      * every window installs an event filter, so each event is dispatched to ALL of them.
        Profiling one late window showed 355,701 eventFilter calls. Nothing but reaping fixes
        that one, which is why BOTH halves of this are needed — the manager guard alone left the
        suite slower than reaping alone.

    MODULE scope, not function scope, is deliberate: several files use `scope="module"` fixtures
    that build a window once and share it across their tests. Reaping per test would delete those
    out from under the tests that follow. Module teardown runs after those fixtures are finished,
    so nothing living is destroyed, and no session-scoped fixture holds widgets.

    A note for whoever suspects this next: it was briefly removed on the theory that it caused a
    segfault in test_sizing.py. It does not. The crash reproduces in a plain loop that builds
    windows with no pytest involved, it happens WITHOUT this fixture too, and it happens EARLIER
    when the manager guards are reverted — it is a headless-Qt artifact at very high widget
    counts. The full suite runs clean on a real display with this fixture in place.
    """
    yield
    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
    except Exception:                       # no Qt in this environment: nothing to reap
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        try:
            w.setParent(None)               # not close(): closeEvent handlers can save state
            w.deleteLater()
        except RuntimeError:                # already gone on the C++ side
            pass
    # deleteLater only queues; without an event loop running, post them by hand
    app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture(autouse=True)
def _ask_gini_offline(monkeypatch):
    try:
        from gini.ui.main_window import MainWindow
    except Exception:
        return
    # neutralise auto-connect: a freshly built MainWindow starts with no model attached
    monkeypatch.setattr(MainWindow, "_wire_llm",
                        lambda self: self.assistant.set_loop(None), raising=False)
