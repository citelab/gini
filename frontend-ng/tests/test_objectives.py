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
