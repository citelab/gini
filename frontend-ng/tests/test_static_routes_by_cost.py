"""Static routes follow the cheapest path, and an unweighted lab compiles exactly as before.

Static routing used to be a breadth-first search over routers: fewest hops. With link costs
(docs/design/link-properties.md) it is Dijkstra over cost, where crossing from router A to a
neighbour costs A's link onto the shared segment -- what a distance-vector protocol adds too.

Two promises, each checked on hundreds of generated topologies rather than a hand-picked few:

1. With every cost 1, the new routes are IDENTICAL to the old search's -- same next hop, same
   interface, for every destination on every router. The old search is kept below, verbatim
   from before the change, as the reference. No existing lab silently changes its routes.
2. With random costs, following the compiled routes hop by hop -- the way a packet would -- from
   every router to every subnet always arrives, never loops, and costs exactly the minimum, which
   is computed independently (Floyd-Warshall) from the interfaces' costs.
"""
import random

import pytest

from gini.domain import link_props as LP
from gini.domain.topology import Topology
from gini.services.compiler import RuntimeCompiler

INF = float("inf")


# ---- the reference: the breadth-first search static routing used to be ----------- #
def _bfs_static_routes(cfg, spec_of, rtr_seg_ip, rtr_seg_dev, seg_routers,
                       gw_seg=None, gw_ip=None, extra_nets=None, rtr_seg_cost=None) -> None:
    """extra_nets: [(cidr, seg, via_ip)] — destinations that are not GINI subnets but
    hang off a node ON `seg` (a routed-mode GINI32 board's physical subnet). Routers on
    that segment route to them via `via_ip`; others hop toward a router that is."""
    import ipaddress
    from collections import deque

    routers = list(spec_of.keys())
    # router adjacency: two routers are neighbours if they share a segment (a
    # router-to-router link), which gives the gateway IPs on that link.
    adj: dict = {d: {} for d in routers}
    for _seg, rtrs in seg_routers.items():
        for a in rtrs:
            for b in rtrs:
                if a != b:
                    adj[a][b] = _seg

    for did in routers:
        my_segs = {seg for (d, seg) in rtr_seg_ip if d == did}
        # BFS: first-hop neighbour toward every reachable router
        dist = {did: 0}
        firsthop: dict = {did: None}
        q = deque([did])
        while q:
            cur = q.popleft()
            for nb in adj[cur]:
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    firsthop[nb] = nb if cur == did else firsthop[cur]
                    q.append(nb)
        routes = []
        for seg, cidr in cfg.subnets.items():
            if seg in my_segs:
                continue                                   # directly connected
            cand = [c for c in seg_routers.get(seg, []) if c in dist and c != did]
            if not cand:
                continue                                   # unreachable from here
            best = min(cand, key=lambda c: (dist[c], str(c)))
            nh = firsthop[best]
            if nh is None:
                continue
            shared = adj[did][nh]
            net = ipaddress.ip_network(cidr)
            routes.append({"net": str(net.network_address),
                           "mask": str(net.netmask),
                           "gw": rtr_seg_ip[(nh, shared)],
                           "dev": rtr_seg_dev[(did, shared)]})

        # subnets living behind a routed-mode GINI32 board (real devices on its radio)
        for cidr, bseg, via_ip in (extra_nets or []):
            try:
                net = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            if bseg in my_segs:                            # board is on my segment
                routes.append({"net": str(net.network_address),
                               "mask": str(net.netmask), "gw": via_ip,
                               "dev": rtr_seg_dev[(did, bseg)]})
            else:                                          # hop toward its router
                cand = [c for c in seg_routers.get(bseg, []) if c in dist and c != did]
                if not cand:
                    continue
                nh = firsthop[min(cand, key=lambda c: (dist[c], str(c)))]
                if nh is None:
                    continue
                shared = adj[did][nh]
                routes.append({"net": str(net.network_address),
                               "mask": str(net.netmask),
                               "gw": rtr_seg_ip[(nh, shared)],
                               "dev": rtr_seg_dev[(did, shared)]})

        # default route (0.0.0.0/0) toward the Internet/NAT gateway, so internet-
        # bound traffic leaves the lab through the drawn Internet element.
        if gw_seg is not None and gw_ip:
            if gw_seg in my_segs:                          # gateway is on my segment
                routes.append({"net": "0.0.0.0", "mask": "0.0.0.0", "gw": gw_ip,
                               "dev": rtr_seg_dev[(did, gw_seg)]})
            else:                                          # hop toward its router
                cand = [c for c in seg_routers.get(gw_seg, [])
                        if c in dist and c != did]
                if cand:
                    best = min(cand, key=lambda c: (dist[c], str(c)))
                    nh = firsthop[best]
                    if nh is not None:
                        shared = adj[did][nh]
                        routes.append({"net": "0.0.0.0", "mask": "0.0.0.0",
                                       "gw": rtr_seg_ip[(nh, shared)],
                                       "dev": rtr_seg_dev[(did, shared)]})
        spec_of[did].routes = routes

class _BfsCompiler(RuntimeCompiler):
    """The compiler with its old route computation put back."""
    _add_static_routes = staticmethod(_bfs_static_routes)


