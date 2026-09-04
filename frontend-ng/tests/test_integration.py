"""I-track: compile a canvas Topology to a runtime config and prove it RUNS.

Builds a two-subnet topology (the kind a student draws), compiles it, and drives the
compiled wiring through the in-process simulator until pings cross — same proof as the
R0 spike, but now starting from the domain model the GUI edits.
"""
import pytest

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler
from gini.services.orchestrator import simulate


def _simulate_or_skip(cfg):
    """A running sim, stopped when the test ends.

    This used to skip on OSError with the note "the in-process sims don't release their sockets",
    which was true and was the bug rather than the environment: each node's `run()` is a select
    loop meant to last as long as its container, so simulating in-process left a thread per node
    running for the remainder of the pytest session. They kept the fixed UDP binds (hence the
    skips) and, worse, kept selecting on live sockets while later Qt tests tore widgets down —
    the cause of the suite's random segfaults, minutes away from anything that named them.

    `Sim` can be stopped now, so the caller closes it and the ports really are free.
    """
    try:
        return simulate(cfg)
    except OSError as e:                      # a genuinely occupied port, not one of ours
        pytest.skip(f"UDP sim port unavailable: {e}")


def two_subnet_lab() -> Topology:
    t = Topology("lab")
    r1 = t.add_device("router")                      # R1
    s1 = t.add_device("switch")                      # S1  (subnet 1)
    s2 = t.add_device("switch")                      # S2  (subnet 2)
    h1 = t.add_device("host")                        # M1
    h2 = t.add_device("host")                        # M2
    h3 = t.add_device("host")                        # M3
    t.add_link(h1.id, s1.id)
    t.add_link(h2.id, s1.id)
    t.add_link(r1.id, s1.id)
    t.add_link(r1.id, s2.id)
    t.add_link(h3.id, s2.id)
    return t


def test_compiler_segments_and_addressing():
    cfg = RuntimeCompiler().compile(two_subnet_lab())
    assert len(cfg.subnets) == 2
    assert set(cfg.subnets.values()) == {"10.0.1.0/24", "10.0.2.0/24"}
    assert len(cfg.machines) == 3
    assert len(cfg.switches) == 2
    assert len(cfg.routers) == 1 and len(cfg.routers[0].ifaces) == 2
    # every machine has a gateway (the router on its segment)
    assert all(m.gw for m in cfg.machines)
    # all UDP bind ports are unique
    ports = [i.ep.bind_port for m in cfg.machines for i in m.ifaces]
    ports += [e.bind_port for s in cfg.switches for e in s.eps]
    ports += [i.ep.bind_port for r in cfg.routers for i in r.ifaces]
    assert len(ports) == len(set(ports))


def test_compiled_topology_actually_runs():
    cfg = RuntimeCompiler().compile(two_subnet_lab())
    # `with`, so the nodes' threads and their UDP binds end with the test rather than with the
    # pytest process — see `_simulate_or_skip` and `Sim.stop`.
    with _simulate_or_skip(cfg) as sim:
        # map names -> ips
        ip = {m.name.lower(): m.ifaces[0].ip.split("/")[0] for m in cfg.machines}
        # same subnet, through a user-space switch
        assert sim.ping("m1", ip["m2"]), "M1 -> M2 (L2 switch) failed"
        # cross subnet, through the user-space gRouter
        assert sim.ping("m1", ip["m3"]), "M1 -> M3 (routed) failed"


def test_groupings_are_skipped_with_notes():
    t = Topology("cloud")
    vpc = t.add_device("vpc")
    inst = t.add_device("instance")
    t.add_link(vpc.id, inst.id)
    cfg = RuntimeCompiler().compile(t)
    assert any("grouping" in n for n in cfg.notes)
