from gini_doctor.stage1 import platforms, probes
from gini_doctor.stage1.redact import Redactor
from gini_doctor.stage1.report import Report

RED = Redactor(homes=[], users=[])


def test_a_probe_is_not_run_off_its_platforms_and_its_facts_read_not_applicable():
    calls = []

    @probes.probe("_t_linux_only", platforms=[platforms.LINUX], na_keys=["subuid.range"])
    def linux_only(ctx):
        calls.append(1)
        ctx.ok("subuid.range", "100000:65536")

    mac = Report("macos")
    probes.run_group("_t_linux_only", mac, platforms.MACOS, RED)
    assert calls == [] and mac.facts["_t_linux_only.subuid.range"] == {"status": "n/a"}

    lin = Report("linux")
    probes.run_group("_t_linux_only", lin, platforms.LINUX, RED)
    assert lin.value("_t_linux_only.subuid.range") == "100000:65536"


def test_a_crashing_probe_becomes_a_fact_and_the_run_continues():
    @probes.probe("_t_crash")
    def crash(ctx):
        ctx.ok("before", 1)
        raise RuntimeError("surprise on this machine")

    r = Report("linux")
    assert probes.run_group("_t_crash", r, platforms.LINUX, RED)
    assert r.value("_t_crash.before") == 1
    err = r.facts["_t_crash.probe_error"]
    assert err["status"] == "error" and "surprise on this machine" in err["detail"]


def test_unknown_groups_are_reported_not_raised():
    assert probes.run_group("no-such-group", Report("linux"), platforms.LINUX, RED) is False


def test_platform_names_are_validated_at_registration():
    import pytest
    with pytest.raises(ValueError):
        probes.probe("_t_bad", platforms=["bsd"])


def test_system_group_identifies_this_machine():
    plat = platforms.current()
    r = Report(plat)
    probes.run_group("system", r, plat, Redactor())
    assert r.value("system.os.family") == plat
    assert "system.probe_error" not in r.facts, r.facts.get("system.probe_error")
    for key in ("system.arch", "system.cpus", "system.os.name", "system.memory.total_mb",
                "system.doctor.python.version", "system.wsl"):
        assert key in r.facts, key


def test_platform_mapping():
    assert platforms.current("win32") == "windows"
    assert platforms.current("cygwin") == "windows"
    assert platforms.current("darwin") == "macos"
    assert platforms.current("linux") == "linux"
    assert platforms.current("freebsd13") == "linux"
