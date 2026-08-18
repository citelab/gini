"""Authentic routing trace — converged tree, loop, dead-end, ECMP, latency, protocol-agnostic."""
from gini.domain.routetable import RouteEntry
from gini.domain.routing_model import (
    Edge, RouterNode, RoutingModel, assemble_model, collect_router_data, forwarding_tree,
    hop_latency, parse_delay_base, parse_iface_ips,
)


def _r(index, net, mask, nh, iface="tun0"):
    return RouteEntry(index, net, mask, nh, iface)


# A 3-router line: R1 —(5ms)— R2 —(10ms)— R3
#   segments: 10.0.1.0/24 (R1,R2), 10.0.2.0/24 (R2,R3)
def _line_model(r1_table=None, r3_reachable=True):
    R1 = RouterNode("R1", "R1", {"10.0.1.1"}, r1_table or [
        _r(0, "10.0.1.0", "255.255.255.0", "0.0.0.0"),                 # direct
        _r(1, "10.0.2.0", "255.255.255.0", "10.0.1.2"),                # via R2
    ])
    R2 = RouterNode("R2", "R2", {"10.0.1.2", "10.0.2.1"}, [
        _r(0, "10.0.1.0", "255.255.255.0", "0.0.0.0"),
        _r(1, "10.0.2.0", "255.255.255.0", "0.0.0.0"),
    ])
    R3 = RouterNode("R3", "R3", {"10.0.2.2"}, [
        _r(0, "10.0.2.0", "255.255.255.0", "0.0.0.0"),
    ])
    edges = [Edge("R1", "R2", 5), Edge("R2", "R3", 10)]
    return RoutingModel([R1, R2, R3], edges)


def test_converged_tree_and_latency():
    m = _line_model()
    t = forwarding_tree(m, "R1")
    p = t.per_dest["R3"]
    assert p.status == "ok" and p.path == ["R1", "R2", "R3"] and p.hop_count == 2
    assert p.total_latency == 15                          # 5 + 10, configured delay-VNF values
    assert ("R1", "R2") in t.edges_used and ("R2", "R3") in t.edges_used
    assert not t.loops and not t.deadends


def test_deadend_when_no_route():
    # R1 has no route toward R3's subnet (and no default) -> black hole
    m = _line_model(r1_table=[_r(0, "10.0.1.0", "255.255.255.0", "0.0.0.0")])
    t = forwarding_tree(m, "R1")
    assert t.per_dest["R3"].status == "deadend" and "R3" in t.deadends
    assert t.per_dest["R3"].path == []                   # R1 drops it — no path
    assert ("R2", "R3") not in t.edges_used              # nothing forwarded toward R3
    assert ("R1", "R2") in t.edges_used                  # R2 itself is still directly reachable


def test_forwarding_loop_is_detected():
    # R1 and R2 each point at the other for 10.0.9.9 -> a loop (RIP mid-convergence)
    R1 = RouterNode("R1", "R1", {"10.0.1.1"},
                    [_r(0, "10.0.9.0", "255.255.255.0", "10.0.1.2")])   # via R2
    R2 = RouterNode("R2", "R2", {"10.0.1.2"},
                    [_r(0, "10.0.9.0", "255.255.255.0", "10.0.1.1")])   # via R1
    phantom = RouterNode("R9", "R9", {"10.0.9.9"}, [])                  # the unreachable dest
    m = RoutingModel([R1, R2, phantom], [Edge("R1", "R2", 5)])
    t = forwarding_tree(m, "R1")
    assert t.per_dest["R9"].status == "loop" and "R9" in t.loops
    assert ("R1", "R2") in t.edges_used and ("R2", "R1") in t.edges_used   # the cycle is highlighted


