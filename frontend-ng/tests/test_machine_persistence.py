"""A machine's files survive a restart.

Student exercises ask them to WRITE programs inside a host — `vi server.c`, `gcc`, `./server` —
and a container's filesystem is discarded on every restart, Reboot or Run. Their work went with
it. Each machine now has a persistent host directory bind-mounted at its HOME (/root), where its
shell lands, so a file created at the prompt is simply still there next time.

This is the router `/scripts` pattern (`~/.gini/scripts` -> a fixed mount), one difference: it is
read-WRITE, because here the student authors INSIDE the container rather than on the host. `/shared`
already works this way.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.app import paths
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler
from gini.services.orchestrator import MACHINE_HOME, _compose, _persist, write_project


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "gini"))


def _two_hosts_and_a_router():
    t = Topology("demo")
    m1 = t.add_device("host"); m1.name = "M1"
    m2 = t.add_device("host"); m2.name = "M2"
    r = t.add_device("router")
    t.add_link(m1.id, r.id)
    t.add_link(m2.id, r.id)
    return t


def _machine_names(cfg):
    # the DOCKER SERVICE names — what the mount and the created dir are both keyed on
    return [m["name"] for m in cfg.to_runtime(docker=True)["machines"]]


# --------------------------------------------------------------------------- #
# the host directory
# --------------------------------------------------------------------------- #

def test_machine_dir_is_under_gini_home_and_keyed_by_name():
    d = paths.machine_dir("M1")
    assert d.parent == paths.gini_home() / "machines"
    assert d.name == "M1"


def test_a_name_with_awkward_characters_is_made_safe():
    assert paths.machine_dir("host 1/../x").name == "host-1-..-x"
    assert paths.machine_dir("").name == "host", "an empty name still yields a directory"


def test_every_machine_gets_its_own_home_created_before_up():
    cfg = RuntimeCompiler().compile(_two_hosts_and_a_router())
    write_project(cfg, tempfile.mkdtemp(), _runtime_dir())
    dirs = {n: paths.machine_dir(n) for n in _machine_names(cfg)}
    assert len(dirs) == 2
    for d in dirs.values():
        assert d.is_dir(), f"{d} was not created — Docker would invent a root-owned one"
    assert len({str(d) for d in dirs.values()}) == 2, "two machines must not share a home"


def _runtime_dir():
    import gini.runtime as rt
    return rt.__path__[0]


# --------------------------------------------------------------------------- #
# the mount
# --------------------------------------------------------------------------- #

def test_a_machine_mounts_its_home_at_root():
    cfg = RuntimeCompiler().compile(_two_hosts_and_a_router())
    compose = _compose(cfg)
    for name in _machine_names(cfg):
        host = str(paths.machine_dir(name)).replace("\\", "/")
        assert f'"{host}:{MACHINE_HOME}"' in compose, f"{name} has no persistent home mount"


def test_the_home_mount_is_read_write_unlike_the_routers_scripts():
    """The student writes INSIDE the container, so it has to persist back — no `:ro`."""
    cfg = RuntimeCompiler().compile(_two_hosts_and_a_router())
    compose = _compose(cfg)
    host = str(paths.machine_dir(_machine_names(cfg)[0])).replace("\\", "/")
    assert f'"{host}:{MACHINE_HOME}:ro"' not in compose
    assert f'"{host}:{MACHINE_HOME}"' in compose


def test_shared_is_still_mounted_next_to_it():
    """The per-machine home is additive: the multicast capstone's /shared must remain."""
    cfg = RuntimeCompiler().compile(_two_hosts_and_a_router())
    compose = _compose(cfg)
    assert "/shared" in compose


# --------------------------------------------------------------------------- #
# the shell lands in the persistent home
# --------------------------------------------------------------------------- #

def test_the_machine_terminal_lands_in_the_persistent_home():
    cfg = RuntimeCompiler().compile(_two_hosts_and_a_router())
    compose = _compose(cfg)
    # the in-app terminal is driven by TTYD_CMD; it must start in /root
    ttyd_lines = [l for l in compose.splitlines() if "TTYD_CMD" in l]
    machine_ttyd = [l for l in ttyd_lines if MACHINE_HOME in l]
    assert machine_ttyd, "no machine terminal lands in the persistent home"
    for l in machine_ttyd:
        assert f"cd {MACHINE_HOME}" in l


def test_persist_lands_where_told_and_still_reattaches():
    cmd = _persist(workdir=MACHINE_HOME)
    assert cmd.startswith(f"cd {MACHINE_HOME} 2>/dev/null;")
    assert "tmux new -A" in cmd, "must still attach-or-create, not start a second session"
    assert f"-s gini -c {MACHINE_HOME}" in cmd


def test_a_router_shell_is_unchanged():
    """Routers land where they always did — no persistent-home cd. `_persist()` with no argument
    must be byte-for-byte what it was, or every router terminal test would have to move."""
    cmd = _persist()
    assert not cmd.startswith("cd ")
    assert " -c " not in cmd
    assert cmd.startswith('TERM=') and "tmux new -A -s gini" in cmd


def test_the_native_login_also_lands_in_the_home():
    """The double-click 'Log in' path opens an external terminal with `docker compose exec`; it
    lands in /root too, via `cd; exec sh` rather than `-w` (podman-compose rejects `-w`)."""
    import inspect

    from gini.ui import main_window
    src = inspect.getsource(main_window.MainWindow._open_terminal)
    assert f"cd {{MACHINE_HOME}}" in src or "cd {MACHINE_HOME}" in src
    assert "-w " not in src.split('role == "machine"')[1].split("elif")[0], \
        "used -w, which podman-compose's exec does not accept"
