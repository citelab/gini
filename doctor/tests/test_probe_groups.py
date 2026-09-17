"""compose, rootless, registry, qt, gini, perf: each against a scripted machine."""
from __future__ import annotations

import json
import os
import sys

import pytest

from fakes import FakeMachine, failed, ok
from gini_doctor.stage1 import platforms, probes
from gini_doctor.stage1.probes import _common, registry as reg, rootless
from gini_doctor.stage1.redact import Redactor
from gini_doctor.stage1.report import Report

RED = Redactor(homes=[], users=[])
PODMAN_JSON = ok(json.dumps({"host": {}, "store": {}}))


def collect(group, platform=platforms.LINUX, approve=probes.refuse):
    r = Report(platform)
    probes.run_group(group, r, platform, RED, approve=approve)
    assert "%s.probe_error" % group not in r.facts, r.facts.get("%s.probe_error" % group)
    return r


@pytest.fixture(autouse=True)
def _no_forced_engine(monkeypatch):
    monkeypatch.delenv("GINI_ENGINE", raising=False)


# ------------------------------------------------------------------------------------ compose
def test_compose_names_the_provider_podman_passes_through_to(monkeypatch):
    FakeMachine(monkeypatch, {
        ("podman", "info"): PODMAN_JSON,
        ("podman", "compose", "version"): ok(
            ">>>> Executing external compose provider \"/usr/bin/podman-compose\". <<<<\npodman-compose version 1.0.6\n"),
        ("podman-compose", "--version"): ok("podman-compose version 1.0.6\n"),
    }, installed=("podman", "podman-compose"))
    r = collect("compose")
    assert r.value("compose.provider") == "podman-compose"
    assert r.value("compose.provider.path") == "/usr/bin/podman-compose"
    assert r.value("compose.podman_compose.version") == "podman-compose version 1.0.6"
    assert r.facts["compose.docker_compose.version"]["status"] == "absent"


def test_compose_on_docker_is_the_builtin_plugin(monkeypatch):
    FakeMachine(monkeypatch, {("docker", "info"): ok("{}"),
                              ("docker", "compose", "version"): ok("Docker Compose version v2.29.7\n")},
                installed=("docker",))
    r = collect("compose", platforms.WINDOWS)
    assert r.value("compose.provider") == "docker-compose-plugin"
    assert r.value("compose.docker.plugin") == "Docker Compose version v2.29.7"
    assert r.facts["compose.plugin.file"] == {"status": "n/a"}


# ------------------------------------------------------------------------------------ rootless
def test_rootless_is_not_applicable_off_linux(monkeypatch):
    FakeMachine(monkeypatch)
    r = collect("rootless", platforms.MACOS)
    assert all(f == {"status": "n/a"} for k, f in r.facts.items() if k.startswith("rootless."))
    assert "rootless.subuid" in r.facts and "rootless.linger" in r.facts


def test_rootless_reads_ranges_runtime_dir_and_delegation(monkeypatch, tmp_path):
    files = {
        "/etc/subuid": "student:100000:65536\n",
        "/etc/subgid": "other:1:2\n",
        "/proc/sys/user/max_user_namespaces": "63936\n",
        "/sys/fs/cgroup/cgroup.controllers": "cpuset cpu io memory pids\n",
    }
    monkeypatch.setattr(rootless, "read_text", lambda path, limit=0: next(
        (v for k, v in files.items() if path == k), "memory pids\n" if path.endswith("user@1000.service/cgroup.controllers") else None))
    monkeypatch.setattr(rootless, "_user", lambda: ("student", 1000))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    FakeMachine(monkeypatch, {("loginctl",): ok("Linger=no\n"), ("crun", "--version"): ok("crun version 1.14.1\n")},
                installed=("loginctl", "crun", "newuidmap"))
    r = collect("rootless")
    assert r.value("rootless.subuid") == "100000:65536"
    assert r.facts["rootless.subgid"]["status"] == "absent"
    assert r.value("rootless.xdg.runtime.exists") is True
    assert r.value("rootless.linger") == "no"
    assert r.value("rootless.cgroup2.user.controllers") == ["memory", "pids"]
    assert r.value("rootless.tool.crun") == "crun version 1.14.1"
    assert r.facts["rootless.tool.pasta"]["status"] == "absent"


# ------------------------------------------------------------------------------------ registry
def test_registry_spots_a_stored_credential_blocking_pulls(monkeypatch):
    monkeypatch.setattr(reg, "anonymous_manifest", lambda timeout=10.0: (True, "HTTP 200"))
    fm = FakeMachine(monkeypatch, {
        ("docker", "info"): ok("{}"),
        ("docker", "manifest", "inspect"): failed("unauthorized: incorrect username or password"),
    }, installed=("docker",))
    r = collect("registry", platforms.MACOS)
    assert r.value("registry.anonymous") == "reachable (HTTP 200)"
    assert r.facts["registry.engine"]["status"] == "error"
    assert r.value("registry.credential_blocks") is True
    assert r.facts["registry.conf.system"] == {"status": "n/a"}
    # read-only: nothing that pulls, runs or logs in was ever called
    assert not [c for c in fm.calls if c[1:2] in (["pull"], ["run"], ["login"], ["logout"])]


