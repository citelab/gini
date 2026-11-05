"""The multicast MAP: every group's distribution tree, laid over the network as drawn.

The Multicast HUD used to be a list — a chip per group and a badge per member interface — which
answered "is anything joined?" and nothing a student actually asks: where does the sender's
traffic go, which routers copy it, which branch was pruned, and why does that one machine not get
it? All of that is already published by `mcast_tree.lua`, per source:

    S 10.0.1.10 G 239.1.1.1 RPF 1 OIF 2,3 PRUNED 4 DROPS 0 COPIES 240

so this module turns those lines into the tree itself — edges from the source to its first
router, from each router out of each interface it copies to, on to the next router or to the
members on that segment — with nothing inferred that a router did not report. No shortest-path
computation, the same rule the Network HUD keeps: the picture is what the routers DID.

**Interfaces are numbered the way the gRouter numbers them.** `ifN` is the N-th interface in
the compiler's order (tun1 is the first), the same arithmetic `link_faults` and the Network HUD
use, so `OIF 2` is the router's second entry in `address_map`.

**A segment is a subnet.** Every interface on 10.0.3.0/24 — the router's and the machines' —
is one segment, whether its devices are cabled straight to the router or through a switch. A
switch, when there is one, is the HUB the picture routes that segment's edges through, at the
switch's own place on the canvas.

**Which machine joined is not something a router knows.** IGMP tells a router "someone on this
interface is a member", which is exactly as much as it needs to forward. On a segment with one
machine that is enough to name it. On a shared LAN it is not, and guessing would draw a member
that is not one; there the caller may pass what the machines themselves report
(`/proc/net/igmp`, parsed by `parse_igmp`), and without that the machines are marked as
"a member is on this LAN" rather than as members.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from .mcast import GroupState

#: Group colours, as theme accent names, handed out in the order groups first appear and never
#: reshuffled — a group that changed colour when another joined would read as a different group.
#: Red is left out (faults) and so is slate (the network itself).
PALETTE = ("teal", "amber", "pink", "indigo", "green", "orange", "purple", "cyan")


# ---- what the routers report ------------------------------------------------------------------ #
@dataclass(frozen=True)
class SourceTree:
    """One router's state for one (source, group): where it must arrive, where it goes."""
    router: str
    source: str
    group: str
    rpf: int | None                  # the interface data must arrive on; None: no route back
    oifs: tuple[int, ...]            # interfaces it is copied out of
    pruned: tuple[int, ...]          # interfaces a downstream router said "no thanks" on
    drops: int                       # copies refused by the reverse-path check
    copies: int                      # copies sent on


_S_LINE = re.compile(r"^S\s+(\S+)\s+G\s+(\S+)\s+RPF\s+(\S+)\s+OIF\s+(\S+)\s+PRUNED\s+(\S+)"
                     r"\s+DROPS\s+(\d+)\s+COPIES\s+(\d+)\s*$")


def _ints(field_: str) -> tuple[int, ...]:
    if field_ in ("-", ""):
        return ()
    out = []
    for x in field_.split(","):
        try:
            out.append(int(x))
        except ValueError:
            pass
    return tuple(out)


def parse_sources(text: str, router: str) -> list[SourceTree]:
    """The `S` lines of one router's `gpipe cp status`. Everything else is ignored."""
    out: list[SourceTree] = []
    for raw in (text or "").splitlines():
        m = _S_LINE.match(raw.strip())
        if not m:
            continue
        rpf = _ints(m.group(3))
        out.append(SourceTree(router=router, source=m.group(1), group=m.group(2),
                              rpf=rpf[0] if rpf else None, oifs=_ints(m.group(4)),
                              pruned=_ints(m.group(5)), drops=int(m.group(6)),
                              copies=int(m.group(7))))
    return out


def runs_forwarder(text: str) -> bool:
    """Did this router answer with a multicast forwarder's snapshot at all? (`MCAST v1 0` is a
    forwarder with no groups — a very different thing to say than "nothing is loaded".)"""
    return any(ln.startswith("MCAST v") for ln in (text or "").splitlines())


