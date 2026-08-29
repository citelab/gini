"""Routing/ARP-table parsing — turn the gRouter's `route` and `arp` CLI dumps into rows for
the Router Lab's routing view.

A regular router is the C gRouter; at runtime `element_query(router, "route show")` prints
its route table and `element_query(router, "arp show")` its ARP cache (the gRouter CLI needs
the `show` subcommand — a bare `route`/`arp` prints nothing). This module parses both.
Pure/text-only, so it's unit-tested without Docker.

`route` output (from routetable.c printRouteTable):
    Index   Network         Netmask         Nexthop         Interface
    [0]     10.0.1.0        255.255.255.0   0.0.0.0         tun1
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# the gRouter prints an optional trailing Origin column (C=connected, S=static,
# D=dynamic/control-plane); older builds print 5 columns, so it must stay optional
_ROUTE_RE = re.compile(r"^\[(\d+)\]\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+([CSD]))?\s*$")


@dataclass
class RouteEntry:
    index: int
    network: str
    netmask: str
    nexthop: str          # 0.0.0.0 == directly connected
    iface: str
    origin: str = ""      # "C" connected · "S" static · "D" dynamic (control plane); "" = unknown

    @property
    def direct(self) -> bool:
        return self.nexthop in ("0.0.0.0", "*", "", "0")

    def nexthop_str(self) -> str:
        return "direct (on-link)" if self.direct else self.nexthop


def parse_routes(text: str) -> list[RouteEntry]:
    """Rows of the gRouter route table (lines like `[0] net mask nexthop iface`)."""
    out: list[RouteEntry] = []
    for line in (text or "").splitlines():
        m = _ROUTE_RE.match(line.strip())
        if m:
            out.append(RouteEntry(int(m.group(1)), m.group(2), m.group(3),
                                  m.group(4), m.group(5), m.group(6) or ""))
    return out


# ARP cache: the gRouter prints IP<->MAC pairs; be tolerant about exact columns and just
# pull the first dotted-quad IP and first MAC on each line.
_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_MAC_RE = re.compile(r"\b([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")


@dataclass
class ArpEntry:
    ip: str
    mac: str


def parse_arp(text: str) -> list[ArpEntry]:
    out: list[ArpEntry] = []
    for line in (text or "").splitlines():
        ip = _IP_RE.search(line)
        mac = _MAC_RE.search(line)
        if ip and mac:
            out.append(ArpEntry(ip.group(1), mac.group(1)))
    return out
