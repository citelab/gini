"""Parity on the first real machine: a macOS laptop with Docker Desktop (2026-09-17).

The first run reported 13 disagreements. One was a real gap (image lists cut to six tags, hiding
the newest and the plain local tags); the other twelve were the legacy doctor running Linux probes
on macOS, or spelling differences. These are those exact legacy lines against what Stage 1 reported,
so none of them can come back as a disagreement.
"""
from __future__ import annotations

from gini_doctor.stage1 import parity
from gini_doctor.stage1.probes import gini as gini_mod
from gini_doctor.stage1.report import ABSENT, ERROR, NA, OK, Report

GH = "ghcr.io/gini-toolkit/gini-%s:%s"
XV6_TAGS = ["gini-xv6:latest"] + [GH % ("xv6", v) for v in
                                  ("6.11.2", "6.11.2-arm64", "6.11.1", "6.11.1-arm64", "6.11.0", "6.11.0-arm64",
                                   "6.10.0", "6.10.0-arm64")]

LEGACY = "\n".join([
    "cgroup2.root.controllers\tcgroup v1 or unreadable",
    "xdg.runtime.exists\tNO",
    "gbuilder.path\t/Users/maheswar/.local/bin/gbuilder",
    "gbuilder.python\t/Users/maheswar/Library/Application Support/pipx/venvs/gini-toolkit/bin/python",
    "image.gini-xv6\t" + " ".join(XV6_TAGS)[:120],
    "mem.total.kb\t?",
    "os.id\tDarwin",
    "os.pretty\tno /etc/os-release",
    "os.version\t24.6.0",
    "pkg.gini-teaching-center\tabsent @ -",
    "podman.info.ok\tNO — podman is installed but not answering",
    "kernel\tDarwin 24.6.0",
]) + "\n"


def mac_report():
    r = Report("macos", host="Maheshs-Laptop.local")
    for key, status, value in [
        ("rootless.cgroup2.root.controllers", NA, None), ("rootless.xdg.runtime.exists", NA, None),
        ("gini.gbuilder.path", OK, "~/.local/bin/gbuilder"),
        ("qt.gbuilder.python", OK, "~/Library/Application Support/pipx/venvs/gini-toolkit/bin/python"),
        ("gini.image.gini-xv6", OK, gini_mod.order_tags(XV6_TAGS)),
        ("system.memory.total_mb", OK, 24576), ("system.os.id", OK, "macos"),
        ("system.os.name", OK, "macOS 15.7.5"), ("system.os.version", OK, "15.7.5"),
        ("system.kernel", OK, "24.6.0"), ("gini.pkg.gini-teaching-center", ABSENT, None),
    ]:
        r.set(key, status, value)
    r.set("engine.podman.info", ERROR, detail="failed: Cannot connect to Podman.")
    return r


def test_the_first_macos_parity_run_has_no_disagreements():
    p = parity.parity(parity.parse_legacy(LEGACY), mac_report())
    assert p.disagree == [], parity.format_text(p)
    assert p.unmapped == []
    improved = {r.legacy_key for r in p.improved}
    agreed = {r.legacy_key for r in p.agree}
    assert {"xdg.runtime.exists", "mem.total.kb", "os.pretty"} <= improved
    # A Linux non-answer against n/a is either; what matters is that it never disagrees.
    assert "cgroup2.root.controllers" in improved | agreed
    assert {"gbuilder.path", "gbuilder.python", "image.gini-xv6", "os.id", "os.version",
            "pkg.gini-teaching-center", "podman.info.ok", "kernel"} <= agreed


def test_a_real_macos_difference_still_disagrees():
    report = mac_report()
    report.set("system.os.version", OK, "14.0")
    p = parity.parity(parity.parse_legacy("os.version\t23.1.0\n"), report)
    assert [r.legacy_key for r in p.disagree] == ["os.version"]


def test_image_tags_keep_local_and_newest_first():
    shuffled = [GH % ("xv6", "6.10.0"), GH % ("xv6", "6.11.2"), "gini-xv6:latest", GH % ("xv6", "6.11.10"),
                GH % ("xv6", "6.11.1")]
    assert gini_mod.order_tags(shuffled) == ["gini-xv6:latest", GH % ("xv6", "6.11.10"), GH % ("xv6", "6.11.2"),
                                             GH % ("xv6", "6.11.1"), GH % ("xv6", "6.10.0")]
    many = [GH % ("pox", "6.%d.0" % i) for i in range(20)]
    assert len(gini_mod.order_tags(many)) == gini_mod.MAX_TAGS
    assert gini_mod.order_tags(many)[0] == GH % ("pox", "6.19.0")