def parse_igmp(text: str) -> set[str]:
    """The groups a machine has joined on its fabric interfaces, from `/proc/net/igmp`.

    Group addresses there are hex in host byte order — little-endian on every machine GINI
    runs on — so `010101EF` is 239.1.1.1. The all-hosts group and the rest of 224.0.0.0/24 are
    joined by every interface and say nothing about the lab; Docker's `eth0` and `lo` are not
    part of the drawn network.
    """
    groups: set[str] = set()
    dev = ""
    for raw in (text or "").splitlines():
        head = re.match(r"^\d+\s+(\S+)\s*:", raw)
        if head:
            dev = head.group(1)
            continue
        m = re.match(r"^\s+([0-9A-Fa-f]{8})\s", raw)
        if not m or not dev.startswith("gini"):
            continue
        b = bytes.fromhex(m.group(1))[::-1]
        g = ".".join(str(x) for x in b)
        if not g.startswith("224.0.0."):
            groups.add(g)
    return groups


# ---- the network, as segments ----------------------------------------------------------------- #
@dataclass
class Segment:
    subnet: str
    routers: list[tuple[str, int]] = field(default_factory=list)    # (router, ifN)
    hosts: list[str] = field(default_factory=list)
    hub: str | None = None                                          # a switch on it, if any


def segments(addressing: dict) -> dict[str, Segment]:
    """subnet -> Segment, from `compiler.address_map` output."""
    segs: dict[str, Segment] = {}
    roles = {n: (i or {}).get("role") for n, i in (addressing or {}).items()}
    for name, info in (addressing or {}).items():
        role = (info or {}).get("role")
        if role not in ("router", "machine"):
            continue
        for k, itf in enumerate(info.get("interfaces") or []):
            sub = str(itf.get("subnet") or "")
            if not sub:
                continue
            seg = segs.setdefault(sub, Segment(sub))
            if role == "router":
                seg.routers.append((name, k + 1))
            elif name not in seg.hosts:
                seg.hosts.append(name)
            peer = itf.get("peer")
            if peer and roles.get(peer) == "switch":
                seg.hub = peer
    return segs


