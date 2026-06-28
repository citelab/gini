"""VPC isolation: each VPC compiles to its own isolated Docker network; elements placed
inside it (via parent_id) attach there instead of the flat `gini` bridge."""
import re

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _svc
from gini.services.orchestrator import _compose


def _net_of(compose: str, service: str) -> str:
    """The network list emitted for a given service block in the compose text."""
    m = re.search(rf"\n  {re.escape(service)}:\n(?:.*\n)*?    networks: \[([^\]]*)\]",
                  compose)
    assert m, f"no networks line for service {service}"
    return m.group(1)


def _vpc_with(*members):
    """A topology: one VPC containing the given (type_key) members via parent_id."""
    t = Topology("cloud")
    vpc = t.add_device("vpc")
    ids = [t.add_device(tk, parent_id=vpc.id) for tk in members]
    return t, vpc, ids


def test_member_service_joins_its_vpc_network():
    t, vpc, (db,) = _vpc_with("database")
    cfg = RuntimeCompiler().compile(t)
    netname = _svc(vpc.name)
    # the compiler records the VPC network + the db's membership
    assert [n.name for n in cfg.networks] == [netname]
    assert cfg.services[0].network == netname
    # and the compose attaches the db to that net, not gini
    comp = _compose(cfg)
    assert f"\n  {netname}:\n" in comp and "driver: bridge" in comp
    assert _net_of(comp, _svc(db.name)) == netname


def test_two_vpcs_are_isolated_with_distinct_subnets():
    t = Topology("cloud")
    v1 = t.add_device("vpc"); v2 = t.add_device("vpc")
    a = t.add_device("database", parent_id=v1.id)
    b = t.add_device("cache", parent_id=v2.id)
    cfg = RuntimeCompiler().compile(t)
    n1, n2 = _svc(v1.name), _svc(v2.name)
    nets = {n.name: n.cidr for n in cfg.networks}
    assert set(nets) == {n1, n2}
    assert nets[n1] != nets[n2]                      # Docker rejects overlapping subnets
    comp = _compose(cfg)
    assert _net_of(comp, _svc(a.name)) == n1
    assert _net_of(comp, _svc(b.name)) == n2         # different network => can't reach a


def test_element_outside_any_vpc_stays_on_gini():
    t = Topology("cloud")
    db = t.add_device("database")                    # no VPC parent
    cfg = RuntimeCompiler().compile(t)
    assert cfg.networks == []
    assert cfg.services[0].network == "gini"
    assert _net_of(_compose(cfg), _svc(db.name)) == "gini"


def test_nested_subnet_resolves_to_the_vpc():
    # service -> subnet -> vpc: membership walks up to the VPC
    t = Topology("cloud")
    vpc = t.add_device("vpc")
    sub = t.add_device("cloud_subnet", parent_id=vpc.id)
    db = t.add_device("database", parent_id=sub.id)
    cfg = RuntimeCompiler().compile(t)
    assert cfg.services[0].network == _svc(vpc.name)


def test_cloudfabric_multihomes_across_vpcs():
    # the telemetry agent must reach services in every VPC, so it joins all VPC nets
    t = Topology("cloud")
    v1 = t.add_device("vpc"); v2 = t.add_device("vpc")
    t.add_device("database", parent_id=v1.id)
    t.add_device("cache", parent_id=v2.id)
    t.add_device("metrics", parent_id=v1.id)         # forces the cloud fabric agent on
    cfg = RuntimeCompiler().compile(t)
    comp = _compose(cfg)
    fab_nets = set(_net_of(comp, "cloudfabric").replace(" ", "").split(","))
    assert {"gini", _svc(v1.name), _svc(v2.name)} <= fab_nets


def test_no_vpc_means_unchanged_flat_compose():
    # backward compatibility: a plain cloud topology emits no extra networks
    t = Topology("cloud")
    t.add_device("database"); t.add_device("cache")
    comp = _compose(RuntimeCompiler().compile(t))
    # only the default gini bridge in the networks: section (no vpc_* nets)
    head = comp.split("services:", 1)[0]
    assert head.count("driver: bridge") == 1


def test_duplicate_default_cidrs_are_made_unique():
    # two VPCs both default to 10.0.0.0/16; the compiler must not emit overlapping subnets
    t = Topology("cloud")
    v1 = t.add_device("vpc"); v2 = t.add_device("vpc")
    t.add_device("database", parent_id=v1.id)
    t.add_device("cache", parent_id=v2.id)
    cidrs = [n.cidr for n in RuntimeCompiler().compile(t).networks]
    assert len(cidrs) == len(set(cidrs))             # all distinct
