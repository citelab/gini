"""The engine group against scripted engine output, so it runs identically on every platform and
needs no Docker or Podman on the machine running the suite."""
from __future__ import annotations

import json

import pytest

from gini_doctor.stage1 import platforms, probes, runner
from gini_doctor.stage1.probes import engine as eng
from gini_doctor.stage1.redact import Redactor
from gini_doctor.stage1.report import Report

RED = Redactor(homes=[], users=[])

PODMAN_INFO = {
    "host": {"security": {"rootless": True}, "networkBackend": "netavark", "cgroupVersion": "v2",
             "cgroupManager": "systemd", "ociRuntime": {"name": "crun"},
             "conmon": {"version": "conmon version 2.1.10"}, "slirp4netns": {"executable": ""}},
    "store": {"graphDriverName": "overlay", "graphRoot": "/var/lib/x", "runRoot": "/run/x"},
}
DOCKER_INFO = {"ServerVersion": "27.3.1", "OperatingSystem": "Docker Desktop", "OSType": "linux",
               "Architecture": "aarch64", "NCPU": 8, "MemTotal": 8 * 1024 ** 3, "Driver": "overlayfs",
               "CgroupVersion": "2", "SecurityOptions": ["name=seccomp,profile=unconfined"]}


def fake_engines(monkeypatch, table, installed):
    """table maps a command prefix (tuple) to a Result; installed names the programs 'on PATH'."""
    def fake_run(argv, timeout=20.0, **kw):
        for prefix, result in table.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return result
        return runner.Result(runner.FAILED, 1, "", "unscripted: %s" % " ".join(argv))
    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner, "which", lambda p: "/usr/bin/%s" % p if p in installed else None)


def ok(stdout):
    return runner.Result(runner.OK, 0, stdout, "")


def collect(platform):
    r = Report(platform)
    probes.run_group("engine", r, platform, RED)
    assert "engine.probe_error" not in r.facts, r.facts.get("engine.probe_error")
    return r


def test_no_engine_at_all(monkeypatch):
    monkeypatch.delenv("GINI_ENGINE", raising=False)
    fake_engines(monkeypatch, {}, installed=())
    r = collect(platforms.LINUX)
    assert r.facts["engine.docker.path"]["status"] == "absent"
    assert r.value("engine.gini.engine.effective") == "none"
    assert r.facts["engine.podman.idmap.matches"]["status"] == "absent"


def test_rootless_podman_on_linux_with_stale_idmap(monkeypatch):
    monkeypatch.delenv("GINI_ENGINE", raising=False)
    # /etc/subuid grants 165536 today, but podman's storage was created with 100000.
    monkeypatch.setattr(eng, "subuid_start", lambda user, path=None: "165536")
    fake_engines(monkeypatch, {
        ("podman", "--version"): ok("podman version 4.9.3\n"),
        ("podman", "info"): ok(json.dumps(PODMAN_INFO)),
        ("podman", "images"): ok("aaa\nbbb\nccc\n"),
        ("podman", "unshare"): ok("         0       1000          1\n         1     100000      65536\n"),
    }, installed=("podman",))
    r = collect(platforms.LINUX)
    assert r.value("engine.podman.version") == "podman version 4.9.3"
    assert r.value("engine.podman.rootless") is True
    assert r.value("engine.podman.network.backend") == "netavark"
    assert r.facts["engine.podman.slirp4netns"]["status"] == "absent"
    assert r.value("engine.podman.images.count") == 3
    assert r.value("engine.gini.engine.effective") == "podman"
    assert r.value("engine.podman.idmap.inuse") == "100000"
    assert r.value("engine.podman.idmap.subuid") == "165536"
    assert r.value("engine.podman.idmap.matches") is False


def test_docker_desktop_on_macos_marks_linux_only_facts_not_applicable(monkeypatch):
    monkeypatch.delenv("GINI_ENGINE", raising=False)
    fake_engines(monkeypatch, {
        ("docker", "--version"): ok("Docker version 27.3.1, build ce12230\n"),
        ("docker", "info"): ok(json.dumps(DOCKER_INFO)),
    }, installed=("docker",))
    r = collect(platforms.MACOS)
    assert r.value("engine.docker.info") == "answering"
    assert r.value("engine.docker.os") == "Docker Desktop"
    assert r.value("engine.docker.memory.total_mb") == 8192
    assert r.value("engine.docker.rootless") is False
    assert r.value("engine.gini.engine.effective") == "docker"
    for k in eng.IDMAP_KEYS:
        assert r.facts["engine." + k] == {"status": "n/a"}


def test_a_docker_daemon_that_is_down_keeps_its_own_words(monkeypatch):
    monkeypatch.delenv("GINI_ENGINE", raising=False)
    down = {"ServerErrors": ["Cannot connect to the Docker daemon at unix:///var/run/docker.sock."]}
    fake_engines(monkeypatch, {
        ("docker", "--version"): ok("Docker version 27.3.1\n"),
        ("docker", "info"): runner.Result(runner.FAILED, 1, json.dumps(down), ""),
    }, installed=("docker",))
    r = collect(platforms.WINDOWS)
    assert r.facts["engine.docker.info"]["status"] == "error"
    assert "Cannot connect to the Docker daemon" in r.facts["engine.docker.info"]["detail"]
    assert r.value("engine.gini.engine.effective") == "docker (present but not answering)"


def test_gini_engine_env_wins(monkeypatch):
    monkeypatch.setenv("GINI_ENGINE", "podman")
    fake_engines(monkeypatch, {("docker", "info"): ok(json.dumps(DOCKER_INFO))}, installed=("docker",))
    r = collect(platforms.LINUX)
    assert r.value("engine.gini.engine.env") == "podman"
    assert r.value("engine.gini.engine.effective") == "podman (forced by GINI_ENGINE)"


@pytest.mark.parametrize("text,expected", [
    ("         0       1000          1\n         1     100000      65536\n", "100000"),
    ("         0       1000          1\n", None),
    ("", None),
])
def test_uid_map_parsing(text, expected):
    assert eng.uid_map_outer_start(text) == expected


def test_subuid_parsing(tmp_path):
    f = tmp_path / "subuid"
    f.write_text("alice:100000:65536\nstudent:165536:65536\n")
    assert eng.subuid_start("student", str(f)) == "165536"
    assert eng.subuid_start("nobody", str(f)) is None
    assert eng.subuid_start("student", str(tmp_path / "missing")) is None
