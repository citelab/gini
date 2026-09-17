"""parity: legacy shell reports against Stage 1 reports from the same machine."""
from __future__ import annotations

from gini_doctor.stage1 import parity
from gini_doctor.stage1.report import ABSENT, NA, OK, Report

LEGACY = """host\ttr-open-18
doctor.version\t6.14.0
user\tstudent
uid\t1000
kernel\tLinux 6.8.0-45-generic
cpus\t8
mem.total.kb\t16270000
podman.path\t/usr/bin/podman
podman.version\tpodman version 4.9.3
podman.rootless\ttrue
podman.idmap.matches\tNO — /etc/subuid grants 165536, podman storage uses 100000; run: podman system migrate
podman.images.count\t12
compose.provider\tpodman-compose
subuid\tstudent:165536:65536
linger\tLinger=yes
xdg.runtime.exists\tNO
cgroup2.user.controllers\tmemory pids
tool.pasta\tabsent
containers.conf\tabsent
pkg.gini-toolkit\t6.14.0 @ /home/student/.local/pipx/venvs/gini-toolkit/lib/python3.12/site-packages
image.gini-xv6\tgini-xv6:latest
live.container.by_label\tfound 3f2a1b
live.pull\tFAILED
auth.home.student..docker.config.json\tpresent bytes=112 creds=1
perf.cpu.freq.cur.khz\t2400000
something.new\tx
"""


def new_report(**overrides):
    r = Report("linux", host="tr-open-18")
    facts = {
        "system.kernel": (OK, "6.8.0-45-generic"), "system.cpus": (OK, 8),
        "system.memory.total_mb": (OK, 15888),
        "engine.podman.path": (OK, "/usr/bin/podman"), "engine.podman.version": (OK, "podman version 4.9.3"),
        "engine.podman.rootless": (OK, True), "engine.podman.idmap.matches": (OK, False),
        "engine.podman.images.count": (OK, 12), "compose.provider": (OK, "podman-compose"),
        "rootless.subuid": (OK, "165536:65536"), "rootless.linger": (OK, "yes"),
        "rootless.xdg.runtime.exists": (OK, False), "rootless.cgroup2.user.controllers": (OK, ["memory", "pids"]),
        "rootless.tool.pasta": (ABSENT, None), "registry.conf.containers.conf": (OK, False),
        "qt.gbuilder.python": (OK, "~/.local/pipx/venvs/gini-toolkit/bin/python"),
        "gini.pkg.gini-toolkit": (OK, "6.14.0"), "gini.image.gini-xv6": (OK, ["gini-xv6:latest"]),
        "live.container.by_label": (OK, "found"), "perf.cpu.freq.cur_khz": (OK, 1800000),
    }
    facts.update(overrides)
    for k, (status, value) in facts.items():
        r.set(k, status, value)
    return r


def test_same_machine_agrees_despite_different_spellings():
    p = parity.parity(parity.parse_legacy(LEGACY), new_report())
    assert p.disagree == [], p.disagree
    agreed = {r.legacy_key for r in p.agree}
    for key in ("kernel", "mem.total.kb", "podman.idmap.matches", "podman.rootless", "subuid", "linger",
                "xdg.runtime.exists", "cgroup2.user.controllers", "tool.pasta", "containers.conf",
                "pkg.gini-toolkit", "image.gini-xv6", "live.container.by_label"):
        assert key in agreed, key
    assert p.unmapped == ["something.new"]
    assert {k for k, _ in p.dropped} >= {"host", "user", "uid", "live.pull", "auth.home.student..docker.config.json"}
    assert [r.legacy_key for r in p.skipped] == ["perf.cpu.freq.cur.khz"]


def test_a_real_difference_is_reported():
    p = parity.parity(parity.parse_legacy(LEGACY), new_report(**{"engine.podman.idmap.matches": (OK, True)}))
    assert [r.legacy_key for r in p.disagree] == ["podman.idmap.matches"]
    assert "DISAGREE" in parity.format_text(p)


def test_a_fact_the_new_doctor_did_not_collect_is_not_silently_passed():
    report = new_report()
    del report.facts["compose.provider"]
    p = parity.parity(parity.parse_legacy(LEGACY), report)
    assert [r.legacy_key for r in p.missing_new] == ["compose.provider"]


def test_without_gbuilder_the_legacy_path_python_false_alarm_is_explained():
    legacy = "gbuilder.python\tpython3\npyside6\tModuleNotFoundError: No module named 'PySide6'\n" \
             "pkg.gini-core\tabsent @ -\n"
    r = Report("linux")
    r.set("qt.gbuilder.python", ABSENT, detail="no gbuilder launcher or pipx venv found")
    p = parity.parity(parity.parse_legacy(legacy), r)
    assert p.disagree == [] and p.missing_new == []
    assert {x.legacy_key for x in p.explained} == {"gbuilder.python", "pyside6", "pkg.gini-core"}


def test_mapping_templates_use_the_whole_key():
    assert parity._new_key("docker.version") == ("engine.docker.version", "text")
    assert parity._new_key("gini.engine.effective") == ("engine.gini.engine.effective", "text")
    assert parity._new_key("perf.cpu.freq.max.khz") == ("perf.cpu.freq.max_khz", "skip_noisy")
    assert parity._new_key("image.gini-pox") == ("gini.image.gini-pox", "image")
