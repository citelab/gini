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


# Node kinds. The HUD draws a circle for a router and a square for an OVS, and colours a
# lit hop by HOW the decision was made -- "computed" (longest-prefix) vs "programmed" (an
# installed flow rule). Classic switches are deliberately NOT nodes: they collapse into
# the edge (see derive_edges), because their forwarding is self-learned and there is no
# decision to inspect.
KIND_ROUTER = "router"
KIND_OVS = "ovs"


@dataclass
class RouterNode:
    rid: str
    name: str
    ips: set                      # every interface IP this router owns
    table: list                   # [RouteEntry] — the router's REAL table
    kind: str = KIND_ROUTER


@dataclass
class OvsNode:
    """An OpenFlow switch on the path.

    An OVS is NOT an L3 decision-maker and is not traced like one. A router picks the next
    hop and addresses the frame to that router's MAC; the switch in between only carries
    it. So an OVS appears in the model as a TRANSIT node that splits an L3 hop
    (R1--OVS1--R2 instead of R1--R2), and its flow table answers one narrower question:
    can it carry this hop -- is there a rule programmed for that destination MAC?

    `port_peer` maps an OpenFlow port number to the neighbour on that port. The model
    cannot derive it (it is a fact about the drawn topology, not about any device's
    state), so the live glue supplies it -- which also keeps this module pure and
    testable with fake CLI text.
    """
    rid: str
    name: str
    flows: list                   # [FlowEntry] — the switch's REAL flow table
    port_peer: dict = field(default_factory=dict)   # {of_port:int -> neighbour rid}
    controller: str | None = None                   # rid of the OFC programming it, if any
    kind: str = KIND_OVS


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


def ovs_egress_port(flows, dest_mac: str):
    """The port an OVS would send a frame for `dest_mac` out of, per its REAL flow table.

    Only an EXPLICIT destination-MAC match counts. A wildcard rule (the boot-time
    match-all -> NORMAL default, say) forwards the frame but says nothing about this
    destination having been programmed, and reporting it as a definite next hop would
    invent precision the switch does not have. Likewise an action of flood/controller/
    normal is not a definite egress port.

    Returns int port, or None for "no rule for this destination yet" -- which is NOT the
    same as unreachable. A switch that has not learned a destination is mid-learning, and
    the HUD must not render it with the router side's red dead-end ring.
    """
    if not dest_mac:
        return None
    want = dest_mac.strip().lower()
    best_prio, best_port = None, None
    for f in flows or []:
        mac = (f.match or {}).get("Ethernet destination MAC address")
        if not mac or mac.strip().lower() != want:
            continue
        port = None
        for a in f.actions or []:
            if str(a).startswith("output:"):
                try:
                    port = int(str(a).split(":", 1)[1])
                except ValueError:
                    port = None
                break
        if port is None:
            continue
        prio = f.priority if f.priority is not None else 0
        if best_prio is None or prio > best_prio:
            best_prio, best_port = prio, port
    return best_port


