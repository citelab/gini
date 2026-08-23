"""A Terminal session must OUTLIVE its browser tab.

The case that drove this: leave `ping` running on M1, switch to M2 to run tcpdump, come back to
M1 — and land in the same session with the ping still going, not a fresh shell. The panel tears
its web view down on every switch (one element's live shell must never appear under another
element's name), which closes the WebSocket and kills ttyd's child. So persistence cannot live in
the UI; it has to live in the container. tmux provides it.

These tests pin the three things that silently break it:
  * `new -A` — attach-or-create. Plain `new` starts a SECOND session on every reconnect, which
    looks identical on screen and quietly loses the work.
  * a stable session name — a per-connection name reattaches to nothing.
  * every element kind actually receiving a TTYD_CMD. Three of the four did not, because
    _term_env was only ever called in the router branch back when everything else had an empty
    command; the compose was still valid and the terminals still worked, just without persistence.
"""
import pytest

yaml = pytest.importorskip("yaml")

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler
from gini.services.orchestrator import TMUX_SESSION, _compose, _persist


def _cmds(**kw) -> dict:
    t = Topology("t")
    r = t.add_device("router")
    t.add_link(r.id, t.add_device("host").id)
    o, c = t.add_device("ovs"), t.add_device("controller")
    t.add_link(o.id, c.id)
    t.add_link(r.id, o.id)
    doc = yaml.safe_load(_compose(RuntimeCompiler().compile(t)))
    return {s: ((v or {}).get("environment") or {}).get("TTYD_CMD", "")
            for s, v in (doc.get("services") or {}).items()}


def test_every_element_with_a_terminal_attaches_to_a_session():
    cmds = _cmds()
    assert cmds, "no services emitted"
    missing = [s for s, c in cmds.items() if not c]
    assert not missing, (
        f"{missing} get a terminal but no TTYD_CMD, so they start a fresh shell every time. "
        f"_term_env must be called for every element kind, AFTER _term_port registers it.")
    for svc, cmd in cmds.items():
        assert "tmux" in cmd, f"{svc}: no persistent session — {cmd}"


def test_reconnecting_attaches_rather_than_starting_a_second_session():
    """`-A` is the whole feature. Without it every reconnect silently opens a new session and the
    student's running command is still there — just not on screen, and unreachable."""
    for svc, cmd in _cmds().items():
        assert "new -A" in cmd, (
            f"{svc}: `tmux new` without -A starts a second session per reconnect instead of "
            f"re-attaching. Got: {cmd}")


def test_the_session_name_is_stable():
    """A name derived from the connection, the port, or a timestamp would reattach to nothing."""
    for svc, cmd in _cmds().items():
        assert f"-s {TMUX_SESSION}" in cmd, f"{svc}: unstable session name — {cmd}"


def test_an_image_without_tmux_still_gives_a_working_shell():
    """Images built before tmux landed must degrade, not break. `exec tmux` would be fatal: if
    exec cannot find the binary the shell exits and the tab shows ttyd's reconnect banner."""
    cmd = _persist()
    assert not cmd.startswith("exec tmux"), (
        "exec makes a missing tmux fatal; an un-rebuilt image would serve a dead terminal")
    assert cmd.rstrip().endswith("exec /bin/sh"), (
        "no fallback shell — a missing tmux, or quitting tmux, would end the session")


def test_quitting_the_router_cli_stays_inside_the_session():
    """grconsole runs INSIDE tmux, so quitting it leaves a shell in the same persistent session
    rather than tearing the session down."""
    cmd = next(c for c in _cmds().values() if "grconsole" in c)
    assert cmd.index("tmux") < cmd.index("grconsole"), "the CLI is outside tmux; it will not persist"
    assert "grconsole.py /run/" in cmd, "grconsole still needs its control socket path"


def test_a_plain_shell_command_carries_no_stray_quoting():
    """_persist() with no command must not emit an empty quoted argument — tmux would try to run
    the empty string as a command and exit immediately."""
    cmd = _persist()
    assert '""' not in cmd and "''" not in cmd, f"empty quoted argument in: {cmd}"
