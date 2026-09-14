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


def test_an_empty_run_state_says_what_it_asked_and_what_came_back(monkeypatch, capsys):
    """Three wrong diagnoses in a row for "the topology is running and gBuilder says it stopped",
    each costing a round trip to somebody else's machine, and every time the machine knew. This is
    the line that makes the next one self-diagnosing."""
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    mod._SAID.clear()
    monkeypatch.setattr(mod.subprocess, "run", _ps(
        "somebody-elses\tUp 3 hours\nother_thing_1\tUp 1 minute\n"))
    o = _orch_at()
    assert o._status_by_label("/tmp") == {}
    err = capsys.readouterr().err
    assert "no containers matched project 'gini-lab'" in err
    assert "somebody-elses" in err, "what came back has to be in the message, not just a count"


def test_an_empty_project_is_not_an_empty_filter(monkeypatch, capsys):
    """This used to log "no project name, so there is no label to filter containers by" and give
    up — which was an accurate description of the code and the wrong behaviour. An empty
    `Orchestrator.project` is the NORMAL case; it means "not namespaced", not "unknown"."""
    import gini.services.orchestrator as mod

    mod._SAID.clear()
    monkeypatch.setattr(mod.subprocess, "run", _ps("gini-lab_m1_1\tUp 1 minute\n"))
    assert _orch_at(project="")._status_by_label("/tmp") == {"m1": "running"}
    assert capsys.readouterr().err == "", "nothing went wrong, so nothing should be reported"


def test_the_same_complaint_is_not_printed_on_every_poll(monkeypatch, capsys):
    """The run-state poll fires every couple of seconds. A line that repeated would bury the
    console it is meant to be read in."""
    import gini.services.orchestrator as mod

    mod._SAID.clear()
    monkeypatch.setattr(mod.subprocess, "run", _ps("nothing-of-ours\tUp 1 minute\n"))
    o = _orch_at()
    for _ in range(5):
        o._status_by_label("/tmp")
    assert capsys.readouterr().err.count("no containers matched") == 1


def test_the_project_is_the_one_the_compose_file_declares(monkeypatch):
    """The actual bug, after four wrong diagnoses.

    `Orchestrator.project` is the optional `-p` for per-student namespacing and is EMPTY in every
    normal run. The project name that containers are labelled with comes from the `name:` key this
    module writes into the compose file. The code looking for those containers read `self.project`,
    got "", filtered on nothing, and concluded a healthy topology had stopped.
    """
    import gini.services.orchestrator as mod

    monkeypatch.setattr(mod.subprocess, "run", _ps(
        "gini-lab_r1_1\tUp 26 seconds\n"
        "gini-lab_m1_1\tUp 25 seconds\n"
        "gini-lab_m2_1\tUp 24 seconds\n"))
    assert _orch_at(project="")._status_by_label("/tmp") == {
        "r1": "running", "m1": "running", "m2": "running"}


def test_namespacing_still_wins_over_the_files_name(monkeypatch):
    """`-p` overrides the file's `name:` for compose, so it must override it here too — otherwise
    two students on one machine would each see the other's containers as their own."""
    import gini.services.orchestrator as mod

    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stderr": "",
                              "stdout": "cs310-bob_m1_1\tUp 1 minute\n"})()

    monkeypatch.setattr(mod.subprocess, "run", run)
    assert _orch_at(project="cs310-bob")._status_by_label("/tmp") == {"m1": "running"}
    assert "label=com.docker.compose.project=cs310-bob" in seen["cmd"]


def test_the_writer_and_the_reader_use_one_constant():
    """Two copies of "gini-lab" is how this drifts back. The compose file's `name:` and the label
    filter must come from the same place."""
    import inspect

    import gini.services.orchestrator as mod

    src = inspect.getsource(mod)
    literals = src.count('"gini-lab"') + src.count("'gini-lab'")
    assert literals == 1, f"'gini-lab' is written {literals} times; it should be COMPOSE_PROJECT"
    assert 'name: {COMPOSE_PROJECT}' in src


# -- one broken teardown poisoning the next launch ------------------------------ #

def _fake_engine(ids, *, rm_ok=True, after_rm=None, calls=None):
    """An engine whose `ps -aq` lists `ids` until `rm -f` runs, then lists `after_rm`."""
    state = {"ids": list(ids)}

    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        if "rm" in cmd:
            if rm_ok:
                state["ids"] = list(after_rm if after_rm is not None else [])
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if "-aq" in cmd:
            return type("R", (), {"returncode": 0, "stderr": "",
                                  "stdout": "\n".join(state["ids"])})()
        # the label-format ps used by _status_by_label
        rows = "".join(f"gini-lab_m{i}_1\tUp 1 minute\n" for i, _ in enumerate(state["ids"], 1))
        return type("R", (), {"returncode": 0, "stdout": rows, "stderr": ""})()
    return run


def test_containers_left_by_a_failed_teardown_are_removed_directly(tmp_path, monkeypatch):
    """Rootless Podman: `down` dies with "rootless netns: kill network process: permission
    denied", compose gives up, and the containers survive. Stop is not finished at that point."""
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, ""
    monkeypatch.setattr(o, "_stop_advertiser", lambda: None, raising=False)
    monkeypatch.setattr(Orchestrator, "_compose",
                        lambda self, *a: (False, "rootless netns: kill network process"))
    monkeypatch.setattr(mod.subprocess, "run", _fake_engine(["aaa", "bbb"]))

    ok, msg = o.down()
    assert ok, "Stop is not done while the containers are still there"
    assert "removed 2 container(s) directly" in msg
    assert "rootless netns" in msg, "and it still says what compose could not do"


def test_a_teardown_that_cannot_be_forced_still_reports_failure(tmp_path, monkeypatch):
    """Saying "stopped" while containers survive would be worse than the error."""
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, ""
    monkeypatch.setattr(o, "_stop_advertiser", lambda: None, raising=False)
    monkeypatch.setattr(Orchestrator, "_compose", lambda self, *a: (False, "nope"))
    monkeypatch.setattr(mod.subprocess, "run",
                        _fake_engine(["aaa"], rm_ok=False, after_rm=["aaa"]))
    ok, _ = o.down()
    assert not ok


def test_a_successful_down_never_touches_the_engine(tmp_path, monkeypatch):
    """The Docker path must be unchanged: when compose works, nothing else runs."""
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, ""
    monkeypatch.setattr(o, "_stop_advertiser", lambda: None, raising=False)
    monkeypatch.setattr(Orchestrator, "_compose", lambda self, *a: (True, "done"))

    def boom(cmd, **kw):
        raise AssertionError("the engine must not be asked after a clean down")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert o.down() == (True, "done")


def test_a_launch_clears_debris_before_compose_sees_it(tmp_path, monkeypatch, capsys):
    """Each launch writes a NEW workdir while the project name is fixed, so leftovers are adopted
    rather than replaced — and a half-stopped container fails with "must be in Created or Stopped
    state to be started". That was the whole alternating run/fail/run/fail."""
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    mod._SAID.clear()
    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, ""
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_engine(["old1", "old2"], calls=calls))

    assert o._purge() == 2
    assert any("rm" in c for c in calls), "leftovers must actually be removed"
    assert o._purge() == 0, "and a second sweep finds nothing to do"


def test_purging_nothing_is_not_an_error(tmp_path, monkeypatch):
    import gini.services.orchestrator as mod
    from gini.services.orchestrator import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o.workdir, o.project = tmp_path, ""
    monkeypatch.setattr(mod.subprocess, "run", _fake_engine([]))
    assert o._purge() == 0
