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
    collect_network_data, contract_edges, decision_kind, forwarding_tree,
    l2_reach, model_signature, ovs_egress_port, ovs_port_peers, parse_iface_macs, trace,
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
# The one subnet only R2 delivers; destinations are subnets, not routers.
R2_NET = "10.0.2.0/24"          # only R2 delivers this one
SHARED_NET = "10.0.1.0/24"      # the transit segment both routers sit on


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
    assert tr.per_dest[R2_NET].status == "ok"
    assert tr.per_dest[R2_NET].path == ["r1", "r2"], "L3 path must stay router-to-router"
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
    assert tr.per_dest[R2_NET].status == "ok"
    assert ("r1", "r2") in tr.edges_used
    assert m.ovs == {}
    assert m.nodes.keys() == m.routers.keys()


# --- "programmed" vs "not learned yet" ----------------------------------------------------- #
def test_hop_carried_true_when_the_destination_mac_is_programmed():
    assert _build([_flow(R2_MAC)]).hop_carried("r1", "r2") is True


def test_hop_carried_false_when_nothing_is_programmed_yet():
    assert _build([]).hop_carried("r1", "r2") is False


def test_a_multi_homed_router_is_carried_via_any_of_its_macs():
    """`mac_of` holds every MAC a node owns. Asking about one arbitrary interface would
    report 'not programmed' whenever we picked the one facing the other way."""
    other = "02:00:00:09:09:09"
    m = _build([_flow(R2_MAC)], mac_of={"r2": [other, R2_MAC]})
    assert m.hop_carried("r1", "r2") is True
    assert _build([_flow(R2_MAC)], mac_of={"r2": [other]}).hop_carried("r1", "r2") is False


def test_a_bare_string_mac_still_works():
    assert _build([_flow(R2_MAC)], mac_of={"r2": R2_MAC}).hop_carried("r1", "r2") is True


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


def test_a_switch_is_never_a_destination():
    """Destinations are SUBNETS. A switch owns no subnet, so it can never be one -- it is
    transit, and the only question asked of it is whether it carries the hop."""
    m = _build([_flow(R2_MAC)])
    dests = set(forwarding_tree(m, "r1").per_dest)
    assert dests == {SHARED_NET, R2_NET}, "both subnets, and only subnets"
    assert "ovs1" not in dests and "r2" not in dests


# --- L2 mode: a pure-SDN fabric ------------------------------------------------------------ #
# OVS1 -- OVS2 -- OVS5, with a host hanging off OVS5. The user's sdn-test topology shape:
# no routers at all, so the L3 trace has neither endpoints nor destinations.
MA = "02:00:00:0a:0a:0a"          # a host MAC living beyond OVS5

def _fabric(flows_by_ovs, peers=None):
    peers = peers or {
        "ovs1": {2: "ovs2"},
        "ovs2": {1: "ovs1", 3: "ovs5"},
        "ovs5": {2: "ovs2", 7: None},     # port 7 faces the host: leaves the fabric
    }
    m = assemble_model(
        [],
        links=[("ovs1", "ovs2"), ("ovs2", "ovs5")],
        ovs_infos=[(r, r.upper(), "", peers.get(r, {}), "ofc1")
                   for r in ("ovs1", "ovs2", "ovs5")],
    )
    for rid, fl in flows_by_ovs.items():
        m.ovs[rid].flows = fl
    return m


def test_l2_walks_switch_to_switch_by_destination_mac():
    m = _fabric({"ovs1": [_flow(MA, action="output:2")],
                 "ovs2": [_flow(MA, action="output:3")],
                 "ovs5": [_flow(MA, action="output:7")]})
    tr = l2_reach(m, "ovs1")
    assert tr.per_dest[MA].status == "ok"
    assert tr.per_dest[MA].path == ["ovs1", "ovs2", "ovs5"]
    assert tr.edges_used == {("ovs1", "ovs2"), ("ovs2", "ovs5")}


