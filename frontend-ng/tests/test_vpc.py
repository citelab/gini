"""VPC + Subnet networking. A VPC is an isolated, internal Docker bridge (the implicit VPC
fabric); a *public* subnet's members also join a per-VPC egress bridge (internet + host
consoles); a *private* subnet's members stay internal-only (no egress)."""
import re

from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler, _svc
from gini.services.orchestrator import _compose


def _nets(compose: str, service: str) -> set:
    """The set of networks the compose attaches to a given service block."""
    m = re.search(rf"\n  {re.escape(service)}:\n(?:.*\n)*?    networks: \[([^\]]*)\]", compose)
    assert m, f"no networks line for service {service}"
    return set(m.group(1).replace(" ", "").split(","))


def _net_block(compose: str, net: str) -> str:
    """The compose definition block for a network (to check driver/internal/ipam)."""
    head = compose.split("services:", 1)[0]
    m = re.search(rf"\n  {re.escape(net)}:\n((?:    .*\n)*)", head)
    return m.group(1) if m else ""


def test_member_with_no_subnet_is_public_on_vpc_plus_egress():
    t = Topology("cloud")
    vpc = t.add_device("vpc")
    db = t.add_device("database", parent_id=vpc.id)        # in the VPC, no subnet -> public
    cfg = RuntimeCompiler().compile(t)
    v, eg = _svc(vpc.name), f"{_svc(vpc.name)}_egress"
    assert {n.name for n in cfg.networks} == {v, eg}
    assert cfg.services[0].networks == [v, eg]
    comp = _compose(cfg)
    assert _nets(comp, _svc(db.name)) == {v, eg}
    assert "internal: true" in _net_block(comp, v)         # the VPC fabric is internal
    assert "internal: true" not in _net_block(comp, eg)    # egress is a normal bridge


def test_private_subnet_member_has_no_egress():
    t = Topology("cloud")
    vpc = t.add_device("vpc")
    sub = t.add_device("cloud_subnet", parent_id=vpc.id)   # default Tier = private
    db = t.add_device("database", parent_id=sub.id)
    cfg = RuntimeCompiler().compile(t)
    v = _svc(vpc.name)
    assert cfg.services[0].networks == [v]                 # internal VPC net only — no egress
    assert {n.name for n in cfg.networks} == {v}           # no egress net created
    assert _nets(_compose(cfg), _svc(db.name)) == {v}


def test_public_subnet_member_gets_egress():
    t = Topology("cloud")
    vpc = t.add_device("vpc")
    sub = t.add_device("cloud_subnet", parent_id=vpc.id)
    sub.properties["Tier"] = "public"
    db = t.add_device("database", parent_id=sub.id)
    cfg = RuntimeCompiler().compile(t)
    v, eg = _svc(vpc.name), f"{_svc(vpc.name)}_egress"
    assert cfg.services[0].networks == [v, eg]
    assert _nets(_compose(cfg), _svc(db.name)) == {v, eg}


def test_public_and_private_share_the_vpc_fabric():
    # a web tier (public) and a db tier (private) in one VPC can still reach each other —
    # they share the internal VPC net — but only the web tier has internet.
    t = Topology("cloud")
    vpc = t.add_device("vpc")
    pub = t.add_device("cloud_subnet", parent_id=vpc.id); pub.properties["Tier"] = "public"
    priv = t.add_device("cloud_subnet", parent_id=vpc.id)         # private
    web = t.add_device("web_app", parent_id=pub.id)
    db = t.add_device("database", parent_id=priv.id)
    cfg = RuntimeCompiler().compile(t)
    v, eg = _svc(vpc.name), f"{_svc(vpc.name)}_egress"
    comp = _compose(cfg)
    assert v in _nets(comp, _svc(web.name)) and v in _nets(comp, _svc(db.name))   # shared fabric
    assert eg in _nets(comp, _svc(web.name))                      # web has egress
    assert eg not in _nets(comp, _svc(db.name))                   # db does not


def test_two_vpcs_are_isolated_with_distinct_subnets():
    t = Topology("cloud")
    v1 = t.add_device("vpc"); v2 = t.add_device("vpc")
    a = t.add_device("database", parent_id=v1.id)
    b = t.add_device("cache", parent_id=v2.id)
    cfg = RuntimeCompiler().compile(t)
    n1, n2 = _svc(v1.name), _svc(v2.name)
    cidrs = {n.name: n.cidr for n in cfg.networks if n.internal}
    assert set(cidrs) == {n1, n2} and cidrs[n1] != cidrs[n2]      # non-overlapping subnets
    comp = _compose(cfg)
    assert n1 in _nets(comp, _svc(a.name)) and n2 not in _nets(comp, _svc(a.name))


def test_element_outside_any_vpc_stays_on_gini():
    t = Topology("cloud")
    db = t.add_device("database")
    cfg = RuntimeCompiler().compile(t)
    assert cfg.networks == [] and cfg.services[0].networks == ["gini"]
    assert _nets(_compose(cfg), _svc(db.name)) == {"gini"}


def test_cloudfabric_multihomes_on_the_internal_fabric_nets():
    t = Topology("cloud")
    v1 = t.add_device("vpc"); v2 = t.add_device("vpc")
    t.add_device("database", parent_id=v1.id)
    t.add_device("cache", parent_id=v2.id)
    t.add_device("metrics", parent_id=v1.id)              # forces the cloud fabric agent on
    comp = _compose(RuntimeCompiler().compile(t))
    fab = _nets(comp, "cloudfabric")
    assert {"gini", _svc(v1.name), _svc(v2.name)} <= fab
    assert f"{_svc(v1.name)}_egress" not in fab           # agent rides the fabric, not egress


def test_no_vpc_means_unchanged_flat_compose():
    t = Topology("cloud")
    t.add_device("database"); t.add_device("cache")
    head = _compose(RuntimeCompiler().compile(t)).split("services:", 1)[0]
    assert head.count("driver: bridge") == 1             # only the gini bridge


def test_duplicate_default_cidrs_are_made_unique():
    t = Topology("cloud")
    v1 = t.add_device("vpc"); v2 = t.add_device("vpc")
    t.add_device("database", parent_id=v1.id)
    t.add_device("cache", parent_id=v2.id)
    cidrs = [n.cidr for n in RuntimeCompiler().compile(t).networks if n.internal]
    assert len(cidrs) == len(set(cidrs))                 # distinct VPC subnets
