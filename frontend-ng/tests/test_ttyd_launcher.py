"""The generated ttyd launcher must survive the SHELL BUILTIN printf, and routers must be handed
their control socket.

Both of these shipped broken and neither was caught by anything else, because the compose was
valid, the ports were right, and the panel embedded happily — the failure only appears as text
inside the terminal, at runtime, in a container.

BUG 1 — `\\"` is not a portable printf escape. The launcher is written by a Dockerfile
`RUN printf '...'`, which uses the shell's BUILTIN printf (busybox ash on Alpine, dash on Debian).
That one emits the backslash literally, while GNU coreutils /usr/bin/printf quietly drops it. The
image therefore contained

    ttyd ... /bin/sh -c \\"${TTYD_CMD:-exec /bin/sh}\\"

so ttyd got `"exec` and `/bin/sh"` as separate words and every terminal died with

    "/bin/sh": line 0: syntax error: unterminated quoted string

The first version of this check ran /usr/bin/printf and reported all six images valid. It was
testing a different program from the one that runs. These tests use `sh -c 'printf "$1"'`.

BUG 2 — grconsole.py is a CLIENT of the running gRouter daemon and takes the control socket path
(`<confdir>/<name>.ctl`; the image sets GINI_HOME=/run). Invoked bare it prints its usage and
exits 2, so the tab showed "usage: grconsole.py <socket>" then ttyd's reconnect banner.
"""
import re
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from gini.domain.topology import Topology
from gini.services import orchestrator as o
from gini.services.compiler import RuntimeCompiler

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _images() -> dict:
    imgs = {"machine-lean": o._DOCKERFILE_MACHINE_LEAN,
            "machine-full": o._DOCKERFILE_MACHINE,
            "machine-security": o._DOCKERFILE_MACHINE_SECURITY,
            "machine-gui": o._DOCKERFILE_MACHINE_GUI}
    for name, rel in (("grouter", "grouter-build/Dockerfile"), ("pox", "sdn/Dockerfile")):
        p = BACKEND / rel
        if p.exists():                       # backend is a sibling checkout; skip if absent
            imgs[name] = p.read_text()
    return imgs


def _launcher(dockerfile: str) -> str:
    """The gini-term script as the IMAGE BUILD would actually write it."""
    m = re.search(r"RUN printf '(.*?)' *[\\\n>]", dockerfile, re.S)
    assert m, "no `RUN printf '...' > gini-term` layer found"
    return subprocess.run(["sh", "-c", 'printf "$1"', "_", m.group(1)],
                          capture_output=True, text=True).stdout


@pytest.mark.parametrize("image", sorted(_images()))
def test_the_launcher_is_valid_shell(image):
    out = subprocess.run(["sh", "-n"], input=_launcher(_images()[image]),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"{image}: gini-term is not valid shell — {out.stderr.strip()}"


@pytest.mark.parametrize("image", sorted(_images()))
def test_the_command_reaches_ttyd_as_one_word(image):
    """A backslash-escaped quote splits ttyd's last argument in two and every session dies."""
    line = _launcher(_images()[image]).strip().splitlines()[-1]
    assert '\\"' not in line, (
        f"{image}: backslash-escaped quote in the launcher. The shell's builtin printf keeps it "
        f'and ttyd receives `"exec` and `/bin/sh"` separately: {line}')


@pytest.mark.parametrize("image", sorted(_images()))
def test_ttyd_cmd_survives_to_runtime(image):
    """It must NOT be expanded at build time — that is what lets one image front a router CLI on
    one element and a shell on another."""
    assert "${TTYD_CMD" in _launcher(_images()[image]), (
        f"{image}: TTYD_CMD was expanded during the build; every element would get a plain shell")


def test_a_router_terminal_is_given_its_control_socket():
    t = Topology("t")
    r = t.add_device("router")
    h = t.add_device("host")
    t.add_link(r.id, h.id)
    doc = yaml.safe_load(o._compose(RuntimeCompiler().compile(t)))
    cmds = {s: ((v or {}).get("environment") or {}).get("TTYD_CMD", "")
            for s, v in (doc.get("services") or {}).items()}
    routers = {s: c for s, c in cmds.items() if "grconsole" in c}
    assert routers, "no router fronts the gRouter CLI"
    for svc, cmd in routers.items():
        assert f"/run/{svc}.ctl" in cmd, (
            f"{svc}: grconsole invoked without its control socket — it will print its usage and "
            f"exit 2 instead of opening the CLI. Got: {cmd}")


def test_quitting_the_router_cli_leaves_a_shell_not_a_dead_session():
    """`exec grconsole` would end the ttyd session the moment a student typed Ctrl-D, showing
    'Press Enter to Reconnect'. Landing in a shell in the router's own container is more useful
    and matches what the external-terminal button does."""
    t = Topology("t")
    r = t.add_device("router")
    t.add_link(r.id, t.add_device("host").id)
    doc = yaml.safe_load(o._compose(RuntimeCompiler().compile(t)))
    for _s, v in (doc.get("services") or {}).items():
        cmd = ((v or {}).get("environment") or {}).get("TTYD_CMD", "")
        if "grconsole" in cmd:
            assert not cmd.startswith("exec "), "the CLI replaces the session; quitting kills it"
            assert "exec /bin/sh" in cmd, "no shell to fall back into when the CLI exits"
