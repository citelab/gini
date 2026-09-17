"""The remedy table: its integrity, and the findings it produces."""
from __future__ import annotations

import re

import pytest

from gini_doctor.stage1 import diagnose, platforms, probes
from gini_doctor.stage1.report import ABSENT, ERROR, OK, Report

TABLE = diagnose.load_table()
OPS = {"equals", "not_equals", "in", "contains", "not_contains", "status", "gt"}


def test_the_table_is_well_formed():
    probes.load_builtin()
    groups = set(probes.groups()) | {"stage0"}
    ids = [r["id"] for r in TABLE["rules"]]
    assert len(ids) == len(set(ids))
    for rule in TABLE["rules"]:
        assert rule["who"] in ("you", "admin"), rule["id"]
        assert rule["when"], rule["id"]
        for cond in rule["when"]:
            assert len(set(cond) & OPS) == 1, rule["id"]
            assert cond["fact"].split(".", 1)[0] in groups, (rule["id"], cond["fact"])
        for key in rule.get("verify", []):
            assert key.split(".", 1)[0] in groups, (rule["id"], key)
        if isinstance(rule.get("command"), dict):
            assert set(rule["command"]) <= platforms.ALL, rule["id"]
        for text in (rule["title"], rule.get("reason", "")):
            for ref in re.findall(r"\{([^}]+)\}", text):
                assert ref.split(".", 1)[0] in groups, (rule["id"], ref)


def _report(platform="linux", **facts):
    r = Report(platform)
    for k, v in facts.items():
        key = k.replace("__", ".")
        if v in (ABSENT, ERROR):
            r.set(key, v, detail="because")
        else:
            r.set(key, OK, v)
    return r


def ids(report):
    return [f.id for f in diagnose.diagnose(report)]


def test_stale_idmap_proposes_migrate_with_the_ranges_filled_in():
    r = _report(engine__podman__idmap__matches=False, engine__podman__idmap__inuse="100000",
                engine__podman__idmap__subuid="165536")
    [f] = diagnose.diagnose(r)
    assert f.id == "podman.idmap-stale" and f.command == "podman system migrate"
    assert "165536" in f.reason and "100000" in f.reason
    assert "stops every running" in f.side_effects.lower()
    assert f.verify == ["engine.podman.idmap.matches"]


def test_false_is_not_zero_and_zero_is_not_false():
    assert ids(_report(engine__podman__idmap__matches=0)) == []
    assert ids(_report(qt__lib__libxcb_cursor=False)) == []
    assert "qt.libxcb-cursor" in ids(_report(**{"qt__lib__libxcb-cursor": 0}))


def test_a_rule_without_a_command_for_this_platform_does_not_fire():
    assert ids(_report("macos", engine__podman__idmap__matches=False)) == []


def test_credential_rule_uses_the_platform_command():
    r = _report("windows", registry__credential_blocks=True, engine__gini__engine__effective="docker",
                registry__engine="docker: unauthorized")
    [f] = diagnose.diagnose(r)
    assert f.command == "docker logout"


def test_informational_findings_have_no_command():
    r = _report(perf__cpu__throttle__count=12)
    [f] = diagnose.diagnose(r)
    assert f.command is None and "12" in f.reason
    assert "nothing to run" in diagnose.format_text([f])


def test_a_healthy_report_has_no_findings():
    r = _report(engine__gini__engine__effective="docker", qt__gbuilder__python="/x/python",
                qt__pyside6="6.7.2", perf__cpu__governor="performance")
    assert ids(r) == []
    assert "Nothing in the remedy table" in diagnose.format_text([])
