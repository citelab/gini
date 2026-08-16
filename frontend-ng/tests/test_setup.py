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
