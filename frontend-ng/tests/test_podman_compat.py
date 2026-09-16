"""Podman as a first-class engine — the Trottier lab machines have it, not Docker.

GINI talks to the container engine only through the CLI. These tests pin the detection
order (Docker first, Podman only when there is no docker binary) and the call sites that
used to hardcode ``docker``. They never need a real Podman: ``run`` is the seam.
"""
from __future__ import annotations

import types

import pytest

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _cadvisor_command, _cadvisor_volumes
import gini.services.orchestrator as mod_orch
from gini.services.orchestrator import Orchestrator, _compose
from gini.setup import images, runtime
from gini.setup.images import PullProgress


@pytest.fixture(autouse=True)
def _no_cached_engine():
    """The suite pins Docker; this file is the one that has to see the probe."""
    runtime._reset_engine_cache()
    yield
    runtime._reset_engine_cache()


def _fnf(name="docker"):
    def run(cmd, **_k):
        raise FileNotFoundError(2, "No such file or directory", cmd[0] if cmd else name)
    return run


def _ok(*_a, **_k):
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def _podman_only(cmd, **_k):
    if cmd[0] == "docker":
        raise FileNotFoundError(2, "No such file or directory", "docker")
    if cmd[0] == "podman":
        return types.SimpleNamespace(returncode=0, stdout="8", stderr="")
    raise FileNotFoundError(2, "No such file or directory", cmd[0])


def test_detect_engine_names_podman_when_docker_is_absent():
    assert runtime.detect_engine(run=_podman_only) == "podman"
    assert runtime.engine_cli(run=_podman_only) == ["podman"]
    assert runtime.compose_cli(run=_podman_only) == ["podman", "compose"]
    assert runtime.engine_name(run=_podman_only) == "Podman"


def test_detect_engine_prefers_docker_when_both_answer():
    seen = []

    def run(cmd, **_k):
        seen.append(cmd[0])
        return _ok()

    assert runtime.detect_engine(run=run) == "docker"
    assert "podman" not in seen


def test_a_stopped_docker_is_not_a_reason_to_pick_podman():
    def run(cmd, **_k):
        if cmd[0] == "docker":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")
        raise AssertionError("must not fall through to podman when docker exists")

    assert runtime.detect_engine(run=run) == "docker"
    assert runtime.docker_state(run=run) == "stopped"


def test_docker_state_falls_through_to_podman_info():
    assert runtime.docker_state(run=_podman_only) == "ok"
    assert runtime.docker_state(run=_fnf()) == "missing"


def test_compose_available_on_podman_only_machines():
    def run(cmd, **_k):
        if cmd[:3] == ["docker", "compose", "version"]:
            raise FileNotFoundError(2, "No such file or directory", "docker")
        if cmd[:3] == ["podman", "compose", "version"]:
            return types.SimpleNamespace(returncode=0)
        raise AssertionError(cmd)

    assert runtime.compose_available(run=run) is True


def test_compose_plugin_missing_on_docker_is_not_podman_compose():
    def run(cmd, **_k):
        if cmd[:3] == ["docker", "compose", "version"]:
            return types.SimpleNamespace(returncode=125)
        raise AssertionError("must not ask podman when docker exists")

    assert runtime.compose_available(run=run) is False


def test_engine_cli_falls_back_to_docker_when_nothing_is_there():
    assert runtime.detect_engine(run=_fnf()) == "missing"
    assert runtime.engine_cli(run=_fnf()) == ["docker"]


def test_orchestrator_compose_prefix_follows_the_engine(monkeypatch):
    runtime._ENGINE = "podman"
    orch = Orchestrator(runtime_dir=".")
    assert orch._dc[:2] == ["podman", "compose"]
    orch.project = "lab1"
    assert orch._dc == ["podman", "compose", "-p", "lab1"]


def test_vm_memory_mib_understands_podman_host_memtotal(monkeypatch):
    Orchestrator._vm_mem_mib = None
    runtime._ENGINE = "podman"
    calls = []

    def fake_run(cmd, **_k):
        calls.append(list(cmd))
        fmt = cmd[cmd.index("--format") + 1] if "--format" in cmd else ""
        out = "8589934592" if fmt == "{{.Host.MemTotal}}" else ""
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr("gini.services.orchestrator.subprocess.run", fake_run)
    mib = Orchestrator(runtime_dir=".").vm_memory_mib()
    assert mib == pytest.approx(8192.0)
    assert any("{{.MemTotal}}" in c for c in calls)
    assert any("{{.Host.MemTotal}}" in c for c in calls)
    Orchestrator._vm_mem_mib = None


