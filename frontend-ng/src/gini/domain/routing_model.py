"""Routing model + the AUTHENTIC forwarding trace behind the Network HUD.

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
    # False when this poll could not read the switch (timeout, container gone). Distinct from
    # an empty flow table, which is a real answer meaning "programmed for nothing". Conflating
    # the two is what made the HUD flicker: a slow poll blanked the rules, the lit path went
    # dark, and the changed projection recorded a phantom convergence event.
    reachable: bool = True


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
    # L2 mode only: destinations a switch simply has no rule for yet. Deliberately NOT
    # `deadends` -- a router with no matching route really is a black hole, but a switch
    # that has not been programmed for a MAC is mid-learning, and painting it with the red
    # dead-end ring would accuse the network of a fault it does not have.
    unprogrammed: set = field(default_factory=set)
    # L2 mode only: the switch HAS a rule, but its egress port is not one we were able to
    # match to a neighbour (ovs_port_peers dropped it, or the number is unexpected). Kept
    # apart from every other outcome because the honest answer here is "we do not know",
    # and the tempting wrong answers are both available: calling it delivered, or calling
    # it broken. The HUD lights neither.
    unverified: set = field(default_factory=set)
    # Edges that form a forwarding loop, so the renderer can paint the CYCLE itself red.
    # In L3 the fault shows as a red ring on the unreachable destination router; in L2 the
    # destination is a host MAC and hosts are not drawn, so without this the single most
    # important SDN failure -- rules pointing at each other after a broken spanning tree --
    # would render as an ordinary lit path.
    fault_edges: set = field(default_factory=set)


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
        # A router is multi-homed, so `mac_of` may hold several MACs for one node and the
        # frame is addressed to whichever faces the shared segment. Asking about a single
        # arbitrary MAC would report "not programmed" whenever we happened to pick the
        # interface pointing the other way, so any match counts.
        return any(ovs_egress_port(self.ovs[mid].flows, mac) is not None
                   for mac in self.macs_of(b))

    def macs_of(self, rid: str) -> list:
        """Every MAC known for a node. `mac_of` accepts a bare string or a list per node."""
        v = self.mac_of.get(rid)
        if not v:
            return []
        return [v] if isinstance(v, str) else list(v)


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


# -- OpenFlow port -> neighbour: computed from the topology, VERIFIED against the switch ------- #
# The first compiled link on an ovs is OpenFlow port 2, not 1. The chain: the compiler emits its
# links in order (compiler.py:1032), run_grouter names them tun1, tun2, ... because tun0 is reserved
# for the tap (run_grouter.py:44), the gRouter reads the interface id out of the digits in the device
# name (gnet.c:302), and OpenFlow numbers a port id+1 (openflow_config.c:39). So of_port = index + 2.
_OF_PORT_BASE = 2
# ...and the compiler stamps that same index into the port's MAC (compiler.py:529), which is what
# lets us check the arithmetic against the running switch instead of trusting it.
_OVS_MAC_PREFIX = "02:00:fe:"

_IFACE_ID_MAC = re.compile(r"^\s*(\d+)\s+.*?((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", re.M)


def query_failed(text) -> bool:
    """Whether an `element_query` reply is a FAILURE rather than console output.

    `element_query` never raises: on a timeout, a stopped container, or no workdir it
    returns a sentinel STRING. So `try/except` around it catches nothing, and handing that
    string to a parser yields an empty result that is indistinguishable from a real empty
    table -- which is precisely how a slow poll came to read as "this switch is programmed
    for nothing". Anything that cannot be trusted as console output is a failure here.
    """
    if not text or not str(text).strip():
        return True
    s = str(text).strip().lower()
    return (s.startswith("(query failed") or s.startswith("(no output")
            or s.startswith("(not running") or s.startswith("(unavailable"))


def parse_iface_macs(text: str) -> dict:
    """{interface_id: mac} from the gRouter's `ifconfig show verbose`.

    Plain `ifconfig show` omits the interface id, so the caller must ask for the verbose
    form (gnet.c:272 prints id, state/mode, device, ip, mac, ...).
    """
    return {int(i): mac.lower() for i, mac in _IFACE_ID_MAC.findall(text or "")}


def ovs_port_peers(peers_in_link_order, iface_verbose=None) -> dict:
    """{of_port -> neighbour rid} for one OVS, from the ORDER its links were compiled in.

    This is the single most dangerous number in the Network HUD. Nothing the switch reports
    names its neighbours, so the peer identity has to come from the topology -- and if the
    compiled link order ever differs from the order recomputed here, every L2 hop drawn
    would be confidently wrong rather than obviously broken.

    So the arithmetic is CHECKED, not trusted. The compiler stamps the same index into each
    port's MAC, so an interface's MAC last octet must equal of_port - 1. Pass the switch's
    `ifconfig show verbose` and any port that fails the check -- or that the switch does not
    report at all -- is DROPPED. A dropped port ends the L2 walk quietly (see l2_reach),
    which is the honest outcome: an edge we cannot justify is one we must not draw.

    Verification is skipped when `iface_verbose` is None, for tests and for the offline case.
    """
    computed = {i + _OF_PORT_BASE: peer for i, peer in enumerate(peers_in_link_order)}
    if iface_verbose is None:
        return computed
    macs = parse_iface_macs(iface_verbose)
    out = {}
    for of_port, peer in computed.items():
        mac = macs.get(of_port - 1)                  # OF port N is gnet interface N-1
        if not mac or not mac.startswith(_OVS_MAC_PREFIX):
            continue                                 # not a compiled ovs port: cannot vouch for it
        try:
            stamped = int(mac.rsplit(":", 1)[1], 16)
        except ValueError:
            continue
        if stamped == of_port - 1:
            out[of_port] = peer
    return out


def contract_edges(drawn, links, passthrough) -> list:
    """Adjacency between the nodes the HUD DRAWS, contracting everything else away.

    `derive_edges` infers router adjacency from connected routes, which is exactly right
    when the only thing between two routers is wire or a classic switch. It cannot help
    here: a switch owns no IP, so route data can never place one on the map, and a
    router-to-router edge inferred that way would draw a direct link across a segment that
    actually runs through an OpenFlow switch we mean to show.

    So when there are switches to draw, adjacency comes from the DRAWN TOPOLOGY instead.
    Walk out from each drawn node through `passthrough` devices only -- classic switches and
    hubs, which the design deliberately keeps off the map because their forwarding is
    self-learned and there is no decision to inspect -- and join it to the first drawn node
    on the far side. Machines are NOT passthrough: a host is an endpoint, and walking
    through one would invent a link between two networks that a host merely sits on.

    drawn:       set of rids the HUD draws (routers + ovs)
    links:       [(a_rid, b_rid)] from the topology
    passthrough: set of rids that may be walked THROUGH but are never drawn
    """
    adj: dict = {}
    for a, b in links:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    out, seen = [], set()
    for start in drawn:
        stack, visited = list(adj.get(start, [])), set()
        while stack:
            cur = stack.pop()
            if cur in visited or cur == start:
                continue
            visited.add(cur)
            if cur in drawn:
                key = frozenset((start, cur))
                if key not in seen:
                    seen.add(key)
                    out.append(tuple(sorted((start, cur))))
            elif cur in passthrough:
                stack.extend(adj.get(cur, []))
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
    for info in (ovs_infos or []):
        # 5-tuple (no reachability) stays valid: the switch is assumed readable, which is
        # what every caller that supplies literal CLI text actually means.
        rid, name, entry_text, port_peer, controller = info[:5]
        reachable = info[5] if len(info) > 5 else True
        from .flowtable import parse as parse_flows       # local: keeps import graph flat
        ovs.append(OvsNode(rid, name, parse_flows(entry_text or ""),
                           dict(port_peer or {}), controller, reachable=reachable))
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


def collect_network_data(routers, switches, query, delay_prop, links=None,
                         neighbours_of=None, mac_of=None, topo_links=None,
                         passthrough=None, run_cache=None):
    """Live glue for the Network HUD: routers AND OpenFlow switches in one model.

    Everything beyond `collect_router_data` is about the switches:

    switches:      [(rid, name, controller_rid_or_None)] of the ovs-role devices
    neighbours_of: callable(ovs_rid) -> [peer_rid] in the order that ovs's links were
                   COMPILED, which is what fixes the OpenFlow port numbers (ovs_port_peers)
    mac_of:        {rid: mac} so an L3 hop can ask a switch whether its destination is
                   programmed; the compiler's address map already carries a mac per interface
    run_cache:     a dict the caller keeps for the lifetime of the RUN; holds the static
                   port map and the last good flow dump per switch (see below)

    A switch that cannot be READ this poll is marked `reachable=False` and carries no flows,
    rather than being drawn as a switch programmed for nothing. `element_query` reports
    failure as a string rather than an exception, so that check is `query_failed`, not
    `try/except` -- which catches nothing here.

    The port map is CACHED. Port wiring cannot change while a topology runs, so asking every
    switch for `ifconfig show verbose` on every poll doubled the number of serial
    `docker compose exec` round trips for an answer that is constant -- and every extra call
    is another chance to time out and blank the picture.
    """
    infos = [(rid, name, query(name, "route show"), query(name, "ifconfig show"))
             for rid, name in routers]

    def latency_of(a, b):
        return hop_latency(parse_delay_base(delay_prop(a, "DelayEgress")),
                           parse_delay_base(delay_prop(b, "DelayIngress")))

    cache = run_cache if run_cache is not None else {}
    ports = cache.setdefault("ports", {})         # {rid: port_peer} — static for the run
    flows = cache.setdefault("flows", {})         # {rid: last GOOD `openflow entry all` text}
    ovs_infos = []
    for rid, name, controller in (switches or []):
        try:
            entries = query(name, "openflow entry all")
        except Exception:                         # a query callable that raises, defensively
            entries = ""
        if rid not in ports:
            try:
                verbose = query(name, "ifconfig show verbose")
            except Exception:
                verbose = ""
            if not query_failed(verbose):         # only cache a mapping we could verify
                ports[rid] = ovs_port_peers(
                    list(neighbours_of(rid)) if neighbours_of else [], verbose)
        peers = ports.get(rid, {})
        if query_failed(entries):
            # CARRY THE LAST KNOWN FLOWS FORWARD. Showing the switch as suddenly empty was
            # the flicker: the lit path went dark and the changed projection wrote a phantom
            # convergence tick. Its real rules did not vanish because we failed to read them
            # -- so keep the last answer and mark the node stale, which the HUD draws
            # differently so the picture never silently passes off old data as fresh.
            ovs_infos.append((rid, name, flows.get(rid, ""), peers, controller, False))
        else:
            flows[rid] = entries
            ovs_infos.append((rid, name, entries, peers, controller, True))

    # With switches on the map, adjacency must come from the drawn topology (contract_edges);
    # route-derived adjacency would draw a router-to-router line straight across them.
    if ovs_infos and links is None and topo_links is not None:
        drawn = {rid for rid, _ in routers} | {i[0] for i in ovs_infos}
        links = contract_edges(drawn, topo_links, set(passthrough or ()))

    return assemble_model(infos, links, latency_of=latency_of,
                          ovs_infos=ovs_infos, mac_of=mac_of)


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
        # An unreachable switch is included on its CARRIED-FORWARD flows (see the flow cache
        # in collect_network_data), which is what keeps its projection identical across a
        # failed poll. Omitting it instead would not help: absent-then-present is itself a
        # change, so the phantom convergence tick would simply move.
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


def flow_activity(model: RoutingModel) -> int:
    """Total rules installed across every switch — the flow table's ACTIVITY, not its effect.

    Deliberately separate from `model_signature`. The signature is the forwarding
    projection (destination MAC -> egress port), so it is blind to how many rules produce
    it: with `from_packet` matching, twenty-two microflows to one destination out one port
    are the same forwarding as two. That blindness is right for the scrub -- replaying
    identical forwarding twice would be meaningless -- but it made the timeline drop the
    controller's work entirely. A flow count moving 4 -> 22 -> 4 recorded ONE change point.

    So the two are recorded side by side: what the network DOES (signature) and what the
    controller is DOING to it (this).
    """
    return sum(len(n.flows or []) for n in getattr(model, "ovs", {}).values())


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
        # [(t, flow_count)] whenever the COUNT moves — the controller programming or the
        # switches ageing rules out, which the signature is blind to (see flow_activity).
        # Two ints per event, so keeping this at full poll resolution costs nothing.
        self.activity: list = []
        self._last_flows = None

    def __len__(self) -> int:
        return len(self.snaps)

    @property
    def t_start(self) -> float:
        return self.snaps[0][0] if self.snaps else 0.0

    def change_times(self) -> list:
        return [t for t, _ in self.snaps]

    def push(self, model: RoutingModel, tnow: float) -> bool:
        """Record the state at `tnow`. Returns True if it was a CHANGE (new snapshot).

        Flow-table activity is recorded separately and unconditionally, because it moves on
        polls where forwarding does not (see flow_activity).
        """
        self.t_end = max(self.t_end, tnow)
        n = flow_activity(model)
        if n != self._last_flows:
            self.activity.append((tnow, n))
            self._last_flows = n
            cut = tnow - self.RETAIN_S
            while len(self.activity) > 1 and self.activity[0][0] < cut:
                self.activity.pop(0)
            if len(self.activity) > self.MAXSNAPS * 4:
                del self.activity[:-self.MAXSNAPS * 4]
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
        self.activity.clear(); self._last_flows = None


def l2_reach(model: RoutingModel, root: str) -> TraceResult:
    """L2 reachability across an SDN fabric, rooted at an OVS.

    The second trace mode. In a HYBRID network the L3 trace does the work and a switch is
    merely transit (see expand_hop) -- but a pure-SDN topology has no routers to be
    endpoints and no hosts drawn as nodes, so that mode has nothing to say. Here the
    switch's MAC decision IS the forwarding decision: for every destination MAC it has a
    rule for, walk egress port -> neighbour switch -> that switch's rule, and so on.

    Note this does not contradict "a switch never chooses the next hop": that was about
    L3. Within one L2 fabric, choosing the egress port for a MAC is exactly what a switch
    decides, and here it is read from the switch's real flow table.

    Destinations are MACs, so `per_dest` is keyed by MAC, not by node id.
    """
    res = TraceResult(root=root)
    if root not in model.ovs:
        return res
    for mac in sorted(_known_macs(model)):
        seen: list = [root]
        cur, status = root, "unprogrammed"
        while True:
            port = ovs_egress_port(model.ovs[cur].flows, mac)
            if port is None:                       # no rule here (yet) -- stop quietly
                break
            peers = model.ovs[cur].port_peer
            if port not in peers:
                status = "unverified"              # a rule we cannot follow -- say so, don't guess
                break
            nxt = peers[port]
            if nxt is None or nxt not in model.ovs:
                status = "ok"                      # egress leaves the fabric: delivered to a host
                break
            res.edges_used.add((cur, nxt))
            if nxt in seen:                        # a programmed loop -- the teaching moment
                status = "loop"
                cycle = seen[seen.index(nxt):] + [nxt]      # close it back on itself
                res.fault_edges |= {(a, b) for a, b in zip(cycle, cycle[1:])}
                break
            seen.append(nxt)
            cur = nxt
        res.per_dest[mac] = PathResult(dest=mac, status=status,
                                       hop_count=max(0, len(seen) - 1),
                                       total_latency=None, path=list(seen))
        if status == "loop":
            res.loops.add(mac)
        elif status == "unprogrammed":
            res.unprogrammed.add(mac)
        elif status == "unverified":
            res.unverified.add(mac)
    return res


def trace(model: RoutingModel, root: str) -> TraceResult:
    """Trace from `root` in whichever mode the root implies.

    The MODE FOLLOWS THE ROOT, so the HUD needs no mode switch: long-press a router and you
    get the L3 forwarding tree (switches appearing as transit); long-press a switch and you
    get L2 reachability across its fabric. A pure-SDN network simply has no routers to
    pick, so it always lands in L2 mode.
    """
    return l2_reach(model, root) if root in model.ovs else forwarding_tree(model, root)


def _known_macs(model: RoutingModel) -> set:
    """Every destination MAC any switch in the fabric has a rule for."""
    macs: set = set()
    for n in model.ovs.values():
        for f in n.flows or []:
            mac = (f.match or {}).get("Ethernet destination MAC address")
            if mac:
                macs.add(mac.strip().lower())
    return macs


def decision_kind(model: RoutingModel, src: str) -> str:
    """How the node at the SOURCE of an edge made its decision -- the edge's colour.

    An L3 hop that crosses a switch renders as two sub-edges, and each takes the colour of
    whoever decided it: R1->OVS1 is `computed` (R1's route table chose the next hop) and
    OVS1->R2 is `programmed` (the flow rule chose the egress port). Colouring the whole hop
    one way would hide that two different kinds of decision were involved.
    """
    return "programmed" if src in model.ovs else "computed"


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
