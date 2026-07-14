"""Advisory topology lint + multi-homing."""
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, validate


def _msgs(topo):
    return [i["message"] for i in validate(topo)]


def test_isolated_device_flagged():
    t = Topology("lab")
    t.add_device("host")                       # M1, connected to nothing
    msgs = _msgs(t)
    assert any("isn't connected" in m for m in msgs)


def test_standalone_xv6_and_peripherals_not_flagged_as_isolated():
    # xv6 runs standalone (no networking) and its peripherals are optional, so a lone
    # xv6/Screen/Keyboard/Storage Volume is NOT an "isolated device" the lint should nag about.
    t = Topology("lab")
    for tk in ("xv6", "terminal", "storage_volume"):
        t.add_device(tk)
    assert not any("isn't connected" in m for m in _msgs(t))


def test_machine_machine_link_has_no_gateway_warning():
    t = Topology("lab")
    a = t.add_device("host"); b = t.add_device("host")
    t.add_link(a.id, b.id)                      # valid point-to-point, but no router
    warns = [i for i in validate(t) if i["level"] == "warn"]
    assert sum("no gateway" in i["message"] for i in warns) == 2


def test_normal_topology_machine_has_no_warning():
    t = Topology("lab")
    r = t.add_device("router"); h = t.add_device("host")
    t.add_link(r.id, h.id)
    warns = [i for i in validate(t) if i["level"] == "warn" and i["device"] == "M1"]
    assert warns == []


def test_switch_loop_flagged():
    t = Topology("lab")
    s1 = t.add_device("switch"); s2 = t.add_device("switch"); s3 = t.add_device("switch")
    t.add_link(s1.id, s2.id); t.add_link(s2.id, s3.id); t.add_link(s3.id, s1.id)
    assert any("loop" in m.lower() for m in _msgs(t))


def test_multihomed_machine_gets_two_interfaces():
    t = Topology("lab")
    r1 = t.add_device("router"); r2 = t.add_device("router")
    m = t.add_device("host")
    t.add_link(m.id, r1.id); t.add_link(m.id, r2.id); t.add_link(r1.id, r2.id)
    cfg = RuntimeCompiler().compile(t)
    m1 = next(x for x in cfg.machines if x.name == "M1")
    assert len(m1.ifaces) == 2                  # one NIC per router (multi-homed)
    assert m1.gw                                 # has a default gateway
    subnets = {i.ip.split(".")[2] for i in m1.ifaces}
    assert len(subnets) == 2                      # two different subnets