class RoutingModel:
    """A snapshot of the whole network's routing state."""

    def __init__(self, routers, edges, dest_ip=None, ovs=None, mac_of=None) -> None:
        self.routers: dict = {r.rid: r for r in routers}
        # OVS transit nodes, keyed like routers but held separately: forwarding_tree()
        # iterates `routers` to pick TARGETS, and a switch is never a target.
        self.ovs: dict = {n.rid: n for n in (ovs or [])}
        self.nodes: dict = {**self.routers, **self.ovs}     # everything the HUD draws
        # rid -> MAC to aim at when an L2 hop has to be resolved. Supplied by the glue from
        # the compiler's address map, which already carries both ip and mac per interface.
        self.mac_of: dict = dict(mac_of or {})
        self.edges: list = list(edges)
        self.ip_owner: dict = {ip: r.rid for r in routers for ip in r.ips}
        self._lat: dict = {frozenset((e.a, e.b)): e.latency_ms for e in self.edges}
        # a representative IP to aim at when reaching each router
        self.dest_ip: dict = dict(dest_ip or {})
        # routers whose target IP the CALLER pinned: the trace aims at exactly that IP.
        # Unpinned routers let forwarding_tree try every interface and pick the nearest.
        self.pinned_dest: set = set((dest_ip or {}).keys())
        for r in routers:
            self.dest_ip.setdefault(r.rid, next(iter(sorted(r.ips)), None))

    def edge_latency(self, a: str, b: str):
        return self._lat.get(frozenset((a, b)))

    def neighbors(self, rid: str) -> list:
        return [(e.b if e.a == rid else e.a) for e in self.edges if rid in (e.a, e.b)]

    def via(self, a: str, b: str):
        """The OVS sitting between routers `a` and `b`, if the hop crosses one.

        When the segment joining two routers carries an OVS, the glue supplies links as
        a--ovs and ovs--b rather than a--b, so the switch is the common OVS neighbour.
        """
        if not self.ovs:
            return None
        na = {n for n in self.neighbors(a) if n in self.ovs}
        for n in self.neighbors(b):
            if n in na:
                return n
        return None

    def expand_hop(self, a: str, b: str) -> list:
        """One L3 hop as the sub-edges the HUD should light: [(a,b)] normally, or
        [(a,ovs),(ovs,b)] when it crosses an SDN segment."""
        mid = self.via(a, b)
        return [(a, b)] if mid is None else [(a, mid), (mid, b)]

    def hop_carried(self, a: str, b: str):
        """Whether the OVS on this hop has a rule PROGRAMMED for the destination.

        Returns None when the hop crosses no OVS (nothing to say), True/False otherwise.
        False means "not programmed yet", not "unreachable" -- see ovs_egress_port.
        """
        mid = self.via(a, b)
        if mid is None:
            return None
        return ovs_egress_port(self.ovs[mid].flows, self.mac_of.get(b)) is not None


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
            # Light the hop as the sub-edges it is actually made of: a hop across an SDN
            # segment becomes router--ovs--router, so the switch shows as a transit node
            # rather than the hop pretending to be a direct link.
            edges.update(model.expand_hop(cur, owner))
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


def assemble_model(router_infos, links=None, latency_of=None,
                   ovs_infos=None, mac_of=None) -> RoutingModel:
    """Build a RoutingModel from per-router CLI text.

    router_infos: [(rid, name, route_show_text, ifconfig_show_text)]
    links:        [(a_rid, b_rid)] to draw; None → derive adjacency from connected routes (handles
                  switch-mediated segments)
    latency_of:   optional callable(a_rid, b_rid) -> float|None  (configured delay-VNF latency)
    ovs_infos:    [(rid, name, openflow_entry_text, port_peer, controller_rid)] — SDN switches to
                  draw as transit nodes. Requires `links` to route through them
                  (a--ovs, ovs--b), since a switch owns no IP for adjacency to be derived from.
    mac_of:       {rid: mac} used to ask an OVS whether a hop's destination is programmed.
    """
    routers = [RouterNode(rid, name, parse_iface_ips(iface_text), parse_routes(route_text))
               for (rid, name, route_text, iface_text) in router_infos]
    ovs = []
    for rid, name, entry_text, port_peer, controller in (ovs_infos or []):
        from .flowtable import parse as parse_flows       # local: keeps import graph flat
        ovs.append(OvsNode(rid, name, parse_flows(entry_text or ""),
                           dict(port_peer or {}), controller))
    pairs = list(links) if links is not None else [(e.a, e.b) for e in derive_edges(routers)]
    edges = [Edge(a, b, latency_of(a, b) if latency_of else None) for (a, b) in pairs]
    return RoutingModel(routers, edges, ovs=ovs, mac_of=mac_of)


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


# -- P4: convergence recording (pure; the HUD scrub renders straight off this) ------------------ #
def model_signature(model: RoutingModel) -> tuple:
    """A hashable fingerprint of the network's forwarding state: every router's table rows
    (plus its interface IPs), and every OVS's FORWARDING PROJECTION.

    The projection matters. A flow table also carries packet counters, byte counts, ages
    and timeouts, all of which change on every single poll. Hashing the raw table would
    make every refresh look like a convergence event, so RouteHistory would record a
    snapshot each time and the scrub timeline would fill with noise until it was useless.
    Only (destination MAC -> egress port) is a forwarding fact; everything else is
    telemetry. Two models with the same signature forward identically.
    """
    sig = []
    for rid in sorted(model.routers):
        r = model.routers[rid]
        rows = tuple(sorted((e.network, e.netmask, e.nexthop, e.iface,
                             getattr(e, "origin", "")) for e in r.table))
        sig.append((rid, tuple(sorted(r.ips)), rows))
    for rid in sorted(getattr(model, "ovs", {})):
        n = model.ovs[rid]
        proj = set()
        for f in n.flows or []:
            mac = (f.match or {}).get("Ethernet destination MAC address")
            if not mac:
                continue                      # wildcard rules say nothing per-destination
            for a in f.actions or []:
                if str(a).startswith("output:"):
                    proj.add((mac.strip().lower(), str(a)))
                    break
        sig.append((rid, tuple(sorted(proj))))
    return tuple(sig)


