"""Network HUD model: OVS switches as TRANSIT nodes on an L3 hop.

The design point being pinned here: an L2 switch never chooses the L3 next hop. A router
does, addressing the frame to the next router's MAC; the switch only carries it. So an OVS
must NOT be traced as a routing decision -- it splits a hop (R1--OVS1--R2) and its flow
table answers only "is this destination programmed here yet?".

Pure/deterministic over fake CLI text, so it needs no Docker and no Qt.
"""
from gini.domain.flowtable import FlowEntry
from gini.domain.routing_model import (
    KIND_OVS, KIND_ROUTER, OvsNode, RoutingModel, RouterNode, assemble_model,
    forwarding_tree, model_signature, ovs_egress_port,
)
from gini.domain.routetable import parse_routes


# --- fixtures ---------------------------------------------------------------------------- #
# R1 --- OVS1 --- R2 : two routers sharing an SDN segment (the transit case from the
# 4-subnet hybrid topology, where an OVS carries router-to-router traffic).
# Real `route show` shape (routetable.c printRouteTable): [idx] net mask nexthop iface [origin]
R1_ROUTES = """\
Index   Network         Netmask         Nexthop         Interface
[0]     10.0.1.0        255.255.255.0   0.0.0.0         tun1    C
[1]     10.0.2.0        255.255.255.0   10.0.1.2        tun1    S
"""
R2_ROUTES = """\
Index   Network         Netmask         Nexthop         Interface
[0]     10.0.1.0        255.255.255.0   0.0.0.0         tun1    C
[1]     10.0.2.0        255.255.255.0   0.0.0.0         tun2    C
"""
R1_IFACE = "eth0 10.0.1.1/24\n"
R2_IFACE = "eth0 10.0.1.2/24\neth1 10.0.2.1/24\n"

R2_MAC = "02:00:00:01:01:02"


def _flow(mac=None, action="output:3", prio=100, packets=0):
    match = {} if mac is None else {"Ethernet destination MAC address": mac}
    return FlowEntry(index=0, match=match, actions=[action], priority=prio, packets=packets)


def _build(flows, mac_of=None):
    m = assemble_model(
        [("r1", "R1", R1_ROUTES, R1_IFACE), ("r2", "R2", R2_ROUTES, R2_IFACE)],
        links=[("r1", "ovs1"), ("ovs1", "r2")],
        ovs_infos=[("ovs1", "OVS1", "", {3: "r2", 2: "r1"}, "ofc1")],
        mac_of=mac_of if mac_of is not None else {"r2": R2_MAC},
    )
    m.ovs["ovs1"].flows = flows
    return m


# --- the switch is a transit node, not a routing decision --------------------------------- #
def test_ovs_splits_the_hop_instead_of_being_a_next_hop():
    m = _build([_flow(R2_MAC)])
    # The L3 trace still reaches R2 -- the switch does not become a routing decision.
    tr = forwarding_tree(m, "r1")
    assert tr.per_dest["r2"].status == "ok"
    assert tr.per_dest["r2"].path == ["r1", "r2"], "L3 path must stay router-to-router"
    # ...but the LIT edges go through the switch, so the HUD draws it as a transit node.
    assert ("r1", "ovs1") in tr.edges_used
    assert ("ovs1", "r2") in tr.edges_used
    assert ("r1", "r2") not in tr.edges_used


def test_expand_hop_is_a_noop_without_an_ovs():
    m = assemble_model([("r1", "R1", R1_ROUTES, R1_IFACE),
                        ("r2", "R2", R2_ROUTES, R2_IFACE)])
    assert m.expand_hop("r1", "r2") == [("r1", "r2")]
    assert m.via("r1", "r2") is None
    assert m.hop_carried("r1", "r2") is None      # no switch on the hop: nothing to say


def test_router_only_model_is_unchanged():
    """The router path must behave exactly as before when no OVS data is supplied."""
    m = assemble_model([("r1", "R1", R1_ROUTES, R1_IFACE),
                        ("r2", "R2", R2_ROUTES, R2_IFACE)])
    tr = forwarding_tree(m, "r1")
    assert tr.per_dest["r2"].status == "ok"
    assert ("r1", "r2") in tr.edges_used
    assert m.ovs == {}
    assert m.nodes.keys() == m.routers.keys()


# --- "programmed" vs "not learned yet" ----------------------------------------------------- #
def test_hop_carried_true_when_the_destination_mac_is_programmed():
    assert _build([_flow(R2_MAC)]).hop_carried("r1", "r2") is True


def test_hop_carried_false_when_nothing_is_programmed_yet():
    assert _build([]).hop_carried("r1", "r2") is False


def test_a_wildcard_rule_does_not_count_as_programmed():
    """The boot-time match-all -> NORMAL default forwards the frame but says nothing about
    THIS destination. Counting it would invent precision the switch does not have."""
    m = _build([_flow(mac=None, action="normal", prio=1)])
    assert m.hop_carried("r1", "r2") is False


def test_flood_and_controller_are_not_a_definite_egress_port():
    assert ovs_egress_port([_flow(R2_MAC, action="flood")], R2_MAC) is None
    assert ovs_egress_port([_flow(R2_MAC, action="controller")], R2_MAC) is None


def test_highest_priority_rule_wins():
    flows = [_flow(R2_MAC, action="output:2", prio=10),
             _flow(R2_MAC, action="output:3", prio=200)]
    assert ovs_egress_port(flows, R2_MAC) == 3


def test_mac_match_is_case_insensitive():
    assert ovs_egress_port([_flow(R2_MAC.upper())], R2_MAC.lower()) == 3


# --- the signature trap: telemetry must not look like convergence -------------------------- #
def test_counters_do_not_change_the_signature():
    """A flow table carries packet/byte counters that move on EVERY poll. If they fed the
    signature, RouteHistory would record a snapshot each refresh and the scrub timeline
    would fill with noise until it was useless."""
    a = _build([_flow(R2_MAC, packets=0)])
    b = _build([_flow(R2_MAC, packets=99_999)])
    assert model_signature(a) == model_signature(b)


def test_a_real_forwarding_change_does_change_the_signature():
    a = _build([_flow(R2_MAC, action="output:3")])
    b = _build([_flow(R2_MAC, action="output:2")])
    assert model_signature(a) != model_signature(b)


def test_learning_a_destination_changes_the_signature():
    assert model_signature(_build([])) != model_signature(_build([_flow(R2_MAC)]))


# --- node kinds -------------------------------------------------------------------------- #
def test_nodes_carry_their_kind_for_the_renderer():
    m = _build([_flow(R2_MAC)])
    assert m.routers["r1"].kind == KIND_ROUTER
    assert m.ovs["ovs1"].kind == KIND_OVS
    assert set(m.nodes) == {"r1", "r2", "ovs1"}
    assert m.ovs["ovs1"].controller == "ofc1", "OFC association drives the dashed overlay"


def test_forwarding_tree_never_targets_a_switch():
    """A switch is never a destination -- forwarding_tree iterates routers only."""
    m = _build([_flow(R2_MAC)])
    assert set(forwarding_tree(m, "r1").per_dest) == {"r2"}