def _host_ips(addressing: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, info in (addressing or {}).items():
        if (info or {}).get("role") != "machine":
            continue
        for itf in info.get("interfaces") or []:
            ip = str(itf.get("ip") or "").split("/")[0]
            if ip:
                out[ip] = name
    return out


def _seg_of(segs: dict[str, Segment]) -> dict[tuple[str, int], Segment]:
    return {(r, i): s for s in segs.values() for (r, i) in s.routers}


def _ambiguous(s: Segment, router: str) -> bool:
    """Can't the router's word name the member? A router's `G` line lists interfaces with
    members AND interfaces it has recently copied to, so on a segment that also holds another
    router — or more than one machine — "this interface" does not say which machine joined."""
    return len(s.hosts) > 1 or any(r2 != router for r2, _ in s.routers)


def shared_lans(addressing: dict, rows: list[GroupState]) -> list[str]:
    """Machines whose membership the routers cannot pin down (see `_ambiguous`), behind an
    interface some router reports for a group. Only these are worth asking directly —
    everywhere else the router's word is already exact."""
    segs = segments(addressing)
    by_if = _seg_of(segs)
    out: list[str] = []
    for r in rows:
        for i in r.ifaces:
            s = by_if.get((r.router, i))
            if s and s.hosts and _ambiguous(s, r.router):
                out.extend(h for h in s.hosts if h not in out)
    return out


# ---- the map ---------------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Edge:
    a: str                           # from (a router or a machine)
    b: str                           # to
    group: str
    kind: str                        # "data" | "pruned"
    router: str = ""                 # the router whose copy this is ("" for source -> 1st hop)
    iface: int = 0                   # ... and the interface it left by
    via: str | None = None           # a switch the segment runs through


@dataclass
class GroupView:
    group: str
    colour: str
    sources: list[str] = field(default_factory=list)       # machine names (or bare IPs)
    members: list[str] = field(default_factory=list)       # machines known to have joined
    maybe: list[str] = field(default_factory=list)         # "a member is on this LAN"
    edges: list[Edge] = field(default_factory=list)


@dataclass
class McastMap:
    groups: dict[str, GroupView] = field(default_factory=dict)
    drops: dict[str, int] = field(default_factory=dict)    # router -> reverse-path refusals

    def order(self) -> list[str]:
        return list(self.groups)


def assign_colours(groups, assigned: dict[str, str]) -> dict[str, str]:
    """Give each new group the next palette colour; keep every colour already handed out."""
    for g in groups:
        if g not in assigned:
            used = list(assigned.values())
            free = [c for c in PALETTE if c not in used]
            assigned[g] = free[0] if free else PALETTE[len(assigned) % len(PALETTE)]
    return assigned


def build(addressing: dict, rows: list[GroupState], trees: list[SourceTree],
          joined: dict[str, set[str]] | None = None,
          colours: dict[str, str] | None = None) -> McastMap:
    """Every group's tree. `rows` and `trees` are what the routers published this poll; `joined`
    is what machines on shared LANs reported about themselves (see `shared_lans`)."""
    segs = segments(addressing)
    by_if = _seg_of(segs)
    ips = _host_ips(addressing)
    joined = joined or {}
    groups = sorted({r.group for r in rows} | {t.group for t in trees},
                    key=lambda g: tuple(int(x) for x in g.split(".")) if _is_v4(g) else (999,))
    colours = assign_colours(groups, colours if colours is not None else {})
    out = McastMap()

    for g in groups:
        view = GroupView(g, colours[g])
        # members: every interface a router reports for the group, resolved to machines
        for r in (r for r in rows if r.group == g):
            for i in r.ifaces:
                s = by_if.get((r.router, i))
                if s is None or not s.hosts:
                    continue
                if not _ambiguous(s, r.router):
                    _add(view.members, s.hosts[0])
                    continue
                # each machine on its own word; one that has not said is UNKNOWN, not "no"
                for h in s.hosts:
                    if h not in joined:
                        _add(view.maybe, h)
                    elif g in joined[h]:
                        _add(view.members, h)
        mine = [t for t in trees if t.group == g]
        for t in mine:
            _add(view.sources, ips.get(t.source, t.source))
        rpf_seg = {(t.router, t.source): by_if.get((t.router, t.rpf)) for t in mine
                   if t.rpf is not None}
        for t in mine:
            src_host = ips.get(t.source)
            arrive = rpf_seg.get((t.router, t.source))
            # source -> its first-hop router: the router whose reverse path is the source's LAN
            # drawn even with nobody downstream: the traffic DOES reach that router, and "it
            # arrives and goes nowhere" is exactly what a student needs to see before a join
            if arrive is not None and src_host and src_host in arrive.hosts:
                _add(view.edges, Edge(src_host, t.router, g, "data", via=arrive.hub))
            for i in t.oifs:
                s = by_if.get((t.router, i))
                if s is None:
                    continue
                for h in s.hosts:
                    if h in view.members or h in view.maybe:
                        _add(view.edges, Edge(t.router, h, g, "data", t.router, i, s.hub))
                for (r2, _i2) in s.routers:
                    if r2 == t.router:
                        continue
                    down = rpf_seg.get((r2, t.source))
                    if down is s:
                        _add(view.edges, Edge(t.router, r2, g, "data", t.router, i, s.hub))
            for i in t.pruned:
                s = by_if.get((t.router, i))
                if s is None:
                    continue
                for (r2, _i2) in s.routers:
                    if r2 != t.router:
                        _add(view.edges, Edge(t.router, r2, g, "pruned", t.router, i, s.hub))
        out.groups[g] = view

    for t in trees:
        if t.drops:
            out.drops[t.router] = out.drops.get(t.router, 0) + t.drops
    return out


def _add(seq: list, item) -> None:
    if item not in seq:
        seq.append(item)


def _is_v4(s: str) -> bool:
    try:
        ipaddress.IPv4Address(s)
        return True
    except ValueError:
        return False


def describe_event(addressing: dict, ev) -> str:
    """A tracker event in words that are true of the network behind the interface.

    The tracker sees a router's `G` line gain or lose an interface and calls it a join or a
    leave. Toward a MACHINE that is what it is. Toward another router it is not: a dense-mode
    forwarder re-floods a pruned branch every PRUNE_HOLD ticks and the far router prunes it again,
    so "R1 if2 join" every ten seconds — with nobody joined behind R3 — told a student something
    false about who wanted the traffic.
    """
    s = _seg_of(segments(addressing)).get((ev.router, ev.iface))
    where = f"{ev.router} if{ev.iface}"
    if s is None:
        return f"{where} {ev.kind} {ev.group}"
    others = [r for r, _ in s.routers if r != ev.router]
    if not s.hosts and others:
        toward = ", ".join(others)
        return (f"{ev.router} re-floods {ev.group} toward {toward}" if ev.kind == "join"
                else f"{toward} pruned {ev.group} — nobody wants it there")
    who = s.hosts[0] if len(s.hosts) == 1 and not others else f"the LAN on if{ev.iface}"
    return (f"{who} joined {ev.group} (via {ev.router})" if ev.kind == "join"
            else f"{who} left {ev.group} (via {ev.router})")
