"""The image question the runtime asks: does gini-<name>:latest resolve?"""
from __future__ import annotations

import json

from fakes import FakeMachine, failed, ok
from gini_doctor.stage1 import diagnose, platforms, probes
from gini_doctor.stage1.probes import gini as gini_mod
from gini_doctor.stage1.redact import Redactor
from gini_doctor.stage1.report import Report

RED = Redactor(homes=[], users=[])
GH = "ghcr.io/gini-toolkit/%s:%s"


def run_gini(monkeypatch, images, inspect):
    monkeypatch.delenv("GINI_ENGINE", raising=False)
    monkeypatch.setattr(gini_mod, "gbuilder_python", lambda ctx: (None, "not found"))
    table = {("docker", "info"): ok("{}"), ("docker", "images"): ok("\n".join(images) + "\n")}
    for img in gini_mod.IMAGES:
        table[("docker", "image", "inspect", "--format", "{{.Id}}", img + ":latest")] = inspect.get(
            img, failed("Error: No such image: %s:latest" % img))
    FakeMachine(monkeypatch, table, installed=("docker",))
    r = Report(platforms.MACOS)
    probes.run_group("gini", r, platforms.MACOS, RED)
    assert "gini.probe_error" not in r.facts, r.facts.get("gini.probe_error")
    return r


def test_runtime_name_resolves_missing_or_listed_but_broken(monkeypatch):
    images = ["gini-xv6:latest", GH % ("gini-xv6", "6.11.2"),
              GH % ("gini-pox", "6.11.1"), GH % ("gini-pox", "6.11.2"),
              "gini-grouter:latest"]
    r = run_gini(monkeypatch, images, {"gini-xv6": ok("sha256:abc\n")})
    assert r.value("gini.image.gini-xv6.runtime") == "resolves"
    assert r.facts["gini.image.gini-pox.runtime"]["status"] == "absent"
    assert r.value("gini.image.gini-pox.newest_pull") == GH % ("gini-pox", "6.11.2")
    grouter = r.facts["gini.image.gini-grouter.runtime"]
    assert grouter["status"] == "error" and "No such image" in grouter["detail"]
    assert r.facts["gini.image.gini-oszoo"]["status"] == "absent"
    assert r.value("gini.image.engine") == "docker"


def test_findings_give_the_exact_tag_command(monkeypatch):
    images = [GH % ("gini-pox", "6.11.2"), "gini-grouter:latest"]
    r = run_gini(monkeypatch, images, {})
    found = {f.id: f for f in diagnose.diagnose(r)}
    assert found["image.gini-pox.runtime-missing"].command == \
        "docker tag ghcr.io/gini-toolkit/gini-pox:6.11.2 gini-pox:latest"
    assert found["image.gini-grouter.runtime-unresolvable"].command.startswith("docker tag \"$(docker images")
    # nothing was downloaded to find this out, and no tag was changed
    assert "image.gini-oszoo.runtime-missing" not in found     # nothing pulled, nothing to tag


def test_the_remedy_table_loads_through_the_package_loader():
    table = diagnose.load_table()
    assert table["version"] and len(table["rules"]) >= 26
    assert json.loads(json.dumps(table)) == table
