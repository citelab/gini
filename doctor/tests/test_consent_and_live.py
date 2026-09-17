"""Nothing that goes beyond looking runs without a yes, and what does run cleans up after itself."""
from __future__ import annotations

import io
import json

from fakes import FakeMachine, failed, ok
from gini_doctor.stage1 import cli, platforms, probes
from gini_doctor.stage1.redact import Redactor
from gini_doctor.stage1.report import Report

RED = Redactor(homes=[], users=[])


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_live_and_xv6_are_skipped_without_consent_and_nothing_runs(monkeypatch):
    fm = FakeMachine(monkeypatch, {("docker", "info"): ok("{}")}, installed=("docker",))
    for group in ("live", "xv6"):
        r = Report(platforms.LINUX)
        probes.run_group(group, r, platforms.LINUX, RED)
        assert "consent" in r.value("%s.skipped" % group)
    assert fm.calls == []


def test_the_approver_asks_on_a_terminal_and_refuses_without_one():
    out = io.StringIO()
    assert cli.make_approver(False, stdin=Tty("y\n"), stdout=out)("live", "runs a container") is True
    assert "runs a container" in out.getvalue() and "[y/N]" in out.getvalue()
    assert cli.make_approver(False, stdin=Tty("\n"), stdout=io.StringIO())("live", "x") is False
    assert cli.make_approver(False, stdin=io.StringIO("y\n"), stdout=io.StringIO())("live", "x") is False
    assert cli.make_approver(True, stdin=io.StringIO(""), stdout=io.StringIO())("live", "x") is True


def test_every_group_that_starts_something_declares_consent():
    probes.load_builtin()
    assert probes.consent_text("live") and probes.consent_text("xv6")
    for group in ("system", "engine", "compose", "rootless", "registry", "qt", "gini", "perf"):
        assert probes.consent_text(group) == "", group


def test_live_round_trip_with_consent_and_it_removes_what_it_made(monkeypatch):
    fm = FakeMachine(monkeypatch, {
        ("docker", "info"): ok("{}"),
        ("docker", "images"): ok("gini-grouter:latest\nbusybox:latest\n"),
        ("docker", "run"): ok(""),
        ("docker", "compose"): ok(""),
        ("docker", "ps", "-q"): ok("c0ffee\n"),
        ("docker", "ps", "-a", "--filter"): ok("ginidoctor1-probe-1\n"),
        ("docker", "exec"): ok("ok\n"),
        ("docker", "ps", "-aq"): ok("c0ffee\n"),
        ("docker", "rm"): ok(""),
    }, installed=("docker",))
    # compose exec fails (podman-compose style) while engine exec works
    fm.table[("docker", "compose", "-f")] = ok("")
    r = Report(platforms.LINUX)
    probes.run_group("live", r, platforms.LINUX, RED, approve=lambda g, t: True)
    assert "live.probe_error" not in r.facts, r.facts.get("live.probe_error")
    assert r.value("live.image") == "busybox:latest"
    assert r.value("live.run") == "ok"
    assert r.value("live.container.by_label") == "found"
    assert r.value("live.exec.engine") == "ok"
    assert r.value("live.compose.down.leftovers") == 1
    assert ["docker", "rm", "-f", "c0ffee"] in fm.calls
    assert not [c for c in fm.calls if "pull" in c]


def test_live_never_pulls_when_there_is_no_local_image(monkeypatch):
    fm = FakeMachine(monkeypatch, {("podman", "info"): ok("{}"), ("podman", "images"): ok("")},
                     installed=("podman",))
    r = Report(platforms.LINUX)
    probes.run_group("live", r, platforms.LINUX, RED, approve=lambda g, t: True)
    assert "does not pull" in r.value("live.skipped")
    assert not [c for c in fm.calls if "pull" in c or "run" in c]


def test_xv6_removes_its_container_even_when_the_agent_never_answers(monkeypatch):
    import time
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    fm = FakeMachine(monkeypatch, {
        ("docker", "info"): ok("{}"),
        ("docker", "images", "-q"): ok("abc123\n"),
        ("docker", "run"): ok("abc123\n"),
        ("docker", "exec"): failed("connection refused"),
        ("docker", "rm"): ok(""),
    }, installed=("docker",))
    r = Report(platforms.MACOS)
    probes.run_group("xv6", r, platforms.MACOS, RED, approve=lambda g, t: True)
    assert "never answered" in r.facts["xv6.boot"]["detail"]
    assert fm.calls[-1][:3] == ["docker", "rm", "-f"]
