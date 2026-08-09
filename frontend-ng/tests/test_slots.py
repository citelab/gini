"""Phase 1 — the slot + role-lattice data model.

Slots are the composition non-terminals of the graph grammar: named, typed dependency sockets that
predicates reference as `type@slot`. This covers the three pieces: the role lattice (so a router
provider satisfies a `network` slot → recursion), the slot data model (YAML round-trip), and
`@slot` scoping in the structural + probe grammar.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.domain import authoring as AU
from gini.domain import capabilities as C
from gini.domain import fragment_yaml as FY
from gini.domain import objectives as O
from gini.domain import probes as P
from gini.domain.topology import Topology


# 1. role lattice — recursion hinge
def test_role_lattice_makes_a_router_satisfy_a_network_slot():
    assert C.satisfies("switched-segment", "network")     # a LAN is a network
    assert C.satisfies("router-gateway", "network")        # a router's routed net is a network too
    assert C.satisfies("router-gateway", "routed-network")
    assert "network" in C.ancestors("router-gateway")      # so `network` slots accept routers → nest


# 2. slot data model — survives YAML
def test_slot_survives_the_yaml_round_trip():
    d = AU.build_fragment_dict(
        frag_id="router", teaches="net", summary="route", spirit="sp",
        objectives=[{"id": "r", "say": "router", "check": "exists(router)", "level": 1}],
        slots=[{"name": "A", "role": "network", "min": 1, "max": 1, "distinct": True},
               {"name": "B", "role": "network"}])
    frag = FY.fragment_from_dict(d)
    assert len(frag.slots) == 2
    assert frag.slots[0].name == "A" and frag.slots[0].role == "network"
    assert frag.slots[1].name == "B"
    assert "slots:" in FY.to_yaml(frag)


# 3. @slot in structural predicates
def test_slot_scoped_structural_predicates():
    t = Topology()
    ha = t.add_device("host", "HA"); sa = t.add_device("switch", "SA")
    ha.slot = sa.slot = "A"; t.add_link(ha.id, sa.id)      # LAN A
    hb = t.add_device("host", "HB"); sb = t.add_device("switch", "SB")
    hb.slot = sb.slot = "B"; t.add_link(hb.id, sb.id)      # LAN B
    r = t.add_device("router", "R1")                       # the delta
    t.add_link(r.id, sa.id); t.add_link(r.id, sb.id)       # wired to both switches
    w = O.TopologyWorld(t)

    assert O.evaluate_check("count(host@A) >= 1", w) and O.evaluate_check("count(host@B) >= 1", w)
    assert O.evaluate_check("count(host@A) >= 2", w) is False     # scoped, not the 2 hosts total
    assert O.evaluate_check("count(host@C) >= 1", w) is False     # no such slot
    assert O.evaluate_check("link(router, switch@A)", w)          # router wired to A's switch...
    assert O.evaluate_check("link(router, switch@B)", w)          # ...and B's
    assert O.evaluate_check("count(host) >= 2", w)                # unscoped still counts all


# 4. @slot in the probe grammar (the cross-slot reach that proves routing)
def test_slot_scoped_reach_probe():
    assert P.probe_ok("reach(host@A -> host@B) == ok")
    t = Topology()
    ha = t.add_device("host", "HA"); ha.slot = "A"
    hb = t.add_device("host", "HB"); hb.slot = "B"
    reachable = P.TypeRunner(P.FakeRunner({("reach", "HA", "HB", None): True}), lambda: t)
    assert P.evaluate("reach(host@A -> host@B) == ok", reachable) is True
    blocked = P.TypeRunner(P.FakeRunner({}), lambda: t)   # default: not reachable
    assert P.evaluate("reach(host@A -> host@B) == ok", blocked) is False