def test_runtime_available_is_false_on_podman():
    runtime._ENGINE = "podman"
    assert Orchestrator(runtime_dir=".").runtime_available("kata") is False


def test_cadvisor_drops_docker_only_on_podman():
    runtime._ENGINE = "podman"
    assert "-docker_only=true" not in _cadvisor_command()
    assert any("/var/lib/containers/" in v for v in _cadvisor_volumes())
    runtime._ENGINE = "docker"
    assert "-docker_only=true" in _cadvisor_command()
    assert any("/var/lib/docker/" in v for v in _cadvisor_volumes())


def test_compiled_observability_on_podman_does_not_demand_docker_graphdriver():
    runtime._ENGINE = "podman"
    t = Topology("obs")
    t.add_device("metrics")
    cfg = RuntimeCompiler().compile(t)
    cad = next(s for s in cfg.services if s.name == "cAdvisor")
    assert "-docker_only=true" not in cad.command
    compose = _compose(cfg)
    assert "/var/lib/containers/" in compose
    assert "-docker_only=true" not in compose


def test_pull_progress_reads_podman_copying_blob_lines():
    p = PullProgress()
    assert p.feed("Copying blob abcdef0123456789 [----] 0.0b / 3.4MiB")
    assert p.feed("Copying blob abcdef0123456789 done")
    assert p.feed("Copying config fedcba9876543210 done")
    assert (p.done, p.total) == (2, 2)
    assert p.fraction == 1.0
    assert p.feed("Getting image source signatures") is False


def test_image_commands_use_the_podman_prefix():
    runtime._ENGINE = "podman"
    seen = []

    def run(cmd, **_k):
        seen.append(list(cmd))
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    images.missing_locally(["ghcr.io/gini-toolkit/gini-xv6:6.1.0"], run=run)
    assert seen and seen[0][0] == "podman"
    assert seen[0][1:3] == ["image", "inspect"]


def test_GINI_ENGINE_forces_podman_even_when_docker_answers(monkeypatch):
    """A Mac with Docker Desktop AND a Podman machine must still be able to take the lab path."""
    monkeypatch.setenv("GINI_ENGINE", "podman")
    seen = []

    def run(cmd, **_k):
        seen.append(cmd[0])
        return _ok()

    assert runtime.detect_engine(run=run) == "podman"
    assert runtime.engine_cli(run=run) == ["podman"]
    assert runtime.docker_state(run=run) == "ok"
    assert "docker" not in seen
    assert runtime.compose_available(run=run) is True
    assert seen.count("podman") >= 1


def test_settings_prefers_podman_when_configured(monkeypatch, tmp_path):
    """Settings preference should override auto-detection when no env var is set."""
    # Clear any GINI_ENGINE to test settings preference
    monkeypatch.delenv("GINI_ENGINE", raising=False)
    
    # Create a temporary config file with podman preference
    import json
    from gini.app import paths
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"container_engine": "podman"}))
    
    # Monkeypatch the config loading to use our test config
    def fake_load_config():
        return json.loads(config_file.read_text())
    
    monkeypatch.setattr(paths, "load_config", fake_load_config)
    
    seen = []

    def run(cmd, **_k):
        seen.append(cmd[0])
        return _ok()

    # Reset cache to force re-detection with new settings
    runtime._reset_engine_cache()
    
    assert runtime.detect_engine(run=run) == "podman"
    assert runtime.engine_cli(run=run) == ["podman"]
    assert "docker" not in seen


def test_settings_prefers_docker_when_configured(monkeypatch, tmp_path):
    """Settings preference for Docker should override auto-detection."""
    monkeypatch.delenv("GINI_ENGINE", raising=False)
    
    import json
    from gini.app import paths
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"container_engine": "docker"}))
    
    def fake_load_config():
        return json.loads(config_file.read_text())
    
    monkeypatch.setattr(paths, "load_config", fake_load_config)
    
    # Even if podman is available, docker should be chosen
    def run(cmd, **_k):
        return _ok()

    runtime._reset_engine_cache()
    
    assert runtime.detect_engine(run=run) == "docker"
    assert runtime.engine_cli(run=run) == ["docker"]


