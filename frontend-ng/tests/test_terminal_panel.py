"""The Terminal panel: read the element -> port map, attach the terminal to that element.

The panel is thin — pyte and terminal_view draw, ttyd_client transports — so what needs testing
is the wiring and, above all, the states where there is NO terminal. Those are the ones a student
meets first, and a blank pane looks broken in every one of them: nothing running yet, an element
that serves none, a corrupt map, a stale map from a previous run, a container still starting.

Several of these tests exist because of specific failures:
  * the port map silently stopped being written, so every element reported "nothing is running"
  * connecting before ttyd had bound its port left a terminal that never recovered
  * a probe left in flight attached the PREVIOUS element under the current element's name
  * building a view per selection spawned a Chromium process per click (now: no per-element
    construction at all)
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

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


def _spin(until, secs=5.0):
    """Pump the event loop until `until()` or the deadline. A refused connection comes back almost
    at once, but "at once" still means a trip round the event loop — a fixed iteration count is a
    flaky test on a loaded machine."""
    import time

    from PySide6.QtCore import QCoreApplication
    deadline = time.time() + secs
    while time.time() < deadline and not until():
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return until()


# -- the empty states ------------------------------------------------------- #
def test_says_what_to_do_before_anything_runs(app, theme):
    p = _panel(theme, workdir="")
    p.show_device("m1", "Machine")
    assert "Run" in p._sub.text()
    assert p._connected_to == ""


def test_an_element_with_no_terminal_says_so_by_name(app, theme, tmp_path):
    """xv6 and user-image containers serve none. Naming the element stops the pane looking like
    it failed."""
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 37600, "cmd": ""}}))
    p.show_device("xv1", "xv6 Machine")
    assert "xv6 Machine" in p._sub.text() and "does not serve" in p._sub.text()
    assert p._connected_to == ""


def test_a_corrupt_or_missing_map_is_not_fatal(app, theme, tmp_path):
    (tmp_path / TERMINALS_FILE).write_text("{not json")
    p = _panel(theme, str(tmp_path))
    p.show_device("m1", "Machine")               # must not raise
    _panel(theme, str(tmp_path / "nope")).show_device("m1", "Machine")


def test_a_stale_map_from_a_previous_run_is_inert(app, theme, tmp_path):
    """The workdir OUTLIVES a run, so gini-terminals.json is still there from last time.
    Attaching from it would show a live-looking terminal for a container that is not running."""
    wd = _project(tmp_path, {"m1": {"port": 37600, "cmd": ""}})
    stopped = _panel(theme, wd, running=False)
    stopped.show_device("m1", "Machine")
    assert "37600" not in stopped._sub.text()
    assert "Run" in stopped._sub.text()
    started = _panel(theme, wd, running=True)
    started.show_device("m1", "Machine")
    assert "37600" in started._sub.text()


def test_selection_of_empty_space_clears_the_pane(app, theme, tmp_path):
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p.on_selection(None, None)
    assert p._name == ""
    assert "Select a machine" in p._sub.text()


# -- what the terminal fronts ------------------------------------------------ #
def test_a_running_element_shows_its_own_port(app, theme, tmp_path):
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 37603, "cmd": ""},
                                          "r1": {"port": 37600, "cmd": "exec grconsole"}}))
    p.show_device("m1", "Machine")
    assert "37603" in p._sub.text()
    p.show_device("r1", "Router")
    assert "37600" in p._sub.text()


def test_the_subtitle_distinguishes_a_router_cli_from_a_shell(app, theme, tmp_path):
    """Routers and switches share an image; only TTYD_CMD says which you are looking at."""
    p = _panel(theme, _project(tmp_path, {"r1": {"port": 1, "cmd": "tmux new -A -s gini "
                                                                  "\"python3 grconsole.py\""},
                                          "ovs1": {"port": 2, "cmd": "tmux new -A -s gini"}}))
    p.show_device("r1", "Router")
    assert "router CLI" in p._sub.text()
    p.show_device("ovs1", "Switch")
    assert "shell" in p._sub.text()


# -- connecting -------------------------------------------------------------- #
def test_a_hidden_tab_opens_no_connection(app, theme, tmp_path):
    """An unopened Terminal tab must cost nothing — no socket to a container nobody is watching."""
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    assert not p.isVisible()
    p.show_device("m1", "Machine")
    assert p._connected_to == "", "connected for a hidden tab"
    assert p._pending is not None, "did not remember what to attach to once the tab opens"


def test_it_waits_for_ttyd_instead_of_failing_once(app, theme, tmp_path):
    """`docker compose up` returns before ttyd inside the container binds its port, and a
    WebSocket that failed once does not retry itself. Port 1 is never listening."""
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p._pending = ("http://127.0.0.1:1/", "m1", "")
    p._tries = 0
    p._probe_port()
    assert _spin(lambda: p._tries > 0), "a refused port never reported back"
    assert p._connected_to == "", "attached to a port nothing is listening on"


def test_a_refused_port_is_retried_not_abandoned(app, theme, tmp_path):
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p.PROBE_MS = 10
    p._pending = ("http://127.0.0.1:1/", "m1", "")
    p._tries = 0
    p._probe_port()
    assert _spin(lambda: p._tries >= 3), f"gave up after {p._tries} attempt(s)"


def test_it_gives_up_eventually_and_says_so(app, theme, tmp_path):
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p._pending = ("http://127.0.0.1:1/", "m1", "")
    p._tries = p.MAX_PROBES + 1
    p._probe_failed()
    assert "No terminal answered" in p._sub.text()


def test_switching_element_abandons_the_previous_probe(app, theme, tmp_path):
    """A probe left in flight would attach the PREVIOUS element under the current element's
    name."""
    p = _panel(theme, _project(tmp_path, {"a": {"port": 1, "cmd": ""}, "b": {"port": 2, "cmd": ""}}))
    p._pending = ("http://127.0.0.1:1/", "a", "")
    p._tries = 0
    p._probe_port()
    assert p._probe is not None
    p.show_device("b", "B")
    assert p._probe is None, "the old element's probe is still armed"


def test_clearing_the_pane_cancels_an_in_flight_probe(app, theme, tmp_path):
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p._pending = ("http://127.0.0.1:1/", "m1", "")
    p._probe_port()
    p.show_none()
    assert p._probe is None and p._pending is None


# -- the design that replaced the web view ----------------------------------- #
def test_there_is_exactly_one_terminal_widget_for_the_panel(app, theme, tmp_path):
    """No per-element construction. The previous design built a QWebEngineView — a Chromium
    process — per selection, which stalled slow machines on every click."""
    p = _panel(theme, _project(tmp_path, {"a": {"port": 1, "cmd": ""}, "b": {"port": 2, "cmd": ""}}))
    p.show()
    view, client = p._view, p._client
    for name in ("a", "b", "a", "b"):
        p.show_device(name, name.upper())
    assert p._view is view, "rebuilt the terminal widget on a selection change"
    assert p._client is client, "rebuilt the transport on a selection change"


def test_qtwebengine_is_not_on_the_terminal_path(app, theme, tmp_path):
    """The whole point of the rewrite. The OS Zoo and Desktop screens still use QtWebEngine, but
    a terminal is opened on every click and must not pay Chromium's price."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "gini"
    offenders = []
    for rel in ("ui/terminal_panel.py", "ui/terminal_view.py", "services/ttyd_client.py",
                "services/ttyd_protocol.py"):
        tree = ast.parse((root / rel).read_text())
        for n in ast.walk(tree):
            mod = (n.module if isinstance(n, ast.ImportFrom) else None) or ""
            names = [a.name for a in getattr(n, "names", [])] if isinstance(n, ast.Import) else []
            if "WebEngine" in mod or any("WebEngine" in x for x in names):
                offenders.append(f"{rel}:{n.lineno}")
    assert not offenders, f"QtWebEngine is back on the terminal path: {offenders}"


def test_switching_elements_clears_the_previous_screen(app, theme, tmp_path):
    """One widget is reused, so the last router's output must not appear under the next host's
    name. The WORK is safe regardless — it is a tmux session in the container."""
    p = _panel(theme, _project(tmp_path, {"a": {"port": 1, "cmd": ""}, "b": {"port": 2, "cmd": ""}}))
    p.show()
    p._view.feed(b"secrets from a\r\n")
    p._pending = ("http://127.0.0.1:2/", "b", "")
    p._attach()
    assert p._view._screen.display[0].strip() == "", "previous element's output survived the switch"


def test_the_terminal_follows_the_theme(app, theme, tmp_path):
    """It is painted by Qt now, so it can — the xterm.js page never could."""
    p = _panel(theme, _project(tmp_path, {"m1": {"port": 1, "cmd": ""}}))
    p.refresh_theme("dark")
    p.show_device("m1", "Machine")
    p.refresh_theme("green")                     # must not raise


def test_no_application_wide_event_filter_creeps_back_in(app, theme, tmp_path):
    """An app-wide filter segfaulted gBuilder once already (ui/app.py)."""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "gini" / "ui" / "terminal_panel.py"
    tree = ast.parse(src.read_text())
    assert not [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "installEventFilter"]
