"""The Terminal tab's panes: M1 and M2 at once, or M1 twice — two shells, never one shown twice.

Asked for 2026-11-22: a networks lab runs `iperf3 -s` on M1 and `iperf3 -c M1` on M2, and students
were flicking between them in a terminal that shows one element at a time. The panel now holds up
to four panes; what these pin is the part that is easy to get subtly wrong:

  * two panes on ONE element are two tmux sessions (`gini`, `gini-2`), asked for in the URL and
    picked by the container's own shell — not the one session shown twice;
  * which pane a canvas click points, so reading M1's Inspector does not throw away the M2
    terminal being typed in;
  * the `$` in the session pick surviving Compose, which substitutes `$` before YAML is parsed.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

from gini.services import ttyd_protocol as proto
from gini.services.orchestrator import MAX_SESSIONS, TERMINALS_FILE, _persist, tmux_session

TERMS = {"m1": {"port": 1, "cmd": ""}, "m2": {"port": 2, "cmd": ""},
         "r1": {"port": 3, "cmd": "tmux new -A -s gini \"grconsole.py\""}}


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _panel(theme, tmp_path, **kw):
    from gini.ui.terminal_panel import TerminalPanel
    (tmp_path / TERMINALS_FILE).write_text(json.dumps(TERMS))
    p = TerminalPanel(theme, workdir_fn=lambda: str(tmp_path), running_fn=lambda: True, **kw)
    p.resize(500, 800)
    return p


def _on(p):
    return [(q._name, q._slot) for q in p.panes]


# -- splitting and closing ---------------------------------------------------- #
def test_split_adds_an_empty_active_pane_up_to_the_limit(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    assert len(p.panes) == 1 and not p.active._close_btn.isVisibleTo(p)
    first = p.active
    new = p.split()
    assert len(p.panes) == 2 and p.active is new and new is not first
    assert new._name == "" and "Click an element" in new._sub.text()
    while p.split():
        pass
    assert len(p.panes) == MAX_SESSIONS
    assert not p.active._split_btn.isEnabled()
    assert p.split() is None


def test_a_new_pane_goes_below_the_one_whose_split_was_pressed(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    top = p.active
    bottom = p.split()
    middle = p.split(top)
    assert p.panes == [top, middle, bottom]
    assert [p._split.widget(i) for i in range(3)] == [top, middle, bottom]


def test_the_last_pane_cannot_be_closed(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    p.close_pane(p.active)
    assert len(p.panes) == 1


def test_closing_a_pane_detaches_it_and_hands_over_the_active_role(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    p.show_device("m1")
    second = p.split()
    p.show_device("m2")
    second._connected_to = "pretend"
    p.close_pane(second)
    assert p.panes and second not in p.panes and p.active is p.panes[0]
    assert second._connected_to == "" and second._pending is None
    assert not p.active._close_btn.isVisibleTo(p), "a lone pane offered to close itself"


# -- which pane the canvas selection points ----------------------------------- #
def test_an_empty_active_pane_takes_the_selection_so_m1_can_be_opened_twice(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    p.show_device("m1")
    p.split()
    p.show_device("m1")
    assert _on(p) == [("m1", 1), ("m1", 2)]
    assert "session 2" in p.panes[1]._sub.text()
    assert "session" not in p.panes[0]._sub.text(), "the first session is not news"


def test_selecting_an_element_already_shown_makes_that_pane_active(app, theme, tmp_path):
    """Clicking M1 to read its Inspector must not re-point the M2 terminal being typed in."""
    p = _panel(theme, tmp_path)
    p.show_device("m1")
    p.split()
    p.show_device("m2")
    assert p.active is p.panes[1]
    p.show_device("m1")
    assert _on(p) == [("m1", 1), ("m2", 1)], "a pane was re-pointed"
    assert p.active is p.panes[0]


def test_an_occupied_active_pane_follows_a_new_element(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    p.show_device("m1")
    p.split()
    p.show_device("m2")
    p.show_device("r1")
    assert _on(p) == [("m1", 1), ("r1", 1)]
    assert "router CLI" in p.active._sub.text()


def test_one_pane_still_follows_every_selection(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    for name in ("m1", "m2", "m1"):
        p.show_device(name)
        assert _on(p) == [(name, 1)]


def test_a_click_on_empty_canvas_clears_only_a_lone_pane(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    p.show_device("m1")
    p.on_selection(None)
    assert _on(p) == [("", 1)]
    p.show_device("m1")
    p.split()
    p.show_device("m2")
    p.on_selection(None)
    assert _on(p) == [("m1", 1), ("m2", 1)], "a click on the canvas blanked a terminal"


def test_typing_into_a_pane_makes_it_the_one_the_selection_points(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    first = p.active
    p.split()
    first._view.focused.emit()
    assert p.active is first


# -- sessions ----------------------------------------------------------------- #
def test_each_pane_on_an_element_takes_the_lowest_free_session(app, theme, tmp_path):
    p = _panel(theme, tmp_path)
    p.show_device("m1")
    p.split()
    p.show_device("m1")
    p.split()
    p.show_device("m1")
    assert _on(p) == [("m1", 1), ("m1", 2), ("m1", 3)]
    p.close_pane(p.panes[1])
    p.split()
    p.show_device("m1")
    assert sorted(s for _n, s in _on(p)) == [1, 2, 3], "session 2 was free and not reused"


def test_a_pane_keeps_its_session_when_shown_the_same_element_again(app, theme, tmp_path):
    """Its screen must not swap to another session because a pane above it closed."""
    p = _panel(theme, tmp_path)
    p.show_device("m1")
    second = p.split()
    p.show_device("m1")
    p.close_pane(p.panes[0])
    second.show_device("m1")
    assert second._slot == 2


def test_a_pane_reads_the_history_of_its_own_session(app, theme, tmp_path):
    seen = []
    p = _panel(theme, tmp_path, history_fn=lambda dev, cmd: seen.append((dev, cmd)) or "")
    p.show_device("m1")
    p.split()
    p.show_device("m1")
    for q in p.panes:
        q._view._history_source()
    assert "-t =gini: " in seen[0][1] and "-t =gini-2: " in seen[1][1]
    assert all(dev == "m1" for dev, _ in seen)


def test_each_pane_records_its_own_commands(app, theme, tmp_path):
    got = []
    p = _panel(theme, tmp_path, record_fn=lambda dev, cmd, out: got.append((dev, cmd)))
    p.show_device("m1")
    p.split()
    p.show_device("m2")
    assert p.panes[0]._tap is not p.panes[1]._tap
    assert (p.panes[0]._tap.device, p.panes[1]._tap.device) == ("m1", "m2")


def test_the_session_is_asked_for_in_the_websocket_url():
    assert proto.ws_url("http://127.0.0.1:37601/") == "ws://127.0.0.1:37601/ws"
    assert proto.ws_url("http://127.0.0.1:37601/", 1) == "ws://127.0.0.1:37601/ws"
    assert proto.ws_url("http://127.0.0.1:37601/", 3) == "ws://127.0.0.1:37601/ws?arg=3"
    assert [tmux_session(n) for n in (1, 2, 4)] == ["gini", "gini-2", "gini-4"]


def test_the_client_carries_the_pane_session_to_the_socket(app):
    from gini.services.ttyd_client import TtydClient
    c = TtydClient()
    c.connect_to("http://127.0.0.1:1/", 80, 24, 2)
    try:
        assert c._session == 2
    finally:
        c.disconnect_from()


def test_leaving_while_the_token_is_in_flight_opens_nothing(app):
    """abort() emits `finished` synchronously. It used to run the token handler mid-teardown —
    a socket opened to the element being left, then an AttributeError on the cleared reply. A pane
    switched or closed in that window is an ordinary click, not a race nobody hits."""
    from gini.services.ttyd_client import TtydClient
    c = TtydClient()
    c.connect_to("http://127.0.0.1:1/", 80, 24)
    c.disconnect_from()
    assert c._reply is None and c._sock is None


# -- the container side --------------------------------------------------------- #
def _picked(tmp_path, *args) -> str:
    """Run the real TTYD_CMD the way ttyd runs it — `sh -c CMD [arg]` — against a fake tmux that
    reports the session it was asked for."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    fake = bin_ / "tmux"
    fake.write_text('#!/bin/sh\nwhile [ "$1" != "-s" ]; do shift; done; echo "SESSION=$2"; '
                    "exit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    cmd = _persist().replace("; exec /bin/sh", "")      # no shell to fall into here
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
    out = subprocess.run(["sh", "-c", cmd, *args], capture_output=True, text=True, env=env,
                         check=False)
    return out.stdout.strip()


def test_the_container_shell_picks_the_session_the_url_asked_for(tmp_path):
    assert _picked(tmp_path) == "SESSION=gini"                   # no ?arg: ttyd passes nothing
    assert _picked(tmp_path, "2") == "SESSION=gini-2"
    assert _picked(tmp_path, "4") == "SESSION=gini-4"


@pytest.mark.parametrize("arg", ["1", "0", "10", "x", "2; touch pwned", "$(id)", "", "-2"])
def test_anything_but_one_digit_from_the_url_is_the_first_session(tmp_path, arg):
    """The value comes off a URL. It only ever selects among names the case statement wrote."""
    assert _picked(tmp_path, arg) == "SESSION=gini"
    assert not (tmp_path / "pwned").exists()


def test_compose_receives_the_dollars_escaped(tmp_path):
    """Compose substitutes `$` before parsing: `$0` and `$S` would arrive empty, and `${TERM…}`
    filled from gBuilder's own environment instead of ttyd's."""
    from gini.domain.topology import Topology

    from gini.services.compiler import RuntimeCompiler
    from gini.services.orchestrator import _compose
    t = Topology("t")
    r = t.add_device("router")
    t.add_link(r.id, t.add_device("host").id)
    text = _compose(RuntimeCompiler().compile(t))
    lines = [ln for ln in text.splitlines() if "TTYD_CMD:" in ln]
    assert lines
    for ln in lines:
        assert not re.search(r"(?<!\$)\$(?!\$)", ln.replace("$$", "")), ln
        assert '"$$0"' in ln and '-s "$$S"' in ln


def test_every_image_lets_the_url_choose_the_session():
    """`-a` is what lets ttyd hand `?arg=N` to the command. Without it a second pane on an element
    silently joins the first pane's session."""
    from pathlib import Path

    from gini.services import orchestrator as o
    backend = Path(__file__).resolve().parents[2] / "backend"
    texts = {"generated": o._ttyd_layers()}
    for rel in ("grouter-build/Dockerfile", "sdn/Dockerfile"):
        if (backend / rel).exists():
            texts[rel] = (backend / rel).read_text()
    for name, text in texts.items():
        launch = [ln for ln in text.splitlines() if "ttyd -W" in ln]
        assert launch and all(" -a " in ln for ln in launch), f"{name}: {launch}"
