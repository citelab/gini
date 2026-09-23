"""The SDN sample apps a student can pick must actually deliver traffic.

Five of the eight `gini.samples.*` apps forwarded with OFPP_NORMAL. On the GINI Flow Switch
(the gRouter in --openflow mode) NORMAL means "hand the frame to the ROUTER's own IP
pipeline", not "behave like a switch", and on a flat segment the router has no interfaces or
routes to use — so NORMAL delivered nothing. With `redirect`, `port_knock`, `ids` or `l4_lb`
loaded, hosts could not even ARP for each other, and every one of those labs was dead on
arrival. They now deliver ordinary traffic through `gini.samples._l2`, the learning-switch
half they share.

Two follow-on bugs surfaced only once packets flowed, and are pinned here too:

* `ids` counted every packet, replies included. A web server's replies go to each client's
  fresh ephemeral port, so ten ordinary page loads flagged — and, with --block, cut off —
  the SERVER as a port scanner. It now counts attempts only.
* The switch read controller messages with `recv()` + `sleep(1)`, so each packet-out waited
  up to a second. For an app that sees every packet that meant a 3-4 s page load or a
  timeout; it now waits on the socket (measured: ~3 ms).

All of it was verified end-to-end against real containers when fixed. These check the SHAPE
that must not come back, the same way test_controller_app.py reads the samples.

`sfc` is exempt: it still has a NORMAL fallback, but it is deliberately not offered to students
(docs/design/sdn-controller-apps.md, "Deferred 1") until it has been run on a real chain.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "backend" / "sdn" / "pox" / "ext" / "gini" / "samples"
CTRL_IFACE = ROOT / "backend" / "src" / "grouter" / "openflow_ctrl_iface.c"

pytestmark = pytest.mark.skipif(not SAMPLES.exists(), reason="backend/sdn not checked out")

#: Offered in the controller's App presets (devices.py). `sfc` is not — see the docstring.
OFFERED = ("switch", "l4_lb", "redirect", "port_knock", "ids", "packet_loss")


def _code(name: str) -> str:
    """Source without comments or docstrings — the headers explain NORMAL, which must not
    count as using it."""
    return ast.unparse(ast.parse((SAMPLES / f"{name}.py").read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", OFFERED)
def test_no_offered_sample_forwards_through_normal(name):
    assert "OFPP_NORMAL" not in _code(name), (
        f"gini.samples.{name} uses OFPP_NORMAL — on the GINI Flow Switch that is the router's "
        f"IP pipeline, which delivers nothing on a flat segment. Use gini.samples._l2.")


@pytest.mark.parametrize("name", ("redirect", "port_knock", "ids"))
def test_apps_that_must_see_every_packet_install_no_forwarding_rule(name):
    """A learned `dst = the server's MAC` rule would carry the next client's SYN past
    redirect, a knock past port_knock and a scan past ids — each would quietly stop working."""
    code = _code(name)
    assert "self.l2.forward(event" in code, "ordinary traffic must go through the shared helper"
    assert "install=True" not in code


def test_the_shared_helper_floods_rather_than_normal_and_keeps_the_ingress_port():
    code = _code("_l2")
    assert "OFPP_FLOOD" in code and "OFPP_NORMAL" not in code
    # Naming in_port=OFPP_NONE for a frame from a packet-in overrode the real ingress port and
    # broke ARP between hosts outright; it must only be named for a frame the controller built.
    assert "in_port=of.OFPP_NONE" not in code


def test_redirect_rewrites_the_mac_as_well_as_the_ip():
    """Rewriting only the IP still delivered the frame to the SERVER's MAC."""
    code = _code("redirect")
    assert "ofp_action_dl_addr.set_dst(vmac)" in code
    assert "ofp_action_dl_addr.set_src(server_mac)" in code


def test_ids_counts_attempts_not_replies():
    code = _code("ids")
    assert "_is_probe" in code
    assert "l4.SYN and (not l4.ACK)" in code or "l4.SYN and not l4.ACK" in code


@pytest.mark.skipif(not CTRL_IFACE.exists(), reason="gRouter source not checked out")
def test_the_switch_waits_on_the_controller_socket_instead_of_sleeping():
    src = CTRL_IFACE.read_text(encoding="utf-8")
    loop = src[src.index('"[openflow_ctrl_iface]:: Receiving messages."'):]
    loop = loop[:loop.index("while (ret == 0);")]
    loop = re.sub(r"//[^\n]*|/\*.*?\*/", "", loop, flags=re.S)   # the comment explains sleep(1)
    assert "poll(" in loop, "the receive loop must wait on the socket"
    assert "sleep(1)" not in loop, "a sleep here delays every controller message by up to 1 s"
