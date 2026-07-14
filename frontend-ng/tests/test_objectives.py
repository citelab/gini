"""The objective predicate engine: safe evaluation of structural predicates over a real
Topology, behavioral objectives held pending (Phase 1), and validation of bad predicates."""
import pytest

from gini.domain import objectives as O
from gini.domain.topology import Topology


def _lan():
    t = Topology()
    m1 = t.add_device("host", "M1"); m2 = t.add_device("host", "M2")
    s1 = t.add_device("switch", "S1"); r1 = t.add_device("router", "R1")
    t.add_link(m1.id, s1.id); t.add_link(m2.id, s1.id); t.add_link(s1.id, r1.id)
    return t


def test_structural_predicates():
    w = O.TopologyWorld(_lan())
    assert O.evaluate_check("exists(switch)", w)
    assert O.evaluate_check("count(host) >= 2", w)
    assert not O.evaluate_check("count(host) >= 3", w)
    assert O.evaluate_check("connected(M1, R1)", w)      # path via S1
    assert not O.evaluate_check("linked(M1, R1)", w)     # not a direct link
    assert O.evaluate_check("linked(M1, S1)", w)


def test_boolean_and_comparison():
    w = O.TopologyWorld(_lan())
    assert O.evaluate_check("exists(router) and not exists(firewall)", w)
    assert O.evaluate_check("exists(hub) or exists(switch)", w)


def test_containment_and_property():
    t = _lan()
    vpc = t.add_device("vpc", "VPC1")
    db = t.add_device("database", "DB1", parent_id=vpc.id)
    w = O.TopologyWorld(t)
    assert O.evaluate_check("contains(VPC1, DB1)", w)
    assert not O.evaluate_check("contains(VPC1, M1)", w)
    assert O.evaluate_check("property(DB1, Name) == 'DB1'", w)


def test_type_based_predicates_are_name_agnostic():
    # the missions bug: objectives must match what's built regardless of device NAMES
    t = Topology()
    h1 = t.add_device("host", "whatever1"); h2 = t.add_device("host", "whatever2")
    sw = t.add_device("switch", "sw"); r = t.add_device("router", "gw")
    t.add_link(h1.id, sw.id); t.add_link(h2.id, sw.id); t.add_link(sw.id, r.id)
    w = O.TopologyWorld(t)
    assert O.evaluate_check("link(host, switch)", w)          # a host wired to a switch
    assert O.evaluate_check("link(switch, router)", w)        # switch wired to router
    assert not O.evaluate_check("link(host, router)", w)      # no direct host↔router link
    assert O.evaluate_check("path(host, router)", w)          # but a PATH exists (via switch)
    assert not O.evaluate_check("link(host, firewall)", w)


def test_through_is_a_chokepoint_not_direct_adjacency():
    # host → switch → router → firewall → internet: the firewall is INDIRECT from the host, but it
    # IS on the only path to the Internet. `link` fails (correctly), `path`/`through` succeed.
    t = Topology()
    h = t.add_device("host", "H"); sw = t.add_device("switch", "S")
    r = t.add_device("router", "R"); fw = t.add_device("firewall", "FW")
    net = t.add_device("cloud", "NET")
    t.add_link(h.id, sw.id); t.add_link(sw.id, r.id); t.add_link(r.id, fw.id); t.add_link(fw.id, net.id)
    w = O.TopologyWorld(t)
    assert not O.evaluate_check("link(host, firewall)", w)        # not directly wired…
    assert O.evaluate_check("path(host, firewall)", w)            # …but reachable
    assert O.evaluate_check("through(firewall, host, cloud)", w)  # firewall is the chokepoint


def test_through_rejects_a_bypass():
    # add a second path host → router → internet that skips the firewall: no longer a chokepoint
    t = Topology()
    h = t.add_device("host", "H"); r = t.add_device("router", "R")
    fw = t.add_device("firewall", "FW"); net = t.add_device("cloud", "NET")
    t.add_link(h.id, r.id); t.add_link(r.id, fw.id); t.add_link(fw.id, net.id)
    t.add_link(r.id, net.id)                                       # the bypass
    w = O.TopologyWorld(t)
    assert O.evaluate_check("path(host, firewall)", w)
    assert not O.evaluate_check("through(firewall, host, cloud)", w)   # bypass defeats the chokepoint


def test_contains_type_is_name_agnostic():
    t = Topology()
    vpc = t.add_device("vpc", "anyvpc")
    t.add_device("database", "mydb", parent_id=vpc.id)
    w = O.TopologyWorld(t)
    assert O.evaluate_check("contains_type(vpc, database)", w)
    assert not O.evaluate_check("contains_type(vpc, web_app)", w)
    assert O.evaluate_check("not path(cloud, database)", w)   # no internet → db not reachable from it


def test_behavioral_is_pending():
    w = O.TopologyWorld(Topology())
    o = O.Objective("reach", "reach", "behavioral", probe="reach(a -> b) == ok")
    assert O.evaluate(o, w).status == O.PENDING


def test_missing_devices_are_false_not_error():
    w = O.TopologyWorld(Topology())
    assert not O.evaluate_check("connected(X, Y)", w)
    assert not O.evaluate_check("contains(A, B)", w)


def test_predicate_safety_and_validation():
    # unknown function / arbitrary code must not evaluate
    assert not O.check_ok("__import__('os').system('rm -rf /')")
    assert not O.check_ok("hack(1)")
    with pytest.raises(O.PredicateError):
        O.parse_check("open('x')")
    # unknown element types are reported for validation
    assert O.unknown_element_types("exists(frobnicator)") == ["frobnicator"]
    assert O.unknown_element_types("exists(router) and count(host) >= 1") == []
