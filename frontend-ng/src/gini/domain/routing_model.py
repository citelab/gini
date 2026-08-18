"""Routing model + the AUTHENTIC forwarding trace behind the Routing HUD.

The whole point of the HUD is to show the routers' *real* forwarding, so this never runs a Dijkstra.
`forwarding_tree(model, root)` walks each router's **real routing table** next-hop by next-hop —
exactly what a packet would do — and returns the edges that forwarding actually uses. When the tables
are converged and consistent that's a clean tree; mid-convergence it honestly reports a **loop** or a
**dead-end** (the teaching moment). Equal-cost next-hops become a **DAG** (ECMP). Protocol-agnostic:
RIP tables (hop count) and OSPF tables (cost) simply produce different trees from the same topology.

Pure/deterministic over parsed `RouteEntry` rows, so it's unit-tested without Docker — and each
`RoutingModel` is just a snapshot, so recording a sequence of them (for the convergence scrub) is
trivial later.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from .routetable import parse_routes


@dataclass
class RouterNode:
    rid: str
    name: str
    ips: set                      # every interface IP this router owns
    table: list                   # [RouteEntry] — the router's REAL table


@dataclass
class Edge:
    a: str
    b: str
    latency_ms: float | None = None   # configured delay-VNF value (None = unknown/unweighted)


@dataclass
class PathResult:
    dest: str
    status: str                   # "ok" | "loop" | "deadend"
    hop_count: int
    total_latency: float | None   # sum of edge latencies along the chosen path (None if any unknown)
    path: list                    # [rid, …] root → dest (the min-latency reaching path, if any)


@dataclass
class TraceResult:
    root: str
    edges_used: set = field(default_factory=set)   # {(from_rid, to_rid)} directed hops to highlight
    per_dest: dict = field(default_factory=dict)    # {dest_rid: PathResult}
    ecmp: set = field(default_factory=set)          # dests that hit equal-cost multipath
    loops: set = field(default_factory=set)         # dests whose forwarding loops
    deadends: set = field(default_factory=set)      # dests with no route (black hole)


# -- longest-prefix match over real RouteEntry rows -------------------------------------------- #
def _prefix_len(netmask: str) -> int:
    try:
        return bin(int(ipaddress.IPv4Address(netmask))).count("1")
    except ipaddress.AddressValueError:
        return -1


def _matches(entry, ip: str) -> bool:
    try:
        net = ipaddress.IPv4Network(f"{entry.network}/{entry.netmask}", strict=False)
        return ipaddress.IPv4Address(ip) in net
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return False


def _best_entries(table, ip: str) -> list:
    """Longest-prefix match; returns ALL entries tied at the longest prefix (→ ECMP if >1 next-hop)."""
    best_len, best = -1, []
    for e in table:
        if _matches(e, ip):
            plen = _prefix_len(e.netmask)
            if plen > best_len:
                best_len, best = plen, [e]
            elif plen == best_len:
                best.append(e)
    return best


class RoutingModel:
    """A snapshot of the whole network's routing state."""

    def __init__(self, routers, edges, dest_ip=None) -> None:
        self.routers: dict = {r.rid: r for r in routers}
        self.edges: list = list(edges)
        self.ip_owner: dict = {ip: r.rid for r in routers for ip in r.ips}
        self._lat: dict = {frozenset((e.a, e.b)): e.latency_ms for e in self.edges}
        # a representative IP to aim at when reaching each router
        self.dest_ip: dict = dict(dest_ip or {})
        for r in routers:
            self.dest_ip.setdefault(r.rid, next(iter(sorted(r.ips)), None))

    def edge_latency(self, a: str, b: str):
        return self._lat.get(frozenset((a, b)))

    def neighbors(self, rid: str) -> list:
        return [(e.b if e.a == rid else e.a) for e in self.edges if rid in (e.a, e.b)]


# -- the authentic trace ----------------------------------------------------------------------- #
def _trace_dest(model: RoutingModel, root: str, target: str):
    """Walk real next-hops from `root` toward `target`, following ALL equal-cost branches.
    Returns (edges, reaching_paths, saw_loop, saw_deadend, saw_ecmp)."""
    edges: set = set()
    reaching: list = []           # [(path[rid…], total_latency_or_None)]
    state = {"loop": False, "deadend": False, "ecmp": False}

    def dfs(cur, path, lat_sum, lat_known):
        if target in model.routers[cur].ips:              # arrived
            reaching.append((path[:], lat_sum if lat_known else None))
            return
        best = _best_entries(model.routers[cur].table, target)
        if not best:
            state["deadend"] = True
            return
        owners = []
        for e in best:
            nh = target if e.direct else e.nexthop
            owner = model.ip_owner.get(nh)
            if owner is None:
                if e.direct:                              # target's subnet is attached here
                    reaching.append((path[:], lat_sum if lat_known else None))
                else:
                    state["deadend"] = True               # next-hop isn't a known router
                continue
            owners.append(owner)
        uniq = list(dict.fromkeys(owners))                # distinct next-hop routers (order-preserving)
        if len(uniq) > 1:
            state["ecmp"] = True
        for owner in uniq:
            edges.add((cur, owner))
            if owner in path:                             # forwarding loop — record the closing hop
                state["loop"] = True
                continue
            el = model.edge_latency(cur, owner)
            dfs(owner, path + [owner], lat_sum + (el or 0.0), lat_known and el is not None)

    dfs(root, [root], 0.0, True)
    return edges, reaching, state["loop"], state["deadend"], state["ecmp"]


