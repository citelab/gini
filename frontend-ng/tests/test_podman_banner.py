"""Podman announces itself on stderr, and GINI reported it as the error.

From the Trottier lab, running a topology under Podman:

    Name resolution over the drawn network could not be set up on:
      M1: >>>> Executing external compose provider "/usr/bin/podman-compose". Please refer to
      the documentation for details. <

Podman 5 delegates `podman compose` to an external provider and says so, on stderr, for every
command. It is not an error, it is not about the command that ran, and it arrives FIRST — so code
reporting "the first 120 characters of stderr" reports the banner on every Podman machine, for
every failure, and the real cause never reaches the screen.
"""
from __future__ import annotations

from gini.setup.runtime import compose_error

BANNER = (b'>>>> Executing external compose provider "/usr/bin/podman-compose". '
          b'Please refer to the documentation for details. <\n')


def test_the_banner_alone_is_not_an_error():
    assert compose_error(BANNER) == ""


def test_the_real_error_survives_the_banner():
    raw = BANNER + b'Error: no container with name or ID "gini-m1" found: no such container\n'
    out = compose_error(raw)
    assert "no such container" in out
    assert "compose provider" not in out


def test_the_end_of_stderr_is_kept_rather_than_the_beginning():
    """A traceback puts its point on the last line and its preamble on the first. Truncating the
    front keeps the preamble, which is how a real error becomes a quote of somebody's boilerplate.
    """
    raw = BANNER + b"\n".join([b"Traceback (most recent call last):",
                               b'  File "/usr/bin/podman-compose", line 1, in <module>',
                               b"  File \"/usr/lib/python3/podman_compose.py\", line 9",
                               b"TypeError: exec() got an unexpected keyword argument 'T'"])
    out = compose_error(raw, limit=200)
    assert "TypeError" in out
    assert "Traceback (most recent" not in out


def test_a_docker_error_is_untouched():
    """Nothing about this may change what a Docker machine sees."""
    raw = b"Error response from daemon: container not running\n"
    assert compose_error(raw) == "Error response from daemon: container not running"


def test_empty_stays_empty_rather_than_becoming_a_message():
    assert compose_error(b"") == ""
    assert compose_error(None) == ""


def test_text_as_well_as_bytes():
    assert "boom" in compose_error("boom")


def test_the_app_names_the_engine_it_is_actually_using(monkeypatch):
    """The same log said "Topology running on Docker." on a machine with no Docker installed.
    `engine_name` exists for this and its own docstring says why: saying Docker when the machine
    has Podman is confusing, and on a campus box it reads as the app describing a different
    computer."""
    from gini.setup import runtime

    monkeypatch.setattr(runtime, "detect_engine", lambda run=None: "podman")
    assert runtime.engine_name() == "Podman"

    # String LITERALS, not raw source: the comment explaining this fix contains the phrase, and a
    # substring search over the file matches its own explanation and calls that a regression.
    import ast
    import pathlib as _p

    tree = ast.parse(_p.Path(runtime.__file__).parents[1].joinpath(
        "ui", "main_window.py").read_text(encoding="utf-8"))
    said = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    bad = [t for t in said if "on Docker" in t or "via Docker" in t]
    assert not bad, f"a hardcoded engine name is back in a user-facing message: {bad}"


# -- `up` exiting 0 is not the topology running --------------------------------- #

class _Cfg:
    """A RuntimeConfig's shape, as far as `_not_running` reads it."""

    def __init__(self, names):
        self._names = names

    def to_runtime(self, docker: bool):
        return {"machines": [{"name": n} for n in self._names],
                "routers": [], "switches": [], "services": []}


def _orch(tmp_path, ps_result):
    from gini.services.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)
    o.workdir = tmp_path
    o.project = ""
    object.__setattr__(type(o), "_dc", property(lambda self: ["docker", "compose"]))
    import gini.services.orchestrator as mod
    mod.subprocess = type("S", (), {"run": staticmethod(ps_result),
                                    "TimeoutExpired": TimeoutError,
                                    "PIPE": -1})
    return o, mod


def test_a_service_with_no_container_is_reported_as_not_started(tmp_path, monkeypatch):
    """The Trottier lab case: podman-compose printed `exit code: 125` for both machines, exited
    0 overall, and gBuilder said "Topology running". Every message after that was about the wrong
    thing — including a name-resolution failure that was really "there is no container"."""
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    def fake_run(cmd, **kw):
        svc = cmd[-1]
        out = "" if svc in ("m1", "m2") else "abc123\n"
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, ""
    assert o._not_running(_Cfg(["r1", "m1", "m2"])) == ["m1", "m2"]


def test_everything_running_reports_nothing(tmp_path, monkeypatch):
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: type(
        "R", (), {"returncode": 0, "stdout": "abc\n", "stderr": ""})())
    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, ""
    assert o._not_running(_Cfg(["r1", "m1"])) == []


