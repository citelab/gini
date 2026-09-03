"""gini-setup — pure logic (OS plan, image refs/tags, marker, runtime detection, CLI --check)."""
import types

from gini.setup import IMAGES, REGISTRY, images, marker, runtime
from gini.setup.cli import main as setup_main


def test_detect_os_maps_platform(monkeypatch):
    for sysname, expect in [("Darwin", "macos"), ("Linux", "linux"), ("Windows", "windows")]:
        monkeypatch.setattr(runtime.platform, "system", lambda s=sysname: s)
        assert runtime.detect_os() == expect


def test_runtime_plan_is_per_os():
    assert "Colima" in runtime.runtime_plan("macos")["runtime"]
    assert runtime.runtime_plan("macos")["auto"]                 # mac can auto-install
    assert runtime.runtime_plan("linux")["auto"] == []           # linux is guided (sudo)
    assert "Docker Desktop" in runtime.runtime_plan("windows")["runtime"]  # no Colima on Windows


def test_docker_available_needs_cli_and_daemon(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _c: None)
    assert runtime.docker_available() is False                   # no CLI
    monkeypatch.setattr(runtime.shutil, "which", lambda _c: "/usr/bin/docker")
    ok = types.SimpleNamespace(returncode=0)
    bad = types.SimpleNamespace(returncode=1)
    assert runtime.docker_available(run=lambda *a, **k: ok) is True
    assert runtime.docker_available(run=lambda *a, **k: bad) is False


def test_image_tag_pins_release_else_latest():
    assert images.image_tag("6.1.0") == "6.1.0"                  # clean release -> pinned
    assert images.image_tag("6.0.1.dev0") == "latest"           # dev build -> latest
    assert images.image_tag("0.0.0+unknown") == "latest"
    assert images.image_tag(None) == "latest"


def test_image_refs_cover_all_custom_images():
    refs = images.image_refs("6.1.0")
    assert len(refs) == len(IMAGES)
    assert f"{REGISTRY}/gini-xv6:6.1.0" in refs
    assert all(r.startswith(REGISTRY + "/") for r in refs)


def test_pull_images_records_success_and_failure():
    def fake_run(cmd, **kw):
        ok = "gini-xv6" in cmd[-1]
        return types.SimpleNamespace(returncode=0 if ok else 1)
    res = dict(images.pull_images(["r/gini-xv6:1", "r/gini-oszoo:1"], run=fake_run))
    assert res["r/gini-xv6:1"] is True and res["r/gini-oszoo:1"] is False


def test_local_name_is_the_name_the_runtime_actually_resolves():
    assert images.local_name("ghcr.io/gini-toolkit/gini-grouter:6.1.0") == "gini-grouter:latest"
    assert images.local_name("ghcr.io/gini-toolkit/gini-xv6:latest") == "gini-xv6:latest"


def test_a_pull_also_tags_the_image_under_the_name_the_runtime_looks_for():
    """THE bug that left every pip install unable to Run anything.

    `docker pull` leaves an image under its REGISTRY reference, and nothing looks for it there:
    the orchestrator inspects `gini-grouter`, the compiler writes `gini-xv6:latest` into the
    compose file. Setup reported four successful pulls and the machine still had nothing runnable
    — and the error surfaced later, elsewhere, as "the gRouter image isn't built yet", pointing at
    a backend/ that a wheel does not contain.
    """
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0)

    ref = f"{REGISTRY}/gini-grouter:6.1.0"
    assert dict(images.pull_images([ref], run=fake_run))[ref] is True
    assert ["docker", "pull", ref] in calls
    assert ["docker", "tag", ref, "gini-grouter:latest"] in calls


def test_an_image_that_pulls_but_cannot_be_tagged_counts_as_a_failure():
    """Otherwise setup writes a marker saying this machine is ready, over an image nothing finds."""
    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=1 if cmd[1] == "tag" else 0)

    res = dict(images.pull_images(["r/gini-xv6:1"], run=fake_run))
    assert res["r/gini-xv6:1"] is False


def test_docker_state_tells_a_stopped_engine_from_an_absent_one(monkeypatch):
    ok = types.SimpleNamespace(returncode=0)
    bad = types.SimpleNamespace(returncode=1)
    monkeypatch.setattr(runtime.shutil, "which", lambda _c: None)
    assert runtime.docker_state() == "missing"
    monkeypatch.setattr(runtime.shutil, "which", lambda _c: "/usr/bin/docker")
    assert runtime.docker_state(run=lambda *a, **k: ok) == "ok"
    assert runtime.docker_state(run=lambda *a, **k: bad) == "stopped"   # installed, not answering


def test_every_os_says_how_to_START_the_runtime_not_only_how_to_install_it():
    for os_name in ("macos", "linux", "windows", "unknown-os"):
        assert runtime.runtime_plan(os_name).get("start"), os_name


def test_missing_locally_asks_docker_by_the_name_the_runtime_resolves():
    """Not by the registry reference — that is the name nothing looks for."""
    seen = []

    def fake(cmd, **k):
        seen.append(list(cmd))
        return types.SimpleNamespace(returncode=0)

    assert images.missing_locally([f"{REGISTRY}/gini-xv6:6.1.1"], run=fake) == []
    assert "gini-xv6:latest" in seen[0]
    assert not any(c.startswith("ghcr.io") for c in seen[0])


