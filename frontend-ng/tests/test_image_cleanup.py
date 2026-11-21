"""GINI gives back the disk it used: older versions' images after an update, volumes at Stop.

Students reported 20-30 GB going to Docker over a term, and the fix being passed around was
`docker system prune -af --volumes` — which also deletes everything else they had in Docker and
makes GINI download all four images again. Two things were never cleaned up: every update left
the previous version's images behind, and every Stop left the topology's anonymous volumes
(Postgres, Prometheus, k3s), which the next Run never reattaches.
"""
from __future__ import annotations

import subprocess as sp

import pytest

from gini.services import bootstrap as B
from gini.setup import images

REG = images.REGISTRY


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A private ~/.gini, so the setup marker is never the developer's real one."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path / "home"))


@pytest.fixture
def with_docker(monkeypatch):
    """A runtime is present (see test_bootstrap.with_docker for why it is patched here)."""
    monkeypatch.setattr(B.runtime, "docker_state", lambda run=None: "ok")


def _docker(listing: str, refuse=()):
    """A docker whose `images` prints `listing`; records every rmi, refusing those in `refuse`."""
    removed = []

    def run(cmd=(), *a, **k):
        cmd = list(cmd)
        if cmd[1:2] == ["images"]:
            return sp.CompletedProcess(cmd, 0, listing, "")
        if cmd[1:2] == ["rmi"]:
            if cmd[2] in refuse:
                return sp.CompletedProcess(cmd, 1, "", "conflict: container is using it")
            removed.append(cmd[2])
            return sp.CompletedProcess(cmd, 0, "", "")
        if cmd[1:3] == ["image", "inspect"]:
            names = cmd[5:] if cmd[3:5] == ["--format", "{{.Id}}"] else cmd[3:]
            return sp.CompletedProcess(cmd, 0, "\n".join(["sha256:ok"] * len(names)) + "\n", "")
        return sp.CompletedProcess(cmd, 0, "", "")

    return run, removed


LISTING = "\n".join([
    f"aaa {REG}/gini-xv6:6.22.12",
    f"aaa {REG}/gini-xv6:6.22.12-arm64",
    f"bbb {REG}/gini-xv6:6.22.10",
    f"bbb {REG}/gini-xv6:6.22.10-arm64",
    f"ccc {REG}/gini-xv6:6.22.7",
    f"ddd {REG}/gini-xv6:<none>",
    f"eee {REG}/gini-oszoo:6.22.8",
    f"fff {REG}/gini-oszoo:6.23.0",
    "aaa gini-xv6:latest",
    "ggg gini-xv6:alab03-test",
    "hhh gini-xv6:6.22.7",
    "iii ghcr.io/somebody-else/gini-xv6:6.0.0",
    "jjj postgres:15",
]) + "\n"


def test_older_versions_of_gini_images_are_removed():
    run, removed = _docker(LISTING)
    gone = images.remove_superseded(images.image_refs("6.22.12"), run=run)
    assert set(removed) == {f"{REG}/gini-xv6:6.22.10", f"{REG}/gini-xv6:6.22.10-arm64",
                            f"{REG}/gini-xv6:6.22.7", "ddd", f"{REG}/gini-oszoo:6.22.8"}
    assert len(gone) == len(removed)


def test_the_current_version_newer_ones_and_anything_not_gini_are_left_alone():
    run, removed = _docker(LISTING)
    images.remove_superseded(images.image_refs("6.22.12"), run=run)
    for kept in (f"{REG}/gini-xv6:6.22.12", f"{REG}/gini-xv6:6.22.12-arm64",
                 f"{REG}/gini-oszoo:6.23.0",       # newer: a downgrade keeps it
                 "gini-xv6:latest",                # what the runtime runs
                 "gini-xv6:alab03-test", "gini-xv6:6.22.7",   # a developer's own builds
                 "ghcr.io/somebody-else/gini-xv6:6.0.0", "postgres:15"):
        assert kept not in removed


def test_an_unversioned_install_removes_nothing():
    """A source build pulls `latest`: there is no "older" to compare against."""
    run, removed = _docker(LISTING)
    assert images.remove_superseded(images.image_refs(None), run=run) == []
    assert removed == []


def test_an_image_a_container_still_uses_is_not_forced_or_reported():
    run, removed = _docker(LISTING, refuse={f"{REG}/gini-xv6:6.22.7"})
    gone = images.remove_superseded(images.image_refs("6.22.12"), run=run)
    assert f"{REG}/gini-xv6:6.22.7" not in gone


def test_a_docker_that_cannot_list_images_is_not_an_error():
    def broken(cmd=(), *a, **k):
        raise FileNotFoundError("docker")
    assert images.remove_superseded(images.image_refs("6.22.12"), run=broken) == []


def test_an_update_removes_the_old_images_once_the_new_ones_are_all_here(monkeypatch,
                                                                         with_docker):
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    run, removed = _docker(f"bbb {REG}/gini-xv6:6.22.10\n")
    steps = []
    r = B.execute(B.plan("6.22.12", run=run), on_step=steps.append, run=run)
    assert r["ok"]
    assert removed == [f"{REG}/gini-xv6:6.22.10"]
    assert steps[-1] == "Removed 1 older GINI image tag."


def test_a_partial_update_keeps_the_old_images(monkeypatch, with_docker):
    """With any new image missing, the old ones may be all that runs: never remove them then."""
    monkeypatch.setattr(images, "find_backend", lambda hint=None: None)
    run, removed = _docker(f"bbb {REG}/gini-xv6:6.22.10\n")

    def half(cmd=(), *a, **k):
        if list(cmd)[1:2] == ["pull"] and "gini-xv6" in list(cmd)[-1]:
            return sp.CompletedProcess(cmd, 1, "", "network")
        return run(cmd, *a, **k)

    r = B.execute(B.plan("6.22.12", run=run), run=half)
    assert not r["ok"] and removed == []


def test_stop_removes_the_topologys_anonymous_volumes(tmp_path):
    from gini.services.orchestrator import Orchestrator
    o = Orchestrator(tmp_path)
    o.workdir = tmp_path
    seen = []
    o._compose = lambda *args: (seen.append(args), (True, "stopped"))[1]
    assert o.down()[0]
    assert seen == [("down", "-v")]
