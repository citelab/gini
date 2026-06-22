"""Element size tiers (S/M/L/XL): cost scaling, CPU caps, persistence, node geometry."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.domain import pricing
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler
from gini.services.orchestrator import _compose


def test_resizable_scope():
    for k in ("web_app", "load_generator", "database", "instance", "container", "host"):
        assert pricing.resizable(k), k
    for k in ("switch", "router", "hub", "cloud", "controller"):
        assert not pricing.resizable(k), k


def test_size_tiers_double_cost():
    mults = [pricing.size_cost_mult(l) for l in (1, 2, 3, 4)]
    assert mults == [1, 2, 4, 8]
    assert pricing.size_label(1) == "S" and pricing.size_label(4) == "XL"
    assert pricing.size_level(99) == 4 and pricing.size_level(0) == 1   # clamped


def test_bill_scales_with_size():
    t = Topology("c")
    w = t.add_device("web_app")          # base rate 4
    w.size = 3                           # L -> x4
    base = pricing.rate_of("web_app")
    assert pricing.bill(t)["rate_per_hr"] == base * 4


def test_size_sets_cpu_limit_in_compose():
    t = Topology("c")
    w = t.add_device("web_app"); w.size = 3        # L -> 2 vCPU
    cfg = RuntimeCompiler().compile(t)
    spec = next(s for s in cfg.services if s.name == w.name)
    assert spec.cpus == 2.0
    compose = _compose(cfg)
    assert "deploy:" in compose and 'cpus: "2"' in compose


def test_machine_size_sets_cpu_limit():
    t = Topology("c")
    h = t.add_device("host"); r = t.add_device("router")
    t.add_link(h.id, r.id)
    h.size = 2                                       # M -> 1 vCPU
    cfg = RuntimeCompiler().compile(t)
    m = next(m for m in cfg.machines if m.name == h.name)
    assert m.cpus == 1.0
    assert 'cpus: "1"' in _compose(cfg)


def test_unsized_infra_has_no_cpu_cap():
    # switches/routers and the auto Prometheus/cAdvisor must not get a CPU limit
    t = Topology("c"); t.add_device("switch")
    assert "deploy:" not in _compose(RuntimeCompiler().compile(t))


def test_size_persists_round_trip():
    t = Topology("p")
    d = t.add_device("instance"); d.size = 4
    t2 = Topology.from_dict(t.to_dict())
    assert t2.devices[d.id].size == 4
    # an old project file without 'size' still loads (defaults to S)
    raw = t.to_dict()
    for dev in raw["devices"]:
        dev.pop("size", None)
    assert Topology.from_dict(raw).devices[d.id].size == 1


def test_update_cpus_safe_when_not_running():
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    o = Orchestrator(rt.__path__[0])           # no workdir yet (nothing launched)
    ok, msg = o.update_cpus("wa1", 2.0)
    assert ok is False and "not running" in msg


def test_update_cpus_builds_docker_update_command(monkeypatch):
    # verify the live path resolves the container id then calls `docker update --cpus`
    from gini.services.orchestrator import Orchestrator
    import gini.runtime as rt
    import subprocess as sp
    calls = []

    class R:
        def __init__(self, out="", rc=0): self.stdout, self.returncode, self.stderr = out, rc, ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["docker", "compose", "ps"]:
            return R(out="container123\n")
        return R(out="ok")
    monkeypatch.setattr(sp, "run", fake_run)
    o = Orchestrator(rt.__path__[0]); o.workdir = "/tmp/x"
    ok, _ = o.update_cpus("wa1", 2.0)
    assert ok is True
    assert ["docker", "update", "--cpus", "2", "container123"] in calls


def test_resize_emits_device_resized_signal():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    seen = []
    w.ctx.bus.device_resized.connect(lambda did: seen.append(did))
    wa = w.api.add_device("web_app", x=80, y=80)["id"]
    app.processEvents()
    w.canvas.scene_.nodes[wa]._bump_size(+1)
    assert seen == [wa]


def test_node_height_grows_only_for_resizable():
    from PySide6.QtWidgets import QApplication
    from gini.ui.main_window import MainWindow
    from gini.ui.canvas import NODE_H, SIZE_STEP
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    wa = w.api.add_device("web_app", x=100, y=100)["id"]
    sw = w.api.add_device("switch", x=320, y=100)["id"]
    app.processEvents()
    n_wa = w.canvas.scene_.nodes[wa]
    n_sw = w.canvas.scene_.nodes[sw]
    assert n_wa.node_h() == NODE_H                       # default S
    w.ctx.topology.devices[wa].size = 3                  # L = +2 steps taller
    assert n_wa.node_h() == NODE_H + 2 * SIZE_STEP
    assert n_sw.node_h() == NODE_H                       # switch never grows
    # the on-node stepper bumps the size and clamps at XL
    n_wa._bump_size(+1)                                  # L -> XL
    assert w.ctx.topology.devices[wa].size == 4
    n_wa._bump_size(+1)                                  # clamp
    assert w.ctx.topology.devices[wa].size == 4
