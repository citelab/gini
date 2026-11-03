"""A station sends multicast into the fabric, as it already does the experiment's unicast.

Lab 6's sender.py names no interface, so its datagrams to 239.1.1.1 followed the only route that
matched — the container's default, Docker's eth0 — and never reached the gRouter. The shuttle now
routes 224.0.0.0/4 onto the fabric interface the way it routes 10.0.0.0/8.
"""
from __future__ import annotations

from gini.runtime import shuttle


def test_multicast_leaves_by_the_interface_on_the_gateways_subnet():
    ifaces = [{"tap": "gini0", "ip": "10.0.1.10/24"}, {"tap": "gini1", "ip": "10.0.2.10/24"}]
    assert shuttle.multicast_iface(ifaces, "10.0.2.1") == "gini1"


def test_a_station_with_no_router_still_sends_multicast_to_its_lan():
    """Hosts on one switch have no gateway, and multicast between them must still go out the
    fabric rather than onto Docker's bridge."""
    assert shuttle.multicast_iface([{"tap": "gini0", "ip": "10.0.1.10/24"}], None) == "gini0"


def test_a_gateway_off_every_subnet_falls_back_to_the_first_interface():
    assert shuttle.multicast_iface([{"ip": "10.0.1.10/24"}], "10.9.9.1") == "gini0"


def test_the_route_is_replace_so_a_restart_never_fails_on_an_existing_one(monkeypatch):
    calls = []
    monkeypatch.setattr(shuttle.subprocess, "run", lambda argv, **k: calls.append(argv))
    shuttle.add_multicast_route("gini0")
    assert calls == [["ip", "route", "replace", "224.0.0.0/4", "dev", "gini0"]]
