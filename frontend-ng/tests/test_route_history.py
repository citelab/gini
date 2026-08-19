"""RouteHistory — the P4 convergence recorder behind the Routing HUD's replay scrub.

Pure domain: pushes are deduped by routing-state signature (a converged network costs one
snapshot no matter how long it sits still), `at(t)` returns the state in force at t, and
the ring retains RETAIN_S seconds of convergence events. Origin tags ('C'/'S'/'D') are
part of the signature, so RIP overwriting a static route with the same nexthop still
registers as a change.
"""
from gini.domain.routing_model import (RouteHistory, RouterNode, RoutingModel,
                                       model_signature)
from gini.domain.routetable import RouteEntry


def _model(nh, origin=""):
    r = RouterNode("R1", "R1", {"10.0.1.1"},
                   [RouteEntry(0, "10.0.9.0", "255.255.255.0", nh, "tun1", origin)])
    return RoutingModel([r], [])


def test_push_dedupes_unchanged_state():
    h = RouteHistory()
    assert h.push(_model("10.0.1.2"), 100.0) is True
    assert h.push(_model("10.0.1.2"), 102.5) is False      # same state -> live edge only
    assert h.t_end == 102.5 and len(h) == 1


def test_change_creates_snapshot_and_ticks():
    h = RouteHistory()
    h.push(_model("10.0.1.2"), 100.0)
    h.push(_model("10.0.1.3"), 105.0)                      # nexthop changed (convergence)
    assert len(h) == 2
    assert h.change_times() == [100.0, 105.0]


def test_at_returns_state_in_force():
    h = RouteHistory()
    h.push(_model("10.0.1.2"), 100.0)
    h.push(_model("10.0.1.3"), 105.0)
    assert h.at(104.9).routers["R1"].table[0].nexthop == "10.0.1.2"
    assert h.at(105.0).routers["R1"].table[0].nexthop == "10.0.1.3"
    assert h.at(99.0).routers["R1"].table[0].nexthop == "10.0.1.2"   # pre-start -> oldest
    assert h.latest().routers["R1"].table[0].nexthop == "10.0.1.3"


def test_ring_retains_recent_events_only():
    h = RouteHistory()
    for i in range(5):
        h.push(_model(f"10.0.1.{i + 2}"), 100.0 + i * 200)  # 200s apart vs RETAIN_S=600
    assert h.snaps[-1][0] == 900.0                          # newest always kept
    assert h.snaps[0][0] >= 900.0 - h.RETAIN_S


def test_signature_is_origin_aware():
    # same nexthop, different origin (RIP 'D' replacing static 'S') must be a change:
    # the HUD replay should show the moment the protocol took over the route.
    assert (model_signature(_model("10.0.1.2", "S"))
            != model_signature(_model("10.0.1.2", "D")))