def test_l2_stops_quietly_where_the_fabric_is_not_programmed_yet():
    """OVS2 has no rule for this MAC. That is mid-learning, NOT a black hole -- it must not
    land in `deadends`, or the HUD would paint a fault the network does not have."""
    m = _fabric({"ovs1": [_flow(MA, action="output:2")], "ovs2": [], "ovs5": []})
    tr = l2_reach(m, "ovs1")
    assert tr.per_dest[MA].status == "unprogrammed"
    assert MA in tr.unprogrammed
    assert not tr.deadends
    assert tr.edges_used == {("ovs1", "ovs2")}, "light what IS programmed, then stop"


def test_l2_reports_a_programmed_loop():
    """Rules that point at each other. Exactly the failure a broken spanning tree leaves
    behind, and the one thing the HUD most needs to be able to show."""
    m = _fabric({"ovs1": [_flow(MA, action="output:2")],
                 "ovs2": [_flow(MA, action="output:1")],
                 "ovs5": []})
    tr = l2_reach(m, "ovs1")
    assert tr.per_dest[MA].status == "loop"
    assert MA in tr.loops
    # The cycle must be nameable, because it is the only place the failure can be SEEN: the
    # destination is a host MAC, and hosts are not drawn.
    assert tr.fault_edges == {("ovs1", "ovs2"), ("ovs2", "ovs1")}


def test_l2_egress_off_the_fabric_is_delivered():
    """A port with no switch behind it faces a host. Hosts are not drawn, so the walk ends
    there and the destination counts as reached."""
    m = _fabric({"ovs5": [_flow(MA, action="output:7")]})
    tr = l2_reach(m, "ovs5")
    assert tr.per_dest[MA].status == "ok"
    assert tr.per_dest[MA].hop_count == 0


def test_l2_destinations_come_from_the_whole_fabric_not_just_the_root():
    """The root may know nothing yet while its neighbours already do; the MAC still belongs
    on the list, reported honestly as unprogrammed at the root."""
    m = _fabric({"ovs1": [], "ovs2": [_flow(MA, action="output:3")], "ovs5": []})
    tr = l2_reach(m, "ovs1")
    assert set(tr.per_dest) == {MA}
    assert tr.per_dest[MA].status == "unprogrammed"


# --- port_peer is the piece that can lie confidently ---------------------------------------- #
def test_a_wrong_port_number_does_not_invent_a_hop():
    """port_peer is a fact about the DRAWN topology, not about any device's state, so a
    mis-numbered port is the likeliest way this HUD produces a plausible lie. An egress port
    the model has no peer for must end the walk, never guess a neighbour."""
    m = _fabric({"ovs1": [_flow(MA, action="output:9")]},      # port 9 is not in port_peer
                peers={"ovs1": {2: "ovs2"}, "ovs2": {}, "ovs5": {}})
    tr = l2_reach(m, "ovs1")
    assert tr.edges_used == set(), "no peer known for port 9 -- draw nothing"
    assert tr.per_dest[MA].path == ["ovs1"]
    # ...and say we do not know, rather than the two tempting wrong answers: that it was
    # delivered off the fabric, or that the switch is broken.
    assert tr.per_dest[MA].status == "unverified"
    assert MA in tr.unverified and not tr.deadends and not tr.unprogrammed


def test_a_port_facing_a_host_is_delivered_not_unverified():
    """The difference from the test above: this port IS mapped, to something that is not a
    switch. That is a real answer -- the frame left the fabric for a host."""
    m = _fabric({"ovs5": [_flow(MA, action="output:7")]})       # port 7 -> None (a host)
    assert l2_reach(m, "ovs5").per_dest[MA].status == "ok"


