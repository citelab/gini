"""The Terminal panel: read the element -> port map, embed that element's ttyd.

The panel is thin by design — ttyd and xterm.js do the terminal work inside the container — so
what actually needs testing is the wiring and, above all, the states where there is NO terminal.
Those are the ones a student meets first: nothing running yet, an element that serves no
terminal, and QtWebEngine missing. A blank pane in any of them looks broken.

The last two tests cover the two things this panel got WRONG the first time round: it built a
Chromium render process per selection even when the tab was hidden, and it was blamed for a
segfault that was really an application-wide event filter (see ui/app.py).
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from gini.services.orchestrator import TERMINALS_FILE


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _project(tmp_path, terms):
    (tmp_path / TERMINALS_FILE).write_text(json.dumps(terms))
    return str(tmp_path)


def _panel(theme, workdir="", running=True):
    from gini.ui.terminal_panel import TerminalPanel
    p = TerminalPanel(theme, workdir_fn=lambda: workdir, running_fn=lambda: running)
    p.resize(500, 600)
    return p


def test_says_what_to_do_before_anything_runs(app, theme):
    """No project yet. The commonest first state, and it must explain itself."""
    p = _panel(theme, workdir="")
    p.show_device("m1", "Machine")
    assert "Run" in p._sub.text()
    assert p._view is None


def test_an_element_with_no_terminal_says_so_by_name(app, theme, tmp_path):
    """Something IS running, but this element serves no terminal — xv6, or a user-image
    container. Naming it stops the pane looking like it failed."""
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 37600, "cmd": ""}}))
    p.show_device("xv1", "xv6 Machine")
    assert "xv6 Machine" in p._sub.text() and "does not serve" in p._sub.text()
    assert p._view is None


def test_a_running_element_gets_its_own_port(app, theme, tmp_path):
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 37603, "cmd": ""},
                                          "r1": {"port": 37600, "cmd": "exec grconsole"}}))
    p.show_device("m1", "Machine")
    assert "37603" in p._sub.text()
    p.show_device("r1", "Router")
    assert "37600" in p._sub.text()


def test_the_subtitle_distinguishes_a_router_cli_from_a_shell(app, theme, tmp_path):
    """Routers and switches share an image; only TTYD_CMD says which you are looking at."""
    p = _panel(theme, _project(tmp_path, {"r1": {"port": 1, "cmd": "exec python3 /x/grconsole.py"},
                                          "ovs1": {"port": 2, "cmd": ""}}))
    p.show_device("r1", "Router")
    assert "router CLI" in p._sub.text()
    p.show_device("ovs1", "Switch")
    assert "shell" in p._sub.text()


def test_a_corrupt_or_missing_map_is_not_fatal(app, theme, tmp_path):
    (tmp_path / TERMINALS_FILE).write_text("{not json")
    p = _panel(theme, str(tmp_path))
    p.show_device("m1", "Machine")               # must not raise
    assert p._view is None
    p2 = _panel(theme, str(tmp_path / "nope"))   # directory does not exist
    p2.show_device("m1", "Machine")


def test_selection_of_empty_space_clears_the_pane(app, theme, tmp_path):
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p.on_selection(None, None)
    assert p._name == ""
    assert "Select a machine" in p._sub.text()


def test_refresh_theme_does_not_throw(app, theme, tmp_path):
    """Follows the source_browser convention; themeChanged passes the theme name."""
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p.refresh_theme("dark")
    p.show_device("m1", "Machine")
    p.refresh_theme("green")


def test_switching_elements_does_not_keep_the_previous_shell(app, theme, tmp_path):
    """A web view keeps its page's state. Carrying one element's live shell into another
    element's tab would be worse than a reload."""
    p = _panel(theme, _project(tmp_path, {"a": {"port": 1, "cmd": ""}, "b": {"port": 2, "cmd": ""}}))
    p.show_device("a", "A")
    assert "127.0.0.1:1/" in p._sub.text()
    p.show_device("b", "B")
    assert "127.0.0.1:2/" in p._sub.text()
    assert p._view is None or p._pending[0].endswith(":2/")


def test_a_stale_map_from_a_previous_run_is_inert(app, theme, tmp_path):
    """The workdir OUTLIVES a run — gini-terminals.json is still sitting there from last time.
    Embedding from it would put a live-looking terminal on screen for a container that is not
    running, at startup, before the student has pressed anything."""
    wd = _project(tmp_path, {"m1": {"port": 37600, "cmd": ""}})
    stopped = _panel(theme, wd, running=False)
    stopped.show_device("m1", "Machine")
    assert "37600" not in stopped._sub.text()
    assert "Run" in stopped._sub.text()
    started = _panel(theme, wd, running=True)
    started.show_device("m1", "Machine")
    assert "37600" in started._sub.text()


def test_a_hidden_tab_builds_no_web_view(app, theme, tmp_path):
    """A QWebEngineView is a Chromium render process, not a widget. The first version built one on
    every selection change, so clicking through ten elements with the Inspector tab showing spawned
    ten render processes for a pane nobody was looking at. Hidden => recorded, not realised."""
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""},
                                          "m2": {"port": 2, "cmd": ""}}))
    assert not p.isVisible()
    p.show_device("m1", "Machine")
    assert p._view is None, "built a render process for a hidden tab"
    assert p._pending is not None, "did not remember what to show once the tab opens"
    p.show_device("m2", "Machine")
    assert p._view is None
    assert p._pending[0].endswith(":2/"), "pending target did not follow the selection"


def test_clearing_the_pane_forgets_what_was_pending(app, theme, tmp_path):
    """Otherwise opening the tab later resurrects a terminal for an element that is no longer
    selected — or no longer running."""
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p.show_device("m1", "Machine")
    assert p._pending is not None
    p.show_none()
    assert p._pending is None


def test_no_application_wide_event_filter_creeps_back_in(app, theme, tmp_path):
    """This panel was blamed for a segfault that was really MainWindow's app-wide event filter:
    PySide cannot wrap the QtQuick objects inside a QWebEngineView, so hovering ANY web view
    crashed the process. Guarding here too because this file is the one most likely to grow a
    'just watch for mouse events' filter later."""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "gini" / "ui" / "terminal_panel.py"
    tree = ast.parse(src.read_text())
    assert not [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "installEventFilter"], (
        "terminal_panel installs an event filter — if it ever reaches the application object "
        "this reintroduces the QWebEngineView segfault. See ui/app.py.")