def test_missing_locally_names_exactly_what_is_gone():
    refs = [f"{REGISTRY}/gini-xv6:6.1.1", f"{REGISTRY}/gini-pox:6.1.1"]

    def fake(cmd, **k):
        names = [c for c in cmd if c.endswith(":latest")]
        present = len(names) == 1 and names[0].startswith("gini-pox")   # pox here, xv6 not
        return types.SimpleNamespace(returncode=0 if present else 1)

    assert images.missing_locally(refs, run=fake) == [f"{REGISTRY}/gini-xv6:6.1.1"]


def test_an_unreachable_docker_does_not_claim_the_images_are_gone():
    """That machine is already heading for NEEDS_RUNTIME; guessing here would offer a download to
    somebody whose engine is simply stopped."""
    def boom(cmd, **k):
        raise OSError("cannot talk to docker")

    assert images.missing_locally([f"{REGISTRY}/gini-xv6:6.1.1"], run=boom) == []


def test_marker_roundtrip_and_status(tmp_path, monkeypatch):
    monkeypatch.setenv("GINI_HOME", str(tmp_path))
    assert marker.is_setup_done() is False
    marker.write_marker({"version": "6.1.0", "images": ["r/gini-xv6:6.1.0"]})
    assert marker.is_setup_done() is True
    assert marker.setup_version() == "6.1.0"
    assert marker.needs_update("6.1.0") is False
    assert marker.needs_update("6.2.0") is True                  # app upgraded past setup


def test_cli_check_reports_without_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GINI_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "docker_available", lambda *a, **k: False)
    rc = setup_main(["--check"])
    out = capsys.readouterr().out
    assert rc == 0 and "runtime: NOT found" in out and "not run yet" in out


# --------------------------------------------------------------------------- #
# a tag Docker has lost track of
# --------------------------------------------------------------------------- #
# The user-visible complaint was two-headed: gBuilder "goes into pulling the images even when I
# don't expect it to find anything new", and Run refused to start a topology saying the gRouter
# image was not built. One cause. Docker's name index can stop resolving a tag the image itself
# still claims — seen on Docker Desktop 27.4.0 right after `pull` + `tag` of an image already
# present under another tag, which is exactly what `pull_images` does on every upgrade.
#
# `missing_locally` asks `docker image inspect <name>`, gets "No such image", and concludes all
# four are gone. So every launch re-downloaded them, the pull re-tagged, the tag did not stick,
# and the next launch did it again.
LISTING = "\n".join(("sha256:aaaaaaaaaaaa gini-xv6:latest",
                     "sha256:bbbbbbbbbbbb gini-oszoo:latest",
                     "sha256:cccccccccccc <none>:<none>"))


def _docker(resolvable: set, listing: str = LISTING):
    """A docker whose LISTING and whose INSPECT disagree — which is the whole bug."""
    seen = {"tagged": []}

    def run(argv, **kw):
        import subprocess as sp
        if argv[:2] == ["docker", "images"]:
            return sp.CompletedProcess(argv, 0, listing, "")
        if argv[:2] == ["docker", "tag"]:
            seen["tagged"].append(argv[3])
            resolvable.add(argv[3])
            return sp.CompletedProcess(argv, 0, "", "")
        # docker image inspect --format {{.Id}} NAME…  — the names start after the format value
        names = list(argv[5:])
        ok = all(n in resolvable for n in names)
        return sp.CompletedProcess(argv, 0 if ok else 1, "", "")
    run.seen = seen
    return run


def test_a_lost_tag_does_not_look_like_a_missing_image():
    """THE fix for "it keeps downloading the images". The tag is repaired and nothing is reported
    missing, so no download is proposed."""
    refs = [f"{REGISTRY}/gini-xv6:6.5.2", f"{REGISTRY}/gini-oszoo:6.5.2"]
    run = _docker(resolvable=set())
    assert images.missing_locally(refs, run=run) == []
    assert run.seen["tagged"] == ["gini-xv6:latest", "gini-oszoo:latest"]


def test_an_image_that_is_really_gone_is_still_reported():
    """The repair must not make setup blind. Nothing in the listing carries that name."""
    refs = [f"{REGISTRY}/gini-grouter:6.5.2"]
    run = _docker(resolvable=set())
    assert images.missing_locally(refs, run=run) == refs
    assert run.seen["tagged"] == []


def test_the_common_case_still_costs_one_call():
    """When everything resolves, nothing else is asked — this runs on every launch."""
    calls = []

    def run(argv, **kw):
        import subprocess as sp
        calls.append(argv[:3])
        return sp.CompletedProcess(argv, 0, "", "")
    assert images.missing_locally([f"{REGISTRY}/gini-xv6:6.5.2"], run=run) == []
    assert len(calls) == 1, "the healthy path must not list or tag anything"


def test_repair_tag_uses_the_run_it_is_given():
    """`run=subprocess.run` as a DEFAULT ARGUMENT binds at import, so patching this module's
    `subprocess` would be ignored and the real daemon reached instead. That is not hypothetical:
    it is what these tests did until the binding moved into the body."""
    import inspect as _inspect
    assert _inspect.signature(images.repair_tag).parameters["run"].default is None