# ---- topologies ---------------------------------------------------------------------- #
def _random_lab(rng: random.Random, weighted: bool) -> Topology:
    t = Topology("gen")
    n = rng.randint(2, 7)
    routers = [t.add_device("router", f"R{i}") for i in range(n)]

    def wire(a, b):
        if rng.random() < 0.3:                      # through a switch, sometimes shared
            s = t.add_device("switch")
            t.add_link(a.id, s.id)
            t.add_link(b.id, s.id)
            if n > 2 and rng.random() < 0.4:
                c = rng.choice([r for r in routers if r not in (a, b)])
                t.add_link(c.id, s.id)
        else:
            t.add_link(a.id, b.id)

    for i in range(1, n):                           # connected...
        wire(routers[i], routers[rng.randrange(i)])
    for _ in range(rng.randint(0, n)):              # ...with loops
        a, b = rng.sample(routers, 2)
        wire(a, b)
    for r in routers:                               # and hosts to route to
        for _ in range(rng.randint(0, 2)):
            t.add_link(t.add_device("host").id, r.id)
    if weighted:
        for l in t.links.values():
            if LP.is_costed(t, l):
                LP.set_prop(l, "cost", rng.randint(1, 15))
    return t


def _routes(cfg) -> dict:
    return {r.name: [(x["net"], x["mask"], x["gw"], x["dev"]) for x in r.routes]
            for r in cfg.routers}


# ---- 1. unweighted: identical to the old search ------------------------------------ #
@pytest.mark.parametrize("seed", range(300))
def test_unit_costs_route_exactly_as_the_breadth_first_search_did(seed):
    t = _random_lab(random.Random(seed), weighted=False)
    assert _routes(RuntimeCompiler().compile(t)) == _routes(_BfsCompiler().compile(t))


# ---- 2. weighted: walking the routes costs the minimum ----------------------------- #
def _walk_all(t, cfg):
    """Follow the compiled routes from every router to every subnet; compare with the optimum."""
    import ipaddress
    links = t.links
    rtr = {r.name: r for r in cfg.routers}
    by_ip, nets_of, cost_on = {}, {}, {}           # ip -> router; router -> {net: tun}; cost
    for r in cfg.routers:
        nets_of[r.name] = {}
        for i, itf in enumerate(r.ifaces, start=1):
            iface = ipaddress.ip_interface(itf.ip)
            by_ip[str(iface.ip)] = r.name
            nets_of[r.name][str(iface.network.network_address)] = i
            cost_on[(r.name, i)] = LP.get(links[itf.link_id], LP.COST)
    names = list(rtr)
    # independent optimum: Floyd-Warshall, u->v over a shared net costs u's interface there
    dist = {u: {v: (0 if u == v else INF) for v in names} for u in names}
    for u in names:
        for v in names:
            if u != v:
                for net, i in nets_of[u].items():
                    if net in nets_of[v]:
                        dist[u][v] = min(dist[u][v], cost_on[(u, i)])
    for k in names:
        for u in names:
            for v in names:
                if dist[u][k] + dist[k][v] < dist[u][v]:
                    dist[u][v] = dist[u][k] + dist[k][v]
    checked = 0
    for start in names:
        for cidr in cfg.subnets.values():
            net = str(ipaddress.ip_network(cidr).network_address)
            best = min((dist[start][r] for r in names if net in nets_of[r]), default=INF)
            cur, spent, seen = start, 0, set()
            while net not in nets_of[cur]:
                assert cur not in seen, f"routing loop toward {net} at {cur}"
                seen.add(cur)
                rt = next((x for x in rtr[cur].routes if x["net"] == net), None)
                if rt is None:
                    assert best == INF, f"{cur} has no route to {net}, which is reachable"
                    break
                spent += cost_on[(cur, rt["dev"])]
                cur = by_ip[rt["gw"]]
            else:
                assert spent == best, (f"{start} -> {net} costs {spent} along the routes, "
                                       f"but {best} is possible")
            checked += 1
    return checked


@pytest.mark.parametrize("seed", range(300))
def test_weighted_routes_cost_the_minimum_when_walked(seed):
    t = _random_lab(random.Random(1000 + seed), weighted=True)
    assert _walk_all(t, RuntimeCompiler().compile(t)) > 0


def test_the_classic_triangle_takes_the_cheaper_longer_path():
    """R1-R2 direct costs 10; R1-R3-R2 costs 2 + 3 = 5. Static routing must take the detour."""
    t = Topology("tri")
    r1, r2, r3 = (t.add_device("router", n) for n in ("R1", "R2", "R3"))
    h2 = t.add_device("host", "H2")
    direct = t.add_link(r1.id, r2.id)
    left, right = t.add_link(r1.id, r3.id), t.add_link(r3.id, r2.id)
    t.add_link(h2.id, r2.id)
    cfg = RuntimeCompiler().compile(t)
    r1_cfg = next(r for r in cfg.routers if r.name == "R1")
    plain = {x["net"]: x["gw"] for x in r1_cfg.routes}
    for l, c in ((direct, 10), (left, 2), (right, 3)):
        LP.set_prop(l, "cost", c)
    r1_w = next(r for r in RuntimeCompiler().compile(t).routers if r.name == "R1")
    weighted = {x["net"]: x["gw"] for x in r1_w.routes}
    r3_ip = next(i.ip.split("/")[0] for i in next(r for r in cfg.routers if r.name == "R3").ifaces
                 if i.link_id == left.id)
    r2_ip = next(i.ip.split("/")[0] for i in next(r for r in cfg.routers if r.name == "R2").ifaces
                 if i.link_id == direct.id)
    h2_subnet = "10.0.4.0"                      # H2's LAN: the fourth link drawn
    assert plain[h2_subnet] == r2_ip            # unweighted: straight across
    assert weighted[h2_subnet] == r3_ip         # weighted: around, through R3
