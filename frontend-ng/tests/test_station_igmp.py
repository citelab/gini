"""Every station speaks IGMPv2 — set at container creation, because it cannot be set after.

Lab 6 told students to run `sysctl -w net.ipv4.conf.all.force_igmp_version=2` on the receiver, and
every one of them got `permission denied on key`: Docker mounts /proc/sys read-only in an
unprivileged container. Without it the receiver joined with an IGMPv3 report, which the handout's
mcast.lua does not read, and it never sent the v2 query that would have made Linux fall back. So
no join, and no datagram. Measured on a real gRouter: 0 delivered as handed out, 12/12 with v2 set
this way.
"""
from __future__ import annotations

import pytest

yaml = pytest.importorskip("yaml")

from gini.domain.topology import Topology

from gini.services.compiler import RuntimeCompiler
from gini.services.orchestrator import _compose

V2 = {"net.ipv4.conf.all.force_igmp_version": 2,
      "net.ipv4.conf.default.force_igmp_version": 2}


def _services(toolkits):
    t = Topology("igmp")
    r = t.add_device("router", "R1")
    names = []
    for i, tk in enumerate(toolkits):
        h = t.add_device("host", f"H{i + 1}", properties={"Toolkit": tk})
        t.add_link(h.id, r.id)
        names.append(h.name.lower())
    return yaml.safe_load(_compose(RuntimeCompiler().compile(t)))["services"], names


def test_every_station_toolkit_is_created_speaking_igmp_v2():
    services, names = _services(["lean", "full", "security", "gui"])
    for n in names:
        assert services[n]["sysctls"] == V2, n


def test_default_is_set_as_well_as_all_because_the_fabric_interfaces_come_later():
    """The shuttle creates gini0… after the container starts; a new interface copies
    `default`, not `all`."""
    services, names = _services(["lean"])
    assert "net.ipv4.conf.default.force_igmp_version" in services[names[0]]["sysctls"]


def test_only_the_stations_own_network_knobs_are_touched():
    """The alternatives that let `sysctl -w` work also open the global kernel knobs — on a Linux
    laptop, the student's real kernel. Nothing here may reach for them."""
    services, names = _services(["lean", "full"])
    for n in names:
        s = services[n]
        assert not s.get("privileged")
        assert not any("systempaths" in str(o) for o in s.get("security_opt", []))
        assert all(k.startswith("net.") for k in s["sysctls"])


def test_routers_are_not_given_station_sysctls():
    """A gRouter is a userspace router; the kernel's IGMP setting means nothing to it."""
    services, _ = _services(["lean"])
    assert "sysctls" not in services["r1"]
