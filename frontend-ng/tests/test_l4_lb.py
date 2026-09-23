"""The load balancer's VIP has to actually answer.

`gini.samples.l4_lb` shipped unable to serve a single request (book §23.6, "Balancing and
Growing a Service"). `curl http://10.0.1.100/` failed for three independent reasons, each of which
alone was enough, and each of which hid the next:

1. Nothing answered ARP for the VIP. No host owns 10.0.1.100, so a client on the segment asked
   "who has 10.0.1.100?", got no reply, and never sent an IP packet — the flow rules never ran.
2. It forwarded through OFPP_NORMAL. On the GINI Flow Switch (the gRouter in --openflow mode)
   NORMAL is "hand to the router's own IP pipeline", not L2 switching, and on a flat segment that
   is a black hole: with l4_lb loaded even a DIRECT fetch of a backend failed, because ARP between
   hosts never crossed the switch.
3. Rewriting only the IP left the frame addressed to the VIP's MAC, which no backend accepts.

Underneath all three, the switch itself crashed on the first TCP address rewrite (the checksum code
in tcp.c / udp.c; see backend/grouter-build/tests/checksum_test.c) — which the app never reached
while (1) held, so fixing the app alone would only have moved the failure.

These read the app's source rather than running POX, the same way test_controller_app.py checks
the samples: the behaviour was verified end-to-end against real containers when this was fixed,
and what must not come back is the SHAPE — so that is what is pinned.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parents[2]
       / "backend" / "sdn" / "pox" / "ext" / "gini" / "samples" / "l4_lb.py")

pytestmark = pytest.mark.skipif(not APP.exists(), reason="backend/sdn not checked out")


def _src() -> str:
    return APP.read_text(encoding="utf-8")


def _code() -> str:
    """The source with comments and docstrings stripped, so an explanation that MENTIONS
    OFPP_NORMAL (the header does, to say why it is gone) cannot satisfy or fail a check."""
    tree = ast.parse(_src())
    return ast.unparse(tree)


def test_it_never_forwards_through_normal():
    assert "OFPP_NORMAL" not in _code(), (
        "OFPP_NORMAL on the GINI Flow Switch is the ROUTER pipeline, not L2 switching — "
        "forward to a learned port or flood instead")


def test_it_answers_arp_for_the_vip():
    code = _code()
    assert "arp.REQUEST" in code and "protodst == self.vip" in code, \
        "a request for the VIP must be recognised"
    assert "arp.REPLY" in code, "and answered — otherwise no client ever sends to the VIP"


def test_the_forward_flow_rewrites_the_mac_as_well_as_the_ip():
    code = _code()
    assert "ofp_action_dl_addr.set_dst" in code, \
        "a frame still addressed to the VIP's MAC is dropped by the backend's NIC"
    assert "ofp_action_nw_addr.set_dst" in code


def test_the_reverse_flow_restores_the_vip_on_both_layers():
    code = _code()
    assert "ofp_action_dl_addr.set_src" in code
    assert "ofp_action_nw_addr.set_src" in code


def test_the_balancer_rules_outrank_a_learned_mac_rule():
    """A plain dl_dst rule for a client (default priority 0x8000) would otherwise race the
    reverse rewrite for a backend's reply, and the client would see the backend's address."""
    tree = ast.parse(_src())
    prio = next(n.value.value for n in ast.walk(tree)
                if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_LB_PRIORITY"
                                                     for t in n.targets))
    assert prio > 0x8000


def test_a_misaddressed_pool_is_named_not_silent():
    """With automatic addressing the textbook's .11 is Client_2, not Web_1. The student then
    sees one client fail for no visible reason; the controller log must say why."""
    assert "is in the backend pool but is sending to the VIP" in _src()
