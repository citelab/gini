"""gLoader: spec -> compile -> launch."""
from pathlib import Path

from gini.domain import Topology
from gini.services import GLoader, save_project
from gini import runtime as rt


def _runtime_dir() -> Path:
    return Path(rt.__file__).parent


def _one_lan() -> tuple[Topology, str, str]:
    t = Topology("demo")
    m1 = t.add_device("host")
    m2 = t.add_device("host")
    s = t.add_device("switch")
    t.add_link(m1.id, s.id)
    t.add_link(m2.id, s.id)
    return t, m1.name, m2.name


def test_compile_produces_plan():
    t, _, _ = _one_lan()
    cfg = GLoader(_runtime_dir()).compile(t)
    assert len(cfg.machines) == 2
    assert len(cfg.switches) == 1
    assert len(cfg.subnets) == 1


def test_simulate_from_topology_pings():
    t, m1, m2 = _one_lan()
    gl = GLoader(_runtime_dir())
    m2ip = gl.compile(t).machines[1].ifaces[0].ip.split("/")[0]
    sim = gl.simulate(t)
    sim.start()
    assert sim.ping(m1.lower(), m2ip) is True


def test_loads_and_runs_from_gini_spec(tmp_path):
    t, m1, m2 = _one_lan()
    spec = tmp_path / "demo.gini"
    save_project(t, spec)

    gl = GLoader(_runtime_dir())
    # gLoader reads the saved .gini spec, compiles it, and brings it up (in-process)
    assert gl.read_spec(spec).name == "demo"
    m2ip = gl.compile(gl.read_spec(spec)).machines[1].ifaces[0].ip.split("/")[0]
    sim = gl.simulate(spec)
    sim.start()
    assert sim.ping(m1.lower(), m2ip) is True
