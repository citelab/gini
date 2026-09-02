"""Deciding whether a lab image is on this machine — the check that refused to start topologies.

The report was "every now and then gBuilder gives this weird message and refuses to start", with:

    Run failed: The real gRouter image 'gini-grouter' isn't built yet, and I can't find the
    backend to build it.
    Build it once:
      cd /Users/…/pipx/venvs/gini-toolkit/lib/backend && docker build …

Two defects in one message, and the intermittency is the important one.

Docker Desktop's Resource Saver pauses the VM when the machine goes idle. The first command after
a wake reaches a daemon that ANSWERS — so it is not a connection failure, and no "is Docker
running?" check catches it — out of an image store that has not finished loading. `docker image
inspect gini-grouter` comes back "No such image" for an image that is right there; seconds later
the same command succeeds. Reproduced exactly that way while diagnosing it.

The second defect: from a wheel or pipx install there is no `backend/` and never was, so the
advice was a `cd` into a directory that does not exist. `gini-setup` is the answer there.
"""
from __future__ import annotations

import subprocess

import pytest

from gini.services.orchestrator import Orchestrator


class _Docker:
    """A fake `docker` that answers the way the real one does while it is waking up."""

    def __init__(self, fail_first: int = 0, never: bool = False):
        self.fail_first, self.never, self.calls = fail_first, never, 0

    def __call__(self, argv, **kw):
        self.calls += 1
        miss = self.never or self.calls <= self.fail_first
        return subprocess.CompletedProcess(
            argv, 1 if miss else 0, "",
            'Error response from daemon: {"message":"No such image: gini-grouter"}' if miss else "")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The retry pauses are real seconds; a test must not spend them."""
    monkeypatch.setattr("gini.services.orchestrator.time.sleep", lambda *_a: None)


def _present(monkeypatch, docker):
    monkeypatch.setattr("gini.services.orchestrator.subprocess.run", docker)
    return Orchestrator._image_present("gini-grouter")


def test_an_image_that_is_there_is_found_first_time(monkeypatch):
    d = _Docker()
    assert _present(monkeypatch, d) is True
    assert d.calls == 1, "no retry when the first answer is yes"


def test_a_waking_daemon_does_not_read_as_a_missing_image(monkeypatch):
    """THE bug. One "No such image" from a daemon that is loading its store is not evidence."""
    d = _Docker(fail_first=1)
    assert _present(monkeypatch, d) is True
    assert d.calls == 2


def test_a_slow_wake_is_still_not_a_missing_image(monkeypatch):
    d = _Docker(fail_first=2)
    assert _present(monkeypatch, d) is True


def test_an_image_that_really_is_absent_is_still_reported_absent(monkeypatch):
    """The retry must not turn a real absence into a hang or a false yes."""
    d = _Docker(never=True)
    assert _present(monkeypatch, d) is False
    assert d.calls == 3, "it gives up rather than retrying for ever"


# ---- and the advice that goes with it -------------------------------------------- #
def test_a_wheel_install_is_not_told_to_cd_into_a_backend_it_does_not_have():
    """pipx and pip installs have no `backend/`. The old message printed a build command rooted in
    the venv — a path that does not exist — which is advice nobody can follow."""
    msg = Orchestrator._no_image_advice("gini-grouter", "cd /nope && docker build .", False)
    assert "gini-setup" in msg
    assert "docker build" not in msg and "cd " not in msg


def test_a_source_checkout_is_still_told_how_to_build():
    msg = Orchestrator._no_image_advice("gini-grouter", "cd /repo/backend && docker build .", True)
    assert "docker build" in msg
    assert "gini-setup" not in msg


def test_a_stopped_daemon_says_so_instead_of_blaming_the_image(monkeypatch):
    """A daemon that is down and an image that is absent look identical to `inspect`, and they
    need opposite advice — the same distinction `setup/runtime.docker_state` already draws."""
    monkeypatch.setattr("gini.setup.runtime.docker_state", lambda **_k: "stopped")
    msg = Orchestrator._docker_not_ready(None)
    assert "not answering" in msg and "Start Docker" in msg


def test_a_healthy_daemon_has_nothing_to_say(monkeypatch):
    monkeypatch.setattr("gini.setup.runtime.docker_state", lambda **_k: "ok")
    assert Orchestrator._docker_not_ready(None) == ""


def test_a_broken_docker_check_does_not_take_the_run_down(monkeypatch):
    """It runs on the failure path of something already going wrong. It must not add an exception
    to it."""
    def boom(**_k):
        raise OSError("no docker here")
    monkeypatch.setattr("gini.setup.runtime.docker_state", boom)
    assert Orchestrator._docker_not_ready(None) == ""
