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
    """Destroy leftover top-level widgets after each test FILE, or the suite goes quadratic.

    `MainWindow.__init__` calls `theme.apply()`, which ends in `app.setStyleSheet(...)`. An
    application-level stylesheet makes Qt re-polish EVERY live widget in the process — so with N
    windows still alive at ~285 widgets each, building window N+1 costs O(N), and a suite that
    builds one per test costs O(N^2). Measured on a bare run:

        window  1   0.14 s      284 live widgets
        window  5   1.72 s    1,424
        window 10   6.63 s    2,849
        window 15  15.20 s    4,274

    That is the "long pause partway through, then it keeps going" — nothing is hung, every later
    test is just paying for every earlier one. Reaping caps the cost at one file's worth instead
    of the whole run.

    MODULE scope, not function scope, is deliberate: several files use `scope="module"` fixtures
    that build a window once and share it across their tests. Reaping per test would delete those
    out from under the tests that come after. Module teardown runs after those fixtures are
    finished, so nothing living is destroyed.

    The real fix is app-side — skip the stylesheet when it has not changed, which would speed up
    opening a second window in the product too — but that touches theming everywhere, so it is a
    deliberate decision rather than a drive-by.
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