def test_settings_auto_allows_normal_detection(monkeypatch, tmp_path):
    """Settings set to 'auto' should allow normal auto-detection."""
    monkeypatch.delenv("GINI_ENGINE", raising=False)
    
    import json
    from gini.app import paths
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"container_engine": "auto"}))
    
    def fake_load_config():
        return json.loads(config_file.read_text())
    
    monkeypatch.setattr(paths, "load_config", fake_load_config)
    
    # Podman only machine should still be detected
    assert runtime.detect_engine(run=_podman_only) == "podman"
    assert runtime.engine_cli(run=_podman_only) == ["podman"]


def test_rootless_podman_detection():
    """Test that rootless Podman can be detected from podman info output."""
    def rootless_run(cmd, **_k):
        if cmd[:4] == ["podman", "info", "--format", "{{.Host.Security.Rootless}}"]:
            return types.SimpleNamespace(returncode=0, stdout="true", stderr="")
        raise AssertionError(cmd)
    
    assert runtime._is_rootless_podman(run=rootless_run) is True
    
    def rootful_run(cmd, **_k):
        if cmd[:4] == ["podman", "info", "--format", "{{.Host.Security.Rootless}}"]:
            return types.SimpleNamespace(returncode=0, stdout="false", stderr="")
        raise AssertionError(cmd)
    
    assert runtime._is_rootless_podman(run=rootful_run) is False
    
    def error_run(cmd, **_k):
        raise FileNotFoundError("podman not found")
    
    assert runtime._is_rootless_podman(run=error_run) is False


# --------------------------------------------------------------------------- #
# Finding a container without asking the compose provider to guess its name.
#
# Reported from the campus lab: every machine failed name resolution at once. The exec behind it,
# run by hand, said:
#
#     podman compose exec -T m1 sh -lc 'echo hi'
#     >>>> Executing external compose provider "/usr/bin/podman-compose" <<<<
#     podman exec ... gini-lab_m1_1 sh -lc echo hi
#     Error: no container with name or ID "gini-lab_m1_1" found: no such container
#     exit code: 125
#
# podman-compose 1.0.6 builds `project_service_1`; compose v2 builds `project-service-1`. The
# provider does not fall back when its guess is wrong — it just fails. Every exec in gBuilder went
# through `compose exec`, so probes, riders, kubectl and the element console were failing the same
# way on the same machine, unreported, at the same time.
# --------------------------------------------------------------------------- #

def _orch_with_engine(monkeypatch, engine="podman"):
    # GINI_ENGINE outranks the probe cache, and conftest pins the suite to Docker — so this is
    # the knob, not runtime._ENGINE.
    monkeypatch.setenv("GINI_ENGINE", engine)
    runtime._reset_engine_cache()
    o = Orchestrator(runtime_dir=".")
    o.workdir = "/tmp/gini-lab-xyz"
    return o


def _ps_returning(cid, seen):
    def run(cmd, **_k):
        seen.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stdout=(cid + "\n") if cid else "", stderr="")
    return run


def test_a_container_is_found_by_label_not_by_a_guessed_name(monkeypatch):
    seen = []
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("c0ffee123456", seen))
    o = _orch_with_engine(monkeypatch)
    assert o.container_id("m1") == "c0ffee123456"
    argv = seen[0]
    assert argv[:3] == ["podman", "ps", "-q"]
    assert "label=com.docker.compose.project=gini-lab" in argv
    assert "label=com.docker.compose.service=m1" in argv
    # the thing that broke: no name is constructed anywhere
    assert not any("gini-lab_m1_1" in a or "gini-lab-m1-1" in a for a in argv)


def test_exec_goes_straight_to_the_engine_when_the_container_is_known(monkeypatch):
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("abc123", []))
    o = _orch_with_engine(monkeypatch)
    assert o.exec_argv("m1") == ["podman", "exec", "-i", "abc123"]


def test_exec_falls_back_to_compose_when_no_container_is_labelled(monkeypatch):
    """An unlabelled container, or an engine whose `ps` says nothing, behaves as before."""
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("", []))
    o = _orch_with_engine(monkeypatch)
    assert o.exec_argv("m1") == ["podman", "compose", "exec", "-T", "m1"]