# -- P2: assemble a model from live CLI text (pure; the live glue just feeds it strings) --------- #
_CIDR = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})/\d{1,2}")
_QUAD = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def _is_netmask_or_zero(ip: str) -> bool:
    return ip == "0.0.0.0" or ip.startswith("255.")


def parse_iface_ips(text: str) -> set:
    """Interface IPs from the gRouter's `ifconfig show` / `interfaces` output. Prefers a CIDR
    (`10.0.1.1/24`), else the first non-netmask dotted-quad on the line. Tolerant of both the real
    C gRouter and the in-process simulator formats."""
    ips: set = set()
    for line in (text or "").splitlines():
        m = _CIDR.search(line)
        if m:
            ips.add(m.group(1))
            continue
        for q in _QUAD.findall(line):
            if not _is_netmask_or_zero(q):
                ips.add(q)
                break
    return ips


def parse_delay_base(prop) -> float | None:
    """The base delay (ms) from a router's `DelayIngress` / `DelayEgress` property (e.g. "50 5 0.90"
    → 50, or "50 ms" → 50). None if unset/unparseable."""
    if not prop:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(prop))
    return float(m.group(1)) if m else None


def hop_latency(egress_base, ingress_base) -> float | None:
    """A directed hop's configured latency ≈ the source router's egress delay + the destination
    router's ingress delay. None if neither is configured."""
    if egress_base is None and ingress_base is None:
        return None
    return (egress_base or 0.0) + (ingress_base or 0.0)


def derive_edges(routers) -> list:
    """Router adjacency from REAL data: A and B are neighbours if A has a *connected* route whose
    subnet contains one of B's interface IPs (i.e. they share a segment — direct link OR a shared
    switch). Robust to switch-mediated topologies where routers have no direct link."""
    seen: set = set()
    out: list = []
    for a in routers:
        connected = [e for e in a.table if e.direct]
        for b in routers:
            if b.rid == a.rid:
                continue
            key = frozenset((a.rid, b.rid))
            if key in seen:
                continue
            if any(_matches(e, ip) for ip in b.ips for e in connected):
                seen.add(key)
                out.append(Edge(*sorted((a.rid, b.rid))))
    return out


def assemble_model(router_infos, links=None, latency_of=None) -> RoutingModel:
    """Build a RoutingModel from per-router CLI text.

    router_infos: [(rid, name, route_show_text, ifconfig_show_text)]
    links:        [(a_rid, b_rid)] to draw; None → derive adjacency from connected routes (handles
                  switch-mediated segments)
    latency_of:   optional callable(a_rid, b_rid) -> float|None  (configured delay-VNF latency)
    """
    routers = [RouterNode(rid, name, parse_iface_ips(iface_text), parse_routes(route_text))
               for (rid, name, route_text, iface_text) in router_infos]
    pairs = list(links) if links is not None else [(e.a, e.b) for e in derive_edges(routers)]
    edges = [Edge(a, b, latency_of(a, b) if latency_of else None) for (a, b) in pairs]
    return RoutingModel(routers, edges)


def collect_router_data(routers, query, delay_prop, links=None):
    """Live glue (decoupled via callbacks, so it's testable with fakes and needs no Docker):

    routers:    [(rid, name)] of the router-role devices
    query:      callable(name, cmd) -> text        (e.g. MainWindow.element_query)
    delay_prop: callable(rid, key) -> str          (device property, e.g. "DelayEgress")
    links:      optional [(a_rid, b_rid)]; None → derive adjacency from the live routes

    Returns a RoutingModel built from each router's live `route show` + `ifconfig show`, with edge
    latency = source egress delay + destination ingress delay (the configured delay VNF)."""
    infos = [(rid, name, query(name, "route show"), query(name, "ifconfig show"))
             for rid, name in routers]

    def latency_of(a, b):
        return hop_latency(parse_delay_base(delay_prop(a, "DelayEgress")),
                           parse_delay_base(delay_prop(b, "DelayIngress")))

    return assemble_model(infos, links, latency_of=latency_of)


def forwarding_tree(model: RoutingModel, root: str) -> TraceResult:
    """The forwarding tree/DAG rooted at `root`, built purely from real next-hops."""
    res = TraceResult(root=root)
    for dest in model.routers:
        if dest == root:
            continue
        target = model.dest_ip.get(dest)
        if not target:
            continue
        edges, reaching, loop, deadend, ecmp = _trace_dest(model, root, target)
        res.edges_used |= edges
        if ecmp:
            res.ecmp.add(dest)
        if reaching:
            # representative path = the reaching branch with the least latency, then fewest hops
            path, lat = min(reaching, key=lambda pl: (pl[1] if pl[1] is not None else 1e18,
                                                      len(pl[0])))
            res.per_dest[dest] = PathResult(dest, "ok", len(path) - 1, lat, path)
            if loop:
                res.loops.add(dest)                       # a loop can coexist with a reaching branch
        elif loop:
            res.loops.add(dest)
            res.per_dest[dest] = PathResult(dest, "loop", 0, None, [])
        else:
            res.deadends.add(dest)
            res.per_dest[dest] = PathResult(dest, "deadend", 0, None, [])
    return res