def test_a_port_facing_a_router_is_also_delivered():
    m = _fabric({"ovs1": [_flow(MA, action="output:5")]},
                peers={"ovs1": {5: "r1"}, "ovs2": {}, "ovs5": {}})
    assert l2_reach(m, "ovs1").per_dest[MA].status == "ok"


def test_l2_ignores_wildcard_and_flood_rules_like_l3_does():
    m = _fabric({"ovs1": [_flow(mac=None, action="normal", prio=1),
                          _flow(MA, action="flood")]})
    assert l2_reach(m, "ovs1").per_dest[MA].status == "unprogrammed"


# --- mode selection and edge colour --------------------------------------------------------- #
def test_the_mode_follows_the_root():
    m = _build([_flow(R2_MAC)])
    assert set(trace(m, "r1").per_dest) == {SHARED_NET, R2_NET}   # router root -> L3
    assert set(trace(m, "ovs1").per_dest) == {R2_MAC}        # switch root -> L2 (by MAC)


# --- OpenFlow port numbering: computed, then checked against the switch --------------------- #
# `ifconfig show verbose` (gnet.c:272): id, state/mode, device, ip, mac, mtu, sock, tid
OVS_IFCONFIG = """\
Int.	State/Mode	Device	IP address	MAC address		MTU	Socket Name	Thread ID
1	UN		tun1	169.254.0.1	02:00:fe:00:00:01	1500	/tmp/a	7
2	UN		tun2	169.254.0.2	02:00:fe:00:00:02	1500	/tmp/b	8
3	UN		tun3	169.254.0.3	02:00:fe:00:00:03	1500	/tmp/c	9
"""


def test_the_first_link_is_openflow_port_2_not_1():
    """tun0 is reserved for the tap, so the first COMPILED link is tun1 -> OF port 2. Off by
    one here and every L2 hop in the HUD points at the wrong neighbour."""
    assert ovs_port_peers(["ovs2", "ovs5"]) == {2: "ovs2", 3: "ovs5"}


def test_parse_iface_macs_reads_the_verbose_ifconfig():
    assert parse_iface_macs(OVS_IFCONFIG) == {1: "02:00:fe:00:00:01",
                                              2: "02:00:fe:00:00:02",
                                              3: "02:00:fe:00:00:03"}


def test_the_computed_mapping_is_confirmed_by_the_running_switch():
    assert ovs_port_peers(["ovs2", "ovs5"], OVS_IFCONFIG) == {2: "ovs2", 3: "ovs5"}


def test_a_port_the_switch_does_not_report_is_dropped():
    """Three links compiled but only two interfaces exist. The third is unaccounted for, so
    it is left out rather than drawn on faith."""
    two_ifaces = "\n".join(OVS_IFCONFIG.splitlines()[:3])       # header + tun1 + tun2
    assert ovs_port_peers(["ovs2", "ovs5", "ovs9"], two_ifaces) == {2: "ovs2", 3: "ovs5"}


def test_a_contradicting_mac_drops_the_port_rather_than_drawing_it():
    """The compiler stamps the port index into the MAC, so a mismatch means the compiled link
    order is not the order we just recomputed -- exactly the case that would otherwise render
    a confident lie. Drawing nothing is the honest outcome."""
    bad = OVS_IFCONFIG.replace("02:00:fe:00:00:02", "02:00:fe:00:00:07")
    assert ovs_port_peers(["ovs2", "ovs5"], bad) == {2: "ovs2"}, "port 3 must be dropped"


def test_a_foreign_mac_is_not_vouched_for():
    """Not a compiled ovs port at all (a hand-written gRouter config, say). We have no basis
    for the index arithmetic there, so we decline to map it."""
    foreign = OVS_IFCONFIG.replace("02:00:fe:00:00:02", "aa:bb:cc:dd:ee:02")
    assert 3 not in ovs_port_peers(["ovs2", "ovs5"], foreign)


def test_verification_is_skipped_when_no_ifconfig_is_supplied():
    assert ovs_port_peers(["ovs2"], None) == {2: "ovs2"}