def test_env_flags_land_before_the_container_id(monkeypatch):
    """`podman exec -e K=V <id> cmd` — after the id they would be arguments to the command."""
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("abc123", []))
    o = _orch_with_engine(monkeypatch)
    argv = o.exec_argv("faas", {"GINI_FN": "hello"})
    assert argv == ["podman", "exec", "-i", "-e", "GINI_FN=hello", "abc123"]
    assert argv.index("-e") < argv.index("abc123")


def test_env_flags_land_before_the_service_on_the_compose_fallback(monkeypatch):
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("", []))
    o = _orch_with_engine(monkeypatch)
    argv = o.exec_argv("faas", {"GINI_FN": "hello"})
    assert argv == ["podman", "compose", "exec", "-T", "-e", "GINI_FN=hello", "faas"]


def test_the_lookup_is_cached_so_a_probe_loop_does_not_re_ask_every_time(monkeypatch):
    seen = []
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("abc123", seen))
    o = _orch_with_engine(monkeypatch)
    for _ in range(5):
        o.exec_argv("m1")
    assert len(seen) == 1


def test_a_relaunch_invalidates_the_cache(monkeypatch):
    """Container ids do not survive down/up, and a stale one execs into nothing."""
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("abc123", []))
    o = _orch_with_engine(monkeypatch)
    assert o.container_id("m1") == "abc123"
    monkeypatch.setattr(Orchestrator, "_compose", lambda self, *a: (True, "done"))
    monkeypatch.setattr(o, "_stop_advertiser", lambda: None, raising=False)
    o.down()
    assert o._cids == {}


def test_the_cache_survives_an_instance_built_without_init():
    """Tests build bare Orchestrators to exercise one method; that must not AttributeError."""
    o = Orchestrator.__new__(Orchestrator)
    assert o._cids == {}
    o._cids["m1"] = "abc"
    assert o._cids == {"m1": "abc"}


def test_an_orchestrator_stub_without_exec_argv_still_works():
    """probes/riders take whatever object they are handed — the suite hands them small stubs."""
    stub = types.SimpleNamespace(_dc=["podman", "compose", "-p", "lab1"])
    assert mod_orch.exec_argv_for(stub, "m1") == [
        "podman", "compose", "-p", "lab1", "exec", "-T", "m1"]
    assert mod_orch.exec_argv_for(stub, "faas", {"A": "b"}) == [
        "podman", "compose", "-p", "lab1", "exec", "-T", "-e", "A=b", "faas"]


def test_exec_argv_for_prefers_a_real_orchestrator(monkeypatch):
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("abc123", []))
    o = _orch_with_engine(monkeypatch)
    assert mod_orch.exec_argv_for(o, "m1") == ["podman", "exec", "-i", "abc123"]


def test_exec_uses_an_id_compose_can_find_even_when_labels_cannot(monkeypatch):
    """`exec_argv` asks the same question as every other id lookup in the class.

    It used to call `container_id` (labels only) while `update_cpus` and the stats sampler went
    through `_resolve_cid` (labels, then `compose ps -q`). So a container the labels could not
    see but compose could took the FRAGILE path — `compose exec` — in the same second that a
    sibling method got a real id for the same service.
    """
    def fake_run(cmd, **_k):
        if any(str(a).startswith("label=") for a in cmd):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")   # labels: blind
        return types.SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(mod_orch.subprocess, "run", fake_run)
    o = _orch_with_engine(monkeypatch)
    assert o.exec_argv("m1") == ["podman", "exec", "-i", "abc123"]


def test_exec_still_falls_back_when_no_id_exists_anywhere(monkeypatch):
    monkeypatch.setattr(mod_orch.subprocess, "run", _ps_returning("", []))
    o = _orch_with_engine(monkeypatch)
    assert o.exec_argv("m1") == ["podman", "compose", "exec", "-T", "m1"]


def test_env_flags_have_one_definition(monkeypatch):
    """Three copies of this loop had drifted into orchestrator and main_window."""
    assert mod_orch._env_flags(None) == []
    assert mod_orch._env_flags({}) == []
    assert mod_orch._env_flags({"A": "b", "C": "d"}) == ["-e", "A=b", "-e", "C=d"]
