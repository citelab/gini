"""Link properties in the domain: a cost and a failure model (docs/design/link-properties.md).

Phase 1 gives a link `props` and the rules for them, in one place (domain/link_props.py), that the
inspector, the AI tools and the compiler all go through. These pin the rules -- cost is an
abstract whole number 1-15, failure times are seconds -- and the promise that a link nobody
configured saves exactly as it did before link properties existed.
"""
import json

import pytest

from gini.domain import link_props as LP
from gini.domain.topology import Link, Topology


def _lab():
    t = Topology("lab")
    r1, r2 = t.add_device("router", "R1"), t.add_device("router", "R2")
    s, m = t.add_device("switch", "S1"), t.add_device("host", "M1")
    rr = t.add_link(r1.id, r2.id)
    rs = t.add_link(r2.id, s.id)
    sm = t.add_link(m.id, s.id)
    return t, rr, rs, sm


# ---- the rules ---------------------------------------------------------------- #
def test_defaults_are_todays_behaviour():
    assert LP.DEFAULTS == {"cost": 1, "fail_after": 0.0, "repair_after": 0.0}
    assert LP.values(Link("l", "a", "b")) == LP.DEFAULTS


@pytest.mark.parametrize("value, expect", [(1, 1), (15, 15), ("7", 7), (4.0, 4)])
def test_cost_is_a_whole_number_from_1_to_15(value, expect):
    assert LP.normalize("cost", value) == expect


@pytest.mark.parametrize("bad", [0, 16, 2.5, -1, "abc", None, "16"])
def test_a_cost_outside_the_rules_is_refused_in_words(bad):
    with pytest.raises(ValueError, match="1 to 15"):
        LP.normalize("cost", bad)


def test_failure_times_are_seconds_from_zero_to_a_day():
    assert LP.normalize("fail_after", 120) == 120.0
    assert LP.normalize("repair_after", "30") == 30.0
    for bad in (-1, 86401, "soon"):
        with pytest.raises(ValueError):
            LP.normalize("fail_after", bad)


def test_an_unknown_property_is_named_in_the_error():
    with pytest.raises(ValueError, match="delay"):
        LP.normalize("delay", 50)          # delay lives on the router, not the link


def test_only_chosen_values_are_stored():
    link = Link("l", "a", "b")
    LP.set_prop(link, "cost", 4)
    assert link.props == {"cost": 4}
    LP.set_prop(link, "cost", 1)                 # back to the default: removed, not stored
    assert link.props == {}


def test_clean_keeps_valid_values_and_drops_the_rest():
    assert LP.clean({"cost": 99, "fail_after": "60", "repair_after": 0, "colour": "red"}) == \
        {"fail_after": 60.0}


def test_cost_only_means_something_next_to_a_router():
    t, rr, rs, sm = _lab()
    assert LP.is_costed(t, rr) and LP.is_costed(t, rs)
    assert not LP.is_costed(t, sm)               # host to switch: no router, no routing cost
    ping = t.add_device("ping_probe")
    att = t.add_attach(ping.id, next(d.id for d in t.devices.values() if d.name == "M1"))
    assert not LP.is_costed(t, att) and not LP.can_fail(att)
    assert LP.can_fail(sm)                       # but any cable can fail


def test_the_router_types_match_the_compiler():
    from gini.services.compiler import ROUTERS
    assert set(LP.ROUTER_TYPES) == set(ROUTERS)


def test_a_topology_is_weighted_once_any_router_link_costs_more_than_1():
    t, rr, rs, sm = _lab()
    assert not LP.weighted(t)
    sm.props = {"cost": 5}                       # not a router link: does not count
    assert not LP.weighted(t)
    LP.set_prop(rr, "cost", 3)
    assert LP.weighted(t)


def test_describe_says_both_in_plain_words():
    link = Link("l", "a", "b")
    assert LP.describe(link) == "cost 1 · never fails"
    link.props = {"cost": 4, "fail_after": 120.0, "repair_after": 30.0}
    assert LP.describe(link) == "cost 4 · fails after ≈ 120 s, repaired after ≈ 30 s"
    link.props = {"fail_after": 60.0}
    assert "stays down until restored" in LP.describe(link)


# ---- saving ------------------------------------------------------------------- #
def test_an_unconfigured_link_saves_exactly_as_before():
    """No "props" key at all: plain labs keep their proof hashes and still open in GINI from
    before Phase 0, whose loader rejects any key it does not know."""
    t, *_ = _lab()
    for rec in t.to_dict()["links"]:
        assert list(rec) == ["id", "source_id", "target_id", "label", "kind"]


def test_a_configured_link_saves_and_loads_its_properties():
    t, rr, *_ = _lab()
    LP.set_prop(rr, "cost", 4)
    LP.set_prop(rr, "fail_after", 120)
    d = json.loads(json.dumps(t.to_dict()))
    back = Topology.from_dict(d).links[rr.id]
    assert back.props == {"cost": 4, "fail_after": 120.0}


def test_a_bad_value_in_a_file_is_dropped_on_load():
    t, rr, *_ = _lab()
    d = t.to_dict()
    d["links"][0]["props"] = {"cost": 99, "fail_after": 30}
    assert Topology.from_dict(d).links[rr.id].props == {"fail_after": 30.0}


def test_copying_a_link_copies_its_properties_not_a_shared_dict():
    from gini.domain.topology import apply_link_attributes, link_attributes
    t, rr, *_ = _lab()
    LP.set_prop(rr, "cost", 6)
    other = Link("x", "a", "b")
    apply_link_attributes(other, link_attributes(rr))
    assert other.props == {"cost": 6}
    LP.set_prop(other, "cost", 2)
    assert rr.props == {"cost": 6}               # editing the copy leaves the original alone