def test_registry_unreachable_is_not_blamed_on_credentials(monkeypatch):
    monkeypatch.setattr(reg, "anonymous_manifest", lambda timeout=10.0: (False, "timed out"))
    FakeMachine(monkeypatch, {("podman", "info"): PODMAN_JSON,
                              ("podman", "manifest", "inspect"): failed("dial tcp: i/o timeout")},
                installed=("podman",))
    r = collect("registry")
    assert r.facts["registry.anonymous"] == {"status": "error", "detail": "timed out"}
    assert r.facts["registry.credential_blocks"]["status"] == "absent"


def test_registries_conf_settings_ignore_comments(tmp_path):
    f = tmp_path / "registries.conf"
    f.write_text('# unqualified-search-registries = ["nope"]\nunqualified-search-registries = ["docker.io"]\n'
                 'short-name-mode = "enforcing"  # strict\n')
    found = reg.config_lines(str(f))
    assert found["unqualified-search-registries"] == 'unqualified-search-registries = ["docker.io"]'
    assert found["short-name-mode"] == 'short-name-mode = "enforcing"'


# ------------------------------------------------------------------------------------ qt / gini
PIPX_TRAMPOLINE = "#!/bin/sh\n" + "'" * 3 + "exec' \"%s\" \"$0\" \"$@\"\n' " + "'" * 3 + "\nimport sys\n"


@pytest.mark.parametrize("body,expected", [
    ("#!/opt/venv/bin/python3.12\nimport sys\n", "/opt/venv/bin/python3.12"),
    (PIPX_TRAMPOLINE % "/Users/x/Library/Application Support/pipx/venvs/gini-toolkit/bin/python",
     "/Users/x/Library/Application Support/pipx/venvs/gini-toolkit/bin/python"),
    ('#!/bin/sh\nexec "/home/s/.local/pipx/venvs/gini-toolkit/bin/python" -m gbuilder "$@"\n',
     "/home/s/.local/pipx/venvs/gini-toolkit/bin/python"),
    ("#!/bin/sh\necho not a python launcher\n", None),
])
def test_launcher_python_reads_both_launcher_shapes(tmp_path, body, expected):
    f = tmp_path / "gbuilder"
    f.write_text(body)
    assert _common.launcher_python(str(f)) == expected


def test_qt_checks_pyside6_in_gbuilders_python_not_the_first_on_path(monkeypatch, tmp_path):
    venv_py = tmp_path / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("")
    launcher = tmp_path / "gbuilder"
    launcher.write_text(PIPX_TRAMPOLINE % venv_py)
    fm = FakeMachine(monkeypatch, {
        (str(venv_py), "--version"): ok("Python 3.12.7\n"),
        (str(venv_py), "-c"): failed("ImportError: libEGL.so.1: cannot open shared object file"),
        ("ldconfig", "-p"): ok("\tlibxcb-cursor.so.0 (libc6,x86-64) => /usr/lib/libxcb-cursor.so.0\n"),
    }, installed=("ldconfig",))
    monkeypatch.setattr(fm, "which", lambda p: str(launcher) if p == "gbuilder" else fm.__class__.which(fm, p))
    from gini_doctor.stage1 import runner
    monkeypatch.setattr(runner, "which", fm.which)
    r = collect("qt")
    assert r.value("qt.gbuilder.python") == str(venv_py)
    assert r.value("qt.gbuilder.python.found_by") == "gbuilder launcher"
    assert "libEGL.so.1" in r.facts["qt.pyside6"]["detail"]
    assert r.value("qt.lib.libxcb-cursor") == 1
    assert r.value("qt.lib.libGL") == 0


def test_gini_reports_versions_editable_installs_and_local_images(monkeypatch, tmp_path):
    venv_py = tmp_path / "python"
    venv_py.write_text("")
    monkeypatch.setattr(_common, "gbuilder_python", lambda ctx: (str(venv_py), "pipx venv"))
    from gini_doctor.stage1.probes import gini as gini_mod
    monkeypatch.setattr(gini_mod, "gbuilder_python", lambda ctx: (str(venv_py), "pipx venv"))
    meta = {"gini-core": {"version": "6.14.0", "editable": False},
            "gini-toolkit": {"version": "6.15.0.dev3", "editable": True}, "gini-teaching-center": None}
    FakeMachine(monkeypatch, {
        (str(venv_py), "-c", gini_mod.METADATA): ok(json.dumps(meta) + "\n"),
        (str(venv_py), "-c", gini_mod.IMPORT_CHECK): ok("ok\n"),
        ("docker", "info"): ok("{}"),
        ("docker", "images"): ok("gini-xv6:latest\ngini-grouter:6.14\nbusybox:latest\n"),
    }, installed=("docker",))
    r = collect("gini", platforms.WINDOWS)
    assert r.value("gini.pkg.gini-core") == "6.14.0"
    assert r.value("gini.pkg.gini-toolkit") == "6.15.0.dev3 (editable)"
    assert r.facts["gini.pkg.gini-teaching-center"]["status"] == "absent"
    assert r.value("gini.import") == "ok"
    assert r.value("gini.image.gini-xv6") == ["gini-xv6:latest"]
    assert r.facts["gini.image.gini-pox"]["status"] == "absent"


# ------------------------------------------------------------------------------------ perf
def test_perf_marks_linux_only_facts_not_applicable_elsewhere(monkeypatch):
    FakeMachine(monkeypatch, {("sysctl",): ok("Apple M3\n")}, installed=("sysctl",))
    r = collect("perf", platforms.MACOS)
    assert r.value("perf.cpu.model") == "Apple M3"
    assert r.facts["perf.cpu.governor"] == {"status": "n/a"}
    assert isinstance(r.value("perf.host.cpu.median_ms"), int)
