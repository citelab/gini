"""A restarted gRouter container must actually come back.

From a live log:

    r1: fatal: [makePIDFile]:: Another router is still running under: /run/r1.pid
    [r1] gRouter exited (1); stopping container.

No router was running. The gRouter refuses to start when its pid file names a LIVE process, which
is right on a workstation and wrong inside a container: a restarted container begins its PID
namespace at 1 again, so the number in a leftover pid file is near-certain to belong to some new
process — ttyd, the entrypoint, the router itself. kill(pid, 0) succeeds and the router aborts.

Then it loops. The container restarts, finds the same file, dies again. One crash became a router
that could never come back, which is what "gRouter is crashing" actually was.

The entrypoint clears the file, which is safe precisely because of WHERE it runs: reaching that
line means the container has just started. The C-side liveness check is left alone — it is still
correct for someone running two routers on one host outside a container.
"""
import importlib.util
import os
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "backend" / "grouter-build" / "run_grouter.py"
if not RUNNER.exists():
    pytest.skip("backend checkout not present", allow_module_level=True)


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_grouter_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_stale_pid_file_is_removed(runner, tmp_path):
    """The reported failure. The pid inside is deliberately 1 — the number a container restart
    makes most likely, and the one guaranteed to look alive."""
    pid = tmp_path / "r1.pid"
    pid.write_text("1\n")
    runner.clear_stale_pidfile(str(tmp_path), "r1")
    assert not pid.exists(), "the router will refuse to start and the container will loop"


def test_it_says_what_it_did(runner, tmp_path, capsys):
    """Silently deleting a pid file is the kind of thing that should leave a trace — it is a
    recovery, and if it ever happens on every boot that is worth someone noticing."""
    (tmp_path / "r1.pid").write_text("7\n")
    runner.clear_stale_pidfile(str(tmp_path), "r1")
    err = capsys.readouterr().err
    assert "stale pid" in err and "7" in err


def test_no_pid_file_is_the_ordinary_case(runner, tmp_path, capsys):
    """First boot. Must be silent — a warning every start teaches students to ignore warnings."""
    runner.clear_stale_pidfile(str(tmp_path), "r1")
    assert capsys.readouterr().err == ""


def test_an_unremovable_pid_file_warns_but_does_not_raise(runner, tmp_path):
    """A read-only mount should not stop the router from trying: the C side may still succeed,
    and an exception here kills the entrypoint before the router is even launched."""
    d = tmp_path / "ro"
    d.mkdir()
    (d / "r1.pid").write_text("1\n")
    d.chmod(0o500)
    try:
        runner.clear_stale_pidfile(str(d), "r1")     # must not raise
    finally:
        d.chmod(0o700)


def test_it_runs_before_the_router_is_launched(runner):
    """Order is the whole point: clearing after the exec would be too late."""
    import ast
    tree = ast.parse(RUNNER.read_text())
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    src = ast.unparse(main)
    assert "clear_stale_pidfile" in src, "the entrypoint no longer clears the pid file"
    assert src.index("clear_stale_pidfile") < src.index("subprocess.Popen"), (
        "the pid file is cleared after the router is started, which is too late")
