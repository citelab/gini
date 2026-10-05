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


# -- adding a route, as the Router Lab's form does ----------------------------------------------
#
# The form builds the SAME `route add` line a student would type at the console and shows it before
# sending, so the visual way in teaches the textual one rather than replacing it. Everything a
# student can get wrong is caught here, in words, before the router sees it: the router's own
# parser takes `-dev`, `-net`, `-netmask` IN THAT ORDER, reads only the digits of the device name,
# and says nothing useful about a malformed address.

def _ipv4(text: str) -> tuple[int, ...] | None:
    parts = (text or "").strip().split(".")
    if len(parts) != 4:
        return None
    try:
        octets = tuple(int(p) for p in parts)
    except ValueError:
        return None
    return octets if all(0 <= o <= 255 for o in octets) else None


def prefix_to_netmask(prefix: int) -> str:
    """`24` → `255.255.255.0`, `32` → `255.255.255.255` (a host route)."""
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return ".".join(str((bits >> s) & 0xFF) for s in (24, 16, 8, 0))


@dataclass(frozen=True)
class RouteCommand:
    command: str = ""           # the exact line to send, or "" when invalid
    errors: tuple = ()          # what is wrong, in words a student can act on
    note: str = ""              # something worth saying even though it is valid

    @property
    def ok(self) -> bool:
        return bool(self.command) and not self.errors


def route_add_command(destination: str, prefix: int, iface: str, via: str = "") -> RouteCommand:
    """Validate a route and build `route add -dev <iface> -net <net> -netmask <mask> [-gw <via>]`.

    The destination is reduced to its network address for the prefix (10.0.3.12/24 becomes
    10.0.3.0) and the note says so, because the router would otherwise store an entry that reads
    as a host but matches a whole subnet. A /32 is a per-host route and is named as one.
    """
    errors = []
    dst = _ipv4(destination)
    if dst is None:
        errors.append("Destination must be an IPv4 address, like 10.0.3.0.")
    try:
        prefix = int(prefix)
    except (TypeError, ValueError):
        prefix = -1
    if not 0 <= prefix <= 32:
        errors.append("Prefix length must be between 0 and 32.")
    dev = (iface or "").strip()
    if not dev or not any(c.isdigit() for c in dev):
        errors.append("Pick the interface the route leaves through (tun1, tun2, …).")
    gw = (via or "").strip()
    if gw and _ipv4(gw) is None:
        errors.append("Next hop must be an IPv4 address, or empty for a directly connected route.")
    if errors:
        return RouteCommand(errors=tuple(errors))

    mask = prefix_to_netmask(prefix)
    m = tuple(int(x) for x in mask.split("."))
    net = ".".join(str(a & b) for a, b in zip(dst, m))
    notes = []
    if prefix == 32:
        notes.append(f"A per-host route: only {net} is sent this way; the rest of its subnet keeps "
                     f"its existing route (the longest prefix wins).")
    elif net != ".".join(map(str, dst)):
        notes.append(f"{destination.strip()} with /{prefix} is the network {net} — the router "
                     f"stores the network address.")
    cmd = f"route add -dev {dev} -net {net} -netmask {mask}" + (f" -gw {gw}" if gw else "")
    return RouteCommand(command=cmd, note=" ".join(notes))


def route_del_command(entry: RouteEntry) -> str:
    return f"route del {entry.index}"