def test_being_unable_to_ask_is_never_an_accusation(tmp_path, monkeypatch):
    """A `ps` that cannot run means we do not know, and "your topology did not start" is not what
    "we do not know" means. That is the same mistake this check exists to correct, reversed."""
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    def boom(cmd, **kw):
        raise OSError("compose is gone")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, ""
    assert o._not_running(_Cfg(["m1"])) == []


# -- "Running" then "idle", with three healthy containers on screen ------------- #

def _orch_at(project="gini-lab", wd="/tmp"):
    from gini.services.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = wd, project
    return o


def _ps(out, rc=0):
    def run(cmd, **kw):
        return type("R", (), {"returncode": rc, "stdout": out, "stderr": ""})()
    return run


def test_run_state_is_read_from_labels_when_compose_ps_says_nothing(monkeypatch):
    """`compose ps --format json` is docker compose v2's shape; podman-compose 1.0.6 does not
    produce it. status() came back empty, gBuilder read "no services" as "everything died", and a
    launch went Running -> idle one second later with three healthy containers in `podman ps`."""
    import gini.services.orchestrator as mod

    monkeypatch.setattr(mod.subprocess, "run", _ps(
        "gini-lab_r1_1\tUp 49 minutes\n"
        "gini-lab_m1_1\tUp 58 seconds\n"
        "gini-lab_m2_1\tUp 57 seconds\n"))
    assert _orch_at()._status_by_label("/tmp") == {
        "r1": "running", "m1": "running", "m2": "running"}


def test_both_naming_conventions_are_understood(monkeypatch):
    """podman-compose builds `project_service_1`; compose v2 builds `project-service-1`. The two
    disagree about the separator as well as about the JSON."""
    import gini.services.orchestrator as mod

    monkeypatch.setattr(mod.subprocess, "run", _ps("gini-lab-r1-1\tUp 2 minutes\n"))
    assert _orch_at()._status_by_label("/tmp") == {"r1": "running"}


def test_a_stopped_container_is_not_reported_as_running(monkeypatch):
    import gini.services.orchestrator as mod

    monkeypatch.setattr(mod.subprocess, "run", _ps(
        "gini-lab_m1_1\tUp 58 seconds\ngini-lab_m2_1\tExited (0) 2 minutes ago\n"))
    st = _orch_at()._status_by_label("/tmp")
    assert st["m1"] == "running"
    assert st["m2"] != "running"


def test_somebody_elses_container_is_not_ours(monkeypatch):
    """The label filter scopes it to this project, and the name pattern is a second gate — a box
    running two labs must not have one report the other's containers as its own."""
    import gini.services.orchestrator as mod

    monkeypatch.setattr(mod.subprocess, "run", _ps(
        "gini-lab_m1_1\tUp 1 minute\nsomeone-elses-thing\tUp 3 hours\n"))
    assert _orch_at()._status_by_label("/tmp") == {"m1": "running"}


def test_with_no_project_there_is_nothing_to_scope_to(monkeypatch):
    import gini.services.orchestrator as mod

    monkeypatch.setattr(mod.subprocess, "run", _ps("anything\tUp 1 minute\n"))
    assert _orch_at(project="")._status_by_label("/tmp") == {}


def test_an_engine_that_cannot_be_asked_reports_nothing_rather_than_guessing(monkeypatch):
    import gini.services.orchestrator as mod

    def boom(cmd, **kw):
        raise FileNotFoundError("podman")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert _orch_at()._status_by_label("/tmp") == {}


def test_compose_ps_printing_nothing_reaches_the_label_fallback(tmp_path, monkeypatch):
    """The bug in the first version of the fix. podman-compose's `ps --format json` writes NOTHING
    to stdout, and `status()` returned {} on empty output before it ever reached the fallback — so
    the symptom was unchanged and the fix looked applied.

    Reproduced the way the lab machine behaves: compose ps silent, engine ps full of containers.
    """
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    def run(cmd, **kw):
        if "compose" in cmd[:2] or cmd[1:2] == ["compose"]:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stderr": "", "stdout":
                              "gini-lab_r1_1\tUp 26 seconds\n"
                              "gini-lab_m1_1\tUp 25 seconds\n"
                              "gini-lab_m2_1\tUp 24 seconds\n"})()

    monkeypatch.setattr(mod.subprocess, "run", run)
    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, "gini-lab"
    assert o.status(tmp_path) == {"r1": "running", "m1": "running", "m2": "running"}


def test_docker_compose_answering_properly_never_reaches_the_fallback(tmp_path, monkeypatch):
    """The Docker side must be untouched: when compose's own `ps` answers, that is the answer, and
    the engine is never asked a second question."""
    import json

    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    asked = []

    def run(cmd, **kw):
        asked.append(cmd)
        if cmd[1:2] == ["compose"]:
            return type("R", (), {"returncode": 0, "stderr": "", "stdout": json.dumps(
                [{"Service": "m1", "State": "running"},
                 {"Service": "m2", "State": "exited"}])})()
        raise AssertionError("the engine must not be asked when compose answered")

    monkeypatch.setattr(mod.subprocess, "run", run)
    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, "gini-lab"
    assert o.status(tmp_path) == {"m1": "running", "m2": "exited"}
    assert len(asked) == 1