class RouteHistory:
    """A ring buffer of RoutingModel snapshots for the convergence replay.

    `push(model, tnow)` records a snapshot only when the routing state CHANGED (same
    signature → just advance the live edge), so a converged network costs one entry no
    matter how long it sits still — and every entry is a genuine convergence event.
    `at(t)` returns the model that was in force at time t (latest snapshot ≤ t).
    """
    RETAIN_S = 600.0          # keep the last 10 minutes of convergence events
    MAXSNAPS = 300            # hard cap (each snapshot is one poll's parsed tables)

    def __init__(self) -> None:
        self.snaps: list = []          # [(t, model)] — t = when this state FIRST appeared
        self.t_end: float = 0.0        # the live edge (last time any state was observed)
        self._last_sig = None

    def __len__(self) -> int:
        return len(self.snaps)

    @property
    def t_start(self) -> float:
        return self.snaps[0][0] if self.snaps else 0.0

    def change_times(self) -> list:
        return [t for t, _ in self.snaps]

    def push(self, model: RoutingModel, tnow: float) -> bool:
        """Record the state at `tnow`. Returns True if it was a CHANGE (new snapshot)."""
        self.t_end = max(self.t_end, tnow)
        sig = model_signature(model)
        if sig == self._last_sig and self.snaps:
            return False                              # unchanged — just extend the live edge
        self._last_sig = sig
        self.snaps.append((tnow, model))
        cutoff = tnow - self.RETAIN_S                 # ring: drop events older than RETAIN_S,
        while len(self.snaps) > 1 and self.snaps[0][0] < cutoff:
            self.snaps.pop(0)                         # but always keep the oldest survivor
        if len(self.snaps) > self.MAXSNAPS:
            del self.snaps[:-self.MAXSNAPS]
        return True

    def at(self, t: float) -> RoutingModel | None:
        """The model in force at time t: the latest snapshot with snap_t <= t."""
        best = None
        for st, m in self.snaps:
            if st <= t:
                best = m
            else:
                break
        return best if best is not None else (self.snaps[0][1] if self.snaps else None)

    def latest(self) -> RoutingModel | None:
        return self.snaps[-1][1] if self.snaps else None

    def clear(self) -> None:
        self.snaps.clear(); self._last_sig = None; self.t_end = 0.0


def forwarding_tree(model: RoutingModel, root: str) -> TraceResult:
    """The forwarding tree/DAG rooted at `root`, built purely from real next-hops.

    A router is multi-homed, so we trace toward EACH of a destination's interface IPs and
    keep the nearest (fewest-hop) reaching branch. Aiming only at one representative IP —
    e.g. an interface that faces a *different* neighbour — would draw a longer path than
    forwarding actually takes and hide a direct link. Trying every interface makes a
    directly-connected neighbour show its direct edge, so the tree renders honestly.
    """
    res = TraceResult(root=root)
    for dest in model.routers:
        if dest == root:
            continue
        if dest in getattr(model, "pinned_dest", ()):     # caller pinned a specific target IP
            t = model.dest_ip.get(dest)
            targets = [t] if t else []
        else:
            targets = sorted(model.routers[dest].ips)
            if not targets:
                t = model.dest_ip.get(dest)
                targets = [t] if t else []
        if not targets:
            continue
        best = None                # (sort_key, edges, path, lat, loop, deadend, ecmp)
        for target in targets:
            edges, reaching, loop, deadend, ecmp = _trace_dest(model, root, target)
            if reaching:
                # representative branch for THIS interface: least latency, then fewest hops
                path, lat = min(reaching, key=lambda pl: (pl[1] if pl[1] is not None else 1e18,
                                                          len(pl[0])))
                # choose the interface by fewest hops first (a direct link wins), then latency
                key = (0, len(path) - 1, lat if lat is not None else 1e18)
            else:
                path, lat = [], None
                key = (1, 0 if loop else 1, 0.0)          # prefer a loop over a dead-end if nothing reaches
            cand = (key, edges, path, lat, loop, deadend, ecmp)
            if best is None or cand[0] < best[0]:
                best = cand
        _key, edges, path, lat, loop, deadend, ecmp = best
        res.edges_used |= edges
        if ecmp:
            res.ecmp.add(dest)
        if path:                                          # reached via its nearest interface
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