def test_ecmp_becomes_a_dag():
    # R1 has two equal /24 routes to R4's subnet — via R2 and via R3 (both on 10.0.1.0/24)
    R1 = RouterNode("R1", "R1", {"10.0.1.1"}, [
        _r(0, "10.0.1.0", "255.255.255.0", "0.0.0.0"),
        _r(1, "10.0.4.0", "255.255.255.0", "10.0.1.2"),                # via R2
        _r(2, "10.0.4.0", "255.255.255.0", "10.0.1.3"),                # via R3  (equal cost)
    ])
    R2 = RouterNode("R2", "R2", {"10.0.1.2", "10.0.4.1"},
                    [_r(0, "10.0.4.0", "255.255.255.0", "0.0.0.0")])
    R3 = RouterNode("R3", "R3", {"10.0.1.3", "10.0.4.2"},
                    [_r(0, "10.0.4.0", "255.255.255.0", "0.0.0.0")])
    R4 = RouterNode("R4", "R4", {"10.0.4.9"}, [])
    m = RoutingModel([R1, R2, R3, R4], [Edge("R1", "R2", 3), Edge("R1", "R3", 8)],
                     dest_ip={"R4": "10.0.4.9"})
    t = forwarding_tree(m, "R4" if False else "R1")
    assert "R4" in t.ecmp
    assert ("R1", "R2") in t.edges_used and ("R1", "R3") in t.edges_used   # both parallels highlighted
    # the representative path takes the lower-latency next-hop (R2, 3ms < 8ms)
    assert t.per_dest["R4"].path[1] == "R2"


def test_parse_iface_ips_both_formats():
    sim = "  eth0  10.0.1.1/24  aa:bb:cc:dd:ee:ff\n  eth1  10.0.2.1/24  aa:bb:cc:dd:ee:00"
    assert parse_iface_ips(sim) == {"10.0.1.1", "10.0.2.1"}
    real = "tun0: inet 10.0.5.9 netmask 255.255.255.0\ntun1: inet 10.0.6.1 netmask 255.255.255.0"
    assert parse_iface_ips(real) == {"10.0.5.9", "10.0.6.1"}   # netmasks excluded
    assert parse_iface_ips("") == set()


def test_parse_delay_base_and_hop_latency():
    assert parse_delay_base("50 5 0.90") == 50 and parse_delay_base("12 ms") == 12
    assert parse_delay_base("") is None and parse_delay_base(None) is None
    assert hop_latency(20, 5) == 25 and hop_latency(None, None) is None
    assert hop_latency(None, 7) == 7                    # missing side counts as 0


def test_assemble_model_from_cli_text_and_trace():
    r1_routes = "[0] 10.0.1.0 255.255.255.0 0.0.0.0 tun0\n[1] 10.0.2.0 255.255.255.0 10.0.1.2 tun0"
    r2_routes = "[0] 10.0.1.0 255.255.255.0 0.0.0.0 tun0\n[1] 10.0.2.0 255.255.255.0 0.0.0.0 tun1"
    r3_routes = "[0] 10.0.2.0 255.255.255.0 0.0.0.0 tun0"
    infos = [("R1", "R1", r1_routes, "eth0 10.0.1.1/24 m"),
             ("R2", "R2", r2_routes, "eth0 10.0.1.2/24 m\neth1 10.0.2.1/24 m"),
             ("R3", "R3", r3_routes, "eth0 10.0.2.2/24 m")]
    lat = {("R1", "R2"): 5, ("R2", "R3"): 10}
    m = assemble_model(infos, [("R1", "R2"), ("R2", "R3")],
                       latency_of=lambda a, b: lat.get((a, b)) or lat.get((b, a)))
    assert m.ip_owner["10.0.1.2"] == "R2" and m.ip_owner["10.0.2.2"] == "R3"
    t = forwarding_tree(m, "R1")
    assert t.per_dest["R3"].path == ["R1", "R2", "R3"] and t.per_dest["R3"].total_latency == 15


