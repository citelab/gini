"""Phase 3 — slot-aware composition: the deterministic assembler scales primitives into larger
topologies and the oracle grades them. This is the "generative GINI" keystone: a hand-built,
certified LAN primitive + a hub router with a CARDINAL slot compose into an N-LAN routed network,
materialized concretely and graded — no LLM in the loop.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

import pytest

from gini.domain import compose as X
from gini.domain import fragments as F
from gini.domain import probes as P
from gini.domain.fragments import Fragment, ObjectiveTemplate as OT, Slot

LAN_STAGE = {"devices": [{"id": "h1", "type_key": "host"}, {"id": "h2", "type_key": "host"},
                         {"id": "s1", "type_key": "switch"}],
             "links": [{"source_id": "h1", "target_id": "s1"},
                       {"source_id": "h2", "target_id": "s1"}]}


@pytest.fixture(autouse=True)
def _register():
    cap = Fragment(id="cap-lan", layer="core", teaches="lan", summary="2 hosts + switch",
                   provides=("l2-fabric",), certified=True, stage=LAN_STAGE,
                   objectives=(OT(id="h", say="2 hosts", check="count(host) >= 2", level=1),
                               OT(id="s", say="switch", check="exists(switch)", level=1),
                               OT(id="l", say="wire", check="link(host, switch)", level=2)))
    hub = Fragment(id="hub-router", layer="core", teaches="routing", summary="route N LANs",
                   requires=("network",),
                   slots=(Slot(name="lans", role="network", min=2, max=0, distinct=True),),
                   objectives=(OT(id="r", say="place a router", check="exists(router)", level=1),
                               OT(id="wire", say="router wires each LAN",
                                  check="link(router, switch@lans)", level=2),
                               OT(id="reach", say="every LAN reaches every other", kind="behavioral",
                                  probe="reach(host@lans -> host@lans) == ok", level=4)))
    F.FRAGMENTS["cap-lan"], F.FRAGMENTS["hub-router"] = cap, hub
    yield
    F.FRAGMENTS.pop("cap-lan", None)
    F.FRAGMENTS.pop("hub-router", None)


def _binding(n):
    return {"fragment": "hub-router", "bind": {"lans": ["cap-lan"] * n}}


@pytest.mark.parametrize("n", [2, 3, 4, 6])
def test_router_scales_to_n_lans_and_grades_met(n):
    topo, results = X.grade(_binding(n), runner=P.FakeRunner(default=True))
    types = [d.type_key for d in topo.devices.values()]
    assert types.count("host") == 2 * n           # each LAN faithfully reproduced
    assert types.count("switch") == n
    assert types.count("router") == 1             # one shared router (the delta)
    rsw = sum(1 for l in topo.links.values()
              if {topo.devices[l.source_id].type_key,
                  topo.devices[l.target_id].type_key} == {"router", "switch"})
    assert rsw == n                               # router wired to EVERY LAN
    assert all(r.status == "met" for r in results), [(r.say, r.status) for r in results]


def test_cross_lan_reach_expands_to_all_pairs():
    # the behavioral reach(host@lans -> host@lans) becomes C(n,2) member-pair probes
    _, results = X.grade(_binding(4), runner=P.FakeRunner(default=True))
    behavioral = [r for r in results if r.kind == "behavioral"]
    assert len(behavioral) == 6                   # C(4,2)


def test_each_lan_is_distinct_and_slot_tagged():
    topo, _ = X.materialize(_binding(3))
    switch_slots = sorted(d.slot for d in topo.devices.values() if d.type_key == "switch")
    assert switch_slots == ["root_lans0", "root_lans1", "root_lans2"]   # 3 distinct LAN partitions
    host_slots = {d.slot for d in topo.devices.values() if d.type_key == "host"}
    assert host_slots == {"root_lans0", "root_lans1", "root_lans2"}     # each host in one LAN


def test_a_broken_lan_fails_the_composition():
    # if one bound LAN can't route (its reach fails), the composed grade is not all-met
    topo, objs = X.materialize(_binding(3))
    # a runner where reach never succeeds
    _, results = X.grade(_binding(3), runner=P.FakeRunner(default=False))
    assert any(r.kind == "behavioral" and r.status == "unmet" for r in results)


def test_cardinality_bounds_are_enforced():
    with pytest.raises(X.CompositionError):
        X.materialize({"fragment": "hub-router", "bind": {"lans": ["cap-lan"]}})  # min is 2


# ---- Open-N: quantified, N-independent win conditions ---------------------- #
from gini.domain import objectives as O  # noqa: E402
from gini.domain.topology import Topology  # noqa: E402


def _student_hub(n):
    """A hub a student built independently: 1 router, n LANs of 2 hosts each, all wired."""
    t = Topology()
    r = t.add_device("router")
    for _ in range(n):
        sw = t.add_device("switch"); t.add_link(r.id, sw.id)
        for _ in range(2):
            h = t.add_device("host"); t.add_link(h.id, sw.id)
    return t


def test_open_win_condition_is_n_independent():
    _, objs = X.materialize(_binding(2), mode="open")     # authored floor = 2
    checks = [o.check for o in objs if o.check]
    assert "count(switch) >= 2" in checks
    assert "all_linked(switch, router)" in checks
    assert any(o.kind == "behavioral" and "all" in o.probe for o in objs)
    # the SAME condition passes at any N the student picks
    for n in (2, 3, 5, 7):
        t = _student_hub(n)
        res = O.evaluate_all(objs, O.TopologyWorld(t),
                             P.TypeRunner(P.FakeRunner(default=True), lambda t=t: t))
        assert all(r.status == "met" for r in res), (n, [(r.say, r.status) for r in res])


def test_open_grade_rejects_an_orphan_member():
    _, objs = X.materialize(_binding(2), mode="open")
    t = _student_hub(3)
    t.add_device("switch")                                # a 4th LAN switch NOT wired to the router
    res = O.evaluate_all(objs, O.TopologyWorld(t),
                         P.TypeRunner(P.FakeRunner(default=True), lambda: t))
    assert any(r.status != "met" for r in res)            # all_linked catches the orphan


def test_open_grade_enforces_the_floor():
    _, objs = X.materialize(_binding(2), mode="open")
    t = _student_hub(1)                                   # only one LAN — below floor of 2
    res = O.evaluate_all(objs, O.TopologyWorld(t),
                         P.TypeRunner(P.FakeRunner(default=True), lambda: t))
    assert any(r.status != "met" for r in res)            # count(switch) >= 2 fails


def test_composition_drops_a_providers_own_behavioral_checks():
    # a certified LAN with an OUTPUT CHECK (a rider measurement) — trusted by its cert, not re-graded
    F.FRAGMENTS["cap-lan-pcap"] = Fragment(
        id="cap-lan-pcap", layer="core", provides=("l2-fabric",), certified=True, stage=LAN_STAGE,
        objectives=(OT(id="h", say="2 hosts", check="count(host) >= 2", level=1),
                    OT(id="s", say="switch", check="exists(switch)", level=1),
                    OT(id="l", say="wire", check="link(host, switch)", level=2),
                    OT(id="out", say="pcap sees packets", kind="behavioral",
                       probe="measure(packet_view, packets) >= 1", level=4)))
    try:
        binding = {"fragment": "hub-router", "bind": {"lans": ["cap-lan-pcap"] * 3}}
        _, objs = X.materialize(binding, mode="fixed")
        probes = [o.probe for o in objs if o.probe]
        assert not any("packet_view" in p for p in probes)     # the provider's measure is gone
        assert any("reach" in p for p in probes)               # the composition's own reach stays
        # and it grades clean (no unsatisfiable measure) on a live-ish runner
        _, results = X.grade(binding, runner=P.FakeRunner(default=True), mode="fixed")
        assert all(r.status == "met" for r in results)
    finally:
        F.FRAGMENTS.pop("cap-lan-pcap", None)


def test_fixed_mode_still_bakes_a_concrete_instance():
    _, fixed = X.materialize(_binding(4), mode="fixed")
    # fixed mode names concrete labels; open mode never does
    assert any("@root_lans3" in (o.check or "") for o in fixed)
    _, opn = X.materialize(_binding(4), mode="open")
    assert not any("root_lans" in (o.check or "") for o in opn)


# ---- recursion: multi-level networks (routers of routers) ------------------ #
@pytest.fixture
def _campus(_register):
    # a campus core that routes between N routed-networks (each is itself a hub-router of LANs)
    campus = Fragment(id="campus", layer="core", teaches="hierarchy", summary="core over pods",
                      requires=("routed-network",),
                      slots=(Slot(name="pods", role="routed-network", min=2, max=0),),
                      objectives=(OT(id="c", say="place core router", check="exists(router)", level=1),
                                  OT(id="uplink", say="core wires each pod",
                                     check="link(router, router@pods)", level=2),
                                  OT(id="x", say="every pod reaches every other", kind="behavioral",
                                     probe="reach(host@pods -> host@pods) == ok", level=4)))
    F.FRAGMENTS["campus"] = campus
    yield
    F.FRAGMENTS.pop("campus", None)


def test_two_level_campus_materializes_and_grades(_campus):
    # 2 pods, each a hub-router over 2 LANs → a 2-level hierarchy, built from ONE leaf primitive
    pod = {"fragment": "hub-router", "bind": {"lans": ["cap-lan", "cap-lan"]}}
    binding = {"fragment": "campus", "bind": {"pods": [pod, pod]}}
    topo, results = X.grade(binding, runner=P.FakeRunner(default=True))
    t = [d.type_key for d in topo.devices.values()]
    assert t.count("router") == 3           # 1 core + 2 pod routers
    assert t.count("switch") == 4           # 2 pods × 2 LANs
    assert t.count("host") == 8             # 4 LANs × 2 hosts
    # the core links to BOTH pod routers (router↔router), no bleed into pod↔pod internal links
    core = next(d for d in topo.devices.values() if d.slot == "root_self")
    assert topo.degree(core.id) == 2
    assert all(r.status == "met" for r in results), [(r.say, r.status) for r in results
                                                     if r.status != "met"]


def test_depth_budget_stops_pathological_nesting(_campus):
    deep = {"fragment": "campus", "bind": {"pods": []}}
    for _ in range(X.MAX_DEPTH + 2):
        deep = {"fragment": "campus", "bind": {"pods": [deep, deep]}}
    with pytest.raises(X.CompositionError):
        X.materialize(deep)


# ---- Phase 5: lateral peering (meshes/graphs of routers) ------------------- #
from gini.domain.fragments import Peering  # noqa: E402


@pytest.fixture
def _mesh(_register):
    m = Fragment(id="mesh", layer="core", teaches="mesh", summary="peer sites",
                 peerings=(Peering(name="sites", role="routed-network", min=2, max=0,
                                   topology="mesh"),),
                 objectives=(OT(id="x", say="every site reaches every other", kind="behavioral",
                                probe="reach(host@sites -> host@sites) == ok", level=4),))
    F.FRAGMENTS["mesh"] = m
    yield
    F.FRAGMENTS.pop("mesh", None)


def _site():
    return {"fragment": "hub-router", "bind": {"lans": ["cap-lan", "cap-lan"]}}


def test_mesh_of_routers_interconnects_and_grades(_mesh):
    binding = {"fragment": "mesh", "peer": {"sites": [_site(), _site(), _site()]}}
    topo, results = X.grade(binding, runner=P.FakeRunner(default=True))
    t = [d.type_key for d in topo.devices.values()]
    assert t.count("router") == 3 and t.count("switch") == 6 and t.count("host") == 12
    # a full mesh of 3 site-routers = C(3,2)=3 lateral router↔router links
    rr = sum(1 for l in topo.links.values()
             if topo.devices[l.source_id].type_key == "router"
             and topo.devices[l.target_id].type_key == "router")
    assert rr == 3
    assert all(r.status == "met" for r in results), [(r.say, r.status) for r in results
                                                     if r.status != "met"]


@pytest.mark.parametrize("shape,n,edges", [
    ("mesh", 4, 6), ("ring", 4, 4), ("ring", 2, 1), ("line", 4, 3), ("star", 4, 3)])
def test_topology_pair_counts(shape, n, edges):
    assert len(X._topology_pairs(shape, n)) == edges


def test_open_mesh_of_composite_sites_omits_internal_wiring_check(_mesh):
    # sites are composite (routers over LANs) — Open-N must NOT assert "host/switch on a router"
    # (the site's internals are proven by its certificate); only member count + all-pairs reach.
    binding = {"fragment": "mesh", "peer": {"sites": [_site(), _site(), _site()]}}
    _, objs = X.materialize(binding, mode="open")
    checks = [o.check for o in objs if o.check]
    assert "count(router) >= 2" in checks or any(c.startswith("count(router) >=") for c in checks)
    assert not any("all_linked" in c for c in checks)      # no bogus internal-structure assertion
    _, results = X.grade(binding, runner=P.FakeRunner(default=True), mode="open")
    assert all(r.status == "met" for r in results)


def test_peering_round_trips_through_yaml():
    from gini.domain import fragment_yaml as FY
    m = F.FRAGMENTS.get("mesh") or Fragment(
        id="mesh", layer="core",
        peerings=(Peering(name="sites", role="routed-network", topology="ring", min=3),))
    back = FY.fragment_from_dict(FY.to_dict(m if m.peerings else m))
    assert back.peerings and back.peerings[0].topology in ("mesh", "ring")
    assert back.peerings[0].role == "routed-network"
