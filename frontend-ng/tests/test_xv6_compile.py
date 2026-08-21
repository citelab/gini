"""Compiling a topology with an xv6 Machine emits a standalone QEMU-RISC-V container."""
from gini.app import AppContext
from gini.services.compiler import RuntimeCompiler, _role


def test_xv6_role_is_standalone():
    assert _role("xv6") == "xv6"                # not "machine" -> no fabric addressing


def test_compile_emits_xv6_container_with_agent_and_serial_ports():
    ctx = AppContext()
    dev = ctx.add_device("xv6", x=0, y=0)
    dev.properties["Timeslice"] = "10"
    cfg = RuntimeCompiler().compile(ctx.topology)
    svc = next(s for s in cfg.services if s.type_key == "xv6")
    assert svc.image == "gini-xv6:latest"
    assert svc.env["XV6_QUANTUM"] == "10"
    agent = next(p for p in svc.ports if p["label"] == "agent")   # the live state feed
    assert agent["container"] == 5000 and isinstance(agent["host"], int)
    serial = next(p for p in svc.ports if p["label"] == "serial")  # the human console
    assert serial["container"] == 4444


def test_xv6_shadow_volume_is_persistent_under_gini_home(tmp_path, monkeypatch):
    # the Load-loop shadow folder is bind-mounted from ~/.gini/xv6-shadows/<name>/ (persistent +
    # discoverable), NOT the ephemeral compose workdir, so student edits survive a Stop/Run.
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    ctx = AppContext()
    ctx.add_device("xv6", x=0, y=0)
    cfg = RuntimeCompiler().compile(ctx.topology)
    svc = next(s for s in cfg.services if s.type_key == "xv6")
    vol = next(v for v in svc.volumes if v.endswith(":/opt/xv6-riscv/kernel/shadows"))
    host = vol.rsplit(":", 1)[0]
    assert host.startswith(str(tmp_path)) and "xv6-shadows" in host   # under the gini home
    from pathlib import Path
    assert Path(host).is_dir()                                        # created up-front, user-owned


def test_xv6_gets_no_fabric_addressing():
    ctx = AppContext()
    ctx.add_device("xv6", x=0, y=0)
    cfg = RuntimeCompiler().compile(ctx.topology)
    # a standalone kernel has no data-plane machine entry (that loop is role=="machine")
    machines = getattr(cfg, "machines", {}) or {}
    assert all("xv6" not in str(k).lower() for k in machines)