def test_collect_router_data_via_injected_callbacks():
    routes = {"R1": "[0] 10.0.1.0 255.255.255.0 0.0.0.0 tun0\n"
                    "[1] 10.0.2.0 255.255.255.0 10.0.1.2 tun0",
              "R2": "[0] 10.0.2.0 255.255.255.0 0.0.0.0 tun0"}
    ifaces = {"R1": "eth0 10.0.1.1/24 m", "R2": "eth0 10.0.1.2/24 m\neth1 10.0.2.2/24 m"}
    props = {("R1", "DelayEgress"): "8 1 0.9", ("R2", "DelayIngress"): "2 0 0"}

    def query(name, cmd):
        return routes[name] if "route" in cmd else ifaces[name]

    # links omitted → adjacency derived from the live connected routes
    m = collect_router_data([("R1", "R1"), ("R2", "R2")], query,
                            lambda rid, k: props.get((rid, k), ""))
    assert m.ip_owner["10.0.1.2"] == "R2"
    assert m.edge_latency("R1", "R2") == 10             # egress 8 + ingress 2 (configured delay VNF)
    assert forwarding_tree(m, "R1").per_dest["R2"].status == "ok"


def test_derive_edges_handles_switch_mediated_adjacency():
    # R1 and R2 share segment 10.0.1.0/24 (via a switch — no direct link); each has a connected route
    R1 = RouterNode("R1", "R1", {"10.0.1.1"},
                    [_r(0, "10.0.1.0", "255.255.255.0", "0.0.0.0")])
    R2 = RouterNode("R2", "R2", {"10.0.1.2"},
                    [_r(0, "10.0.1.0", "255.255.255.0", "0.0.0.0")])
    R3 = RouterNode("R3", "R3", {"10.0.9.9"},                          # off on its own segment
                    [_r(0, "10.0.9.0", "255.255.255.0", "0.0.0.0")])
    from gini.domain.routing_model import derive_edges
    pairs = {frozenset((e.a, e.b)) for e in derive_edges([R1, R2, R3])}
    assert frozenset(("R1", "R2")) in pairs             # share 10.0.1.0/24 → adjacent
    assert frozenset(("R1", "R3")) not in pairs         # different segments → not adjacent


def test_trace_is_protocol_agnostic():
    # SAME topology, DIFFERENT tables -> the trace faithfully follows whatever the router believes.
    # RIP (hop-count) picks the 2-hop path via R2; OSPF (cost) picks the 3-hop low-latency path via R3.
    def model(r1_route):
        R1 = RouterNode("R1", "R1", {"10.0.1.1", "10.0.7.1"}, [
            _r(0, "10.0.1.0", "255.255.255.0", "0.0.0.0"),
            _r(1, "10.0.7.0", "255.255.255.0", "0.0.0.0"),
            r1_route,
        ])
        R2 = RouterNode("R2", "R2", {"10.0.1.2", "10.0.9.1"},
                        [_r(0, "10.0.9.0", "255.255.255.0", "0.0.0.0")])
        R3 = RouterNode("R3", "R3", {"10.0.7.2", "10.0.8.1"},
                        [_r(0, "10.0.9.0", "255.255.255.0", "10.0.8.2")])
        R4 = RouterNode("R4", "R4", {"10.0.8.2", "10.0.9.2"},
                        [_r(0, "10.0.9.0", "255.255.255.0", "0.0.0.0")])
        edges = [Edge("R1", "R2", 50), Edge("R1", "R3", 1), Edge("R3", "R4", 1)]
        return RoutingModel([R1, R2, R3, R4], edges, dest_ip={"R2": "10.0.9.1"})
    rip = forwarding_tree(model(_r(2, "10.0.9.0", "255.255.255.0", "10.0.1.2")), "R1")   # via R2
    ospf = forwarding_tree(model(_r(2, "10.0.9.0", "255.255.255.0", "10.0.7.2")), "R1")  # via R3
    assert rip.per_dest["R2"].path[1] == "R2"            # RIP: 1 hop, but 50ms
    assert ospf.per_dest["R2"].path[1] == "R3"           # OSPF: more hops, but 1+1ms