# --- adjacency for a map that has switches on it -------------------------------------------- #
def test_a_classic_switch_collapses_into_the_edge():
    """Classic switches are deliberately off the map (self-learned forwarding, no decision to
    inspect), so the two routers either side of one are drawn as neighbours."""
    assert contract_edges({"r1", "r2"}, [("r1", "sw1"), ("sw1", "r2")], {"sw1"}) \
        == [("r1", "r2")]


def test_an_ovs_does_not_collapse_it_becomes_a_node():
    edges = contract_edges({"r1", "r2", "ovs1"},
                           [("r1", "ovs1"), ("ovs1", "r2")], passthrough=set())
    assert sorted(edges) == [("ovs1", "r1"), ("ovs1", "r2")]
    assert ("r1", "r2") not in edges, "drawing a direct link would deny the switch exists"


def test_the_walk_does_not_pass_through_a_host():
    """A machine is an endpoint, not a bridge. Walking through one would invent a link
    between two networks that the host merely sits on."""
    assert contract_edges({"r1", "r2"}, [("r1", "m1"), ("m1", "r2")], passthrough=set()) == []


def test_contraction_crosses_a_chain_of_switches():
    assert contract_edges({"r1", "r2"},
                          [("r1", "sw1"), ("sw1", "sw2"), ("sw2", "r2")],
                          {"sw1", "sw2"}) == [("r1", "r2")]


def test_each_edge_appears_once():
    edges = contract_edges({"r1", "r2"}, [("r1", "sw1"), ("sw1", "r2")], {"sw1"})
    assert len(edges) == 1, "both endpoints walk the segment; the edge must not double up"


# --- the live collector --------------------------------------------------------------------- #
def test_collect_network_data_builds_switches_with_verified_ports():
    replies = {("OVS1", "openflow entry all"): "", ("OVS1", "ifconfig show verbose"): OVS_IFCONFIG}

    def query(name, cmd):
        if name == "OVS1":
            return replies[(name, cmd)]
        return {"route show": R1_ROUTES, "ifconfig show": R1_IFACE}[cmd]

    m = collect_network_data(
        routers=[("r1", "R1")], switches=[("ovs1", "OVS1", "ofc1")],
        query=query, delay_prop=lambda rid, k: "", links=[("r1", "ovs1")],
        neighbours_of=lambda rid: ["r1", "r2"], mac_of={"r2": R2_MAC})
    assert m.ovs["ovs1"].port_peer == {2: "r1", 3: "r2"}
    assert m.ovs["ovs1"].controller == "ofc1"


def test_an_unreadable_switch_is_marked_stale_not_drawn_empty():
    """A switch we cannot read would otherwise appear with an empty flow table, which reads as
    'programmed for nothing' rather than 'not answering'.

    Note the fake RAISES here, which is the defensive path. The real `element_query` does NOT
    raise — it returns a sentinel string — and that is covered in test_network_hud_glue.py.
    Believing the exception was the only failure mode is what let this ship broken."""
    def query(name, cmd):
        if name == "OVS1":
            raise OSError("container not running")
        return {"route show": R1_ROUTES, "ifconfig show": R1_IFACE}[cmd]

    m = collect_network_data(routers=[("r1", "R1")], switches=[("ovs1", "OVS1", None)],
                             query=query, delay_prop=lambda rid, k: "", links=[])
    assert m.ovs["ovs1"].reachable is False
    assert m.ovs["ovs1"].flows == []


def test_edge_colour_follows_the_node_that_decided_it():
    """R1->OVS1 is R1's computed choice; OVS1->R2 is the flow rule's. Colouring the whole
    hop one way would hide that two different kinds of decision were involved."""
    m = _build([_flow(R2_MAC)])
    assert decision_kind(m, "r1") == "computed"
    assert decision_kind(m, "ovs1") == "programmed"
