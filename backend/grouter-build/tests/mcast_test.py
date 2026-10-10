#!/usr/bin/env python3
"""End-to-end: multicast with link costs and failures, in mcast_tree.lua and in the router's own
forwarder.

Multicast used to be right only on a network without loops -- which is exactly the network link
costs and failures are not interesting on. mcast_tree.lua copied every datagram down every branch
it had heard a join on and never decremented the TTL, so the weighted triangle delivered two copies
of everything and most five-router meshes forwarded one datagram round a cycle FOREVER (tens of
thousands of copies a second, measured). The built-in forwarder (`gpipe mcast join`) had the same
hole whenever joins formed a cycle. Both now do reverse-path forwarding: a copy is accepted only
from the interface that leads back to its source, by the unicast routes -- so the tree is the
cheapest-path tree, and it moves when RIP moves.

The weighted triangle of cost_test.py, H1 sending to 239.1.1.1 and H2 a member. The path a
datagram took is read from its TTL on arrival, as cost_test does with pings: H1 sends at 64, so 61
through R3, 62 straight across.

            R1 ---- cost 10 ---- R2 -- H2
   H1 -- R1   \\                /
               cost 2      cost 3
                  \\        /
                     R3

mcast_tree.lua on every router:
  [1] a line H1-R1-R2-H2, H2 joining with IGMPv3 as Linux first does: one copy, and each
      router hop takes one off the TTL (62)
  [2] the triangle: exactly ONE copy, along the cheap detour (61); the direct link is pruned
  [3] the same triangle with the direct link at cost 1: one copy, straight across (62)
  [4] random five-router meshes: one copy each, and nothing still circulating afterwards
  [5] RIP on every router: the cheap link fails, RIP moves, and multicast follows to the direct
      link (62) within seconds, one copy at a time; repaired, it returns to the detour (61)
  [6] static routes: the cheap link fails and multicast from H1 stops -- R2's route back to H1
      still says "via R3", so the copy R1 can still send straight across fails the reverse-path
      check (the routes do not move, so neither does the tree: the unicast lesson); repaired, it
      resumes
  [7] soft state: a member that goes silent expires and its branch is pruned; joining again
      grafts it back at once
  [8] a link that fails and recovers is pruned again if nobody behind it wants the group

the built-in forwarder (`gpipe mcast join` on every link, both ways -- the worst case):
  [9]  the triangle: one copy along the detour (61), the other refused by the reverse-path check
       (`watch` says "rpf")
  [10] with RIP, the cheap link fails: the built-in forwarder follows to the direct link (62)

  GROUTER_BIN=/path/to/grouter python3 mcast_test.py
"""
import os
import random
import re
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in ("../../../frontend-ng/src", "../../../core/src"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, _cand)))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import gini_tun as g                                  # noqa: E402
from gini_tun import Host                             # noqa: E402
from cost_test import RIP, Lab, topology              # noqa: E402
from gini.domain import link_faults                   # noqa: E402
from gini.domain import link_props as LP              # noqa: E402
from gini.domain.topology import Topology             # noqa: E402

MT = os.path.abspath(os.path.join(HERE, "../../../frontend-ng/src/gini/data/examples/"
                                        "mcast_tree.lua"))
GRP = "239.1.1.1"


def mcast_mac(group):
    b = g.ip2b(group)
    return "01:00:5e:%02x:%02x:%02x" % (b[1] & 0x7f, b[2], b[3])


class Member(Host):
    """An IGMPv2 host, as a Linux receiver with force_igmp_version=2 behaves: it reports when it
    joins, answers every general query for the groups it is in, and sends a leave. It records each
    multicast datagram it receives as (ident, ttl)."""

    def __init__(self, *a):
        self.groups, self.answer, self.got = set(), True, []
        super().__init__(*a)

    def _igmp(self, kind, group):
        b = struct.pack("!BBH4s", kind, 0, 0, g.ip2b(group))
        b = struct.pack("!BBH4s", kind, 0, g._cksum(b), g.ip2b(group))
        dst = "224.0.0.2" if kind == 0x17 else group
        self._send(g.eth(mcast_mac(dst), self.mac, g.ETH_IP, g.ip_pkt(self.ip, dst, 1, 2, b)))

    def join(self, group):
        self.groups.add(group)
        self._igmp(0x16, group)

    def join_v3(self, group):
        """Join the way Linux does before it has heard a (v2) query: one IGMPv3 report with a
        CHANGE_TO_EXCLUDE({}) record -- "everyone may send me this group"."""
        self.groups.add(group)
        rec = struct.pack("!BBH4s", 4, 0, 0, g.ip2b(group))
        b = struct.pack("!BBHHH", 0x22, 0, 0, 0, 1) + rec
        b = struct.pack("!BBHHH", 0x22, 0, g._cksum(b), 0, 1) + rec
        self._send(g.eth(mcast_mac("224.0.0.22"), self.mac, g.ETH_IP,
                         g.ip_pkt(self.ip, "224.0.0.22", 1, 2, b)))

    def leave(self, group):
        self.groups.discard(group)
        self._igmp(0x17, group)

    def send_to(self, group, ident, ttl=64):
        self._send(g.eth(mcast_mac(group), self.mac, g.ETH_IP,
                         g.ip_pkt(self.ip, group, ttl, 1, g.icmp(8, ident, 1, b"mc"))))

    def copies(self, ident):
        return [ttl for i, ttl in self.got if i == ident]

    def _handle(self, data):
        if len(data) >= 34 and data[12:14] == b"\x08\x00":
            body = data[14:]
            ihl, proto, dst = (body[0] & 0x0f) * 4, body[9], g.b2ip(body[16:20])
            if proto == 2 and body[ihl] == 0x11 and self.answer:
                for grp in list(self.groups):
                    self._igmp(0x16, grp)
            elif proto == 1 and dst == GRP and len(body) >= ihl + 8:
                self.got.append((struct.unpack("!H", body[ihl + 4:ihl + 6])[0], body[8]))
        super()._handle(data)


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def tun(lab, router, link):
    """The tunN number `router` has on `link`."""
    return next(int(h[3:]) for k, r, h in link_faults.endpoints(lab.cfg, link.id)
                if k == "router" and r == router)


def named(t, a, b):
    return next(l for l in t.links.values()
                if {t.devices[l.source_id].name, t.devices[l.target_id].name} == {a, b})


def start(t, tag, lua=True, rip=False):
    lab = Lab(t, tag, Member)
    for name in lab.routers:
        if rip:
            lab.cli(name.upper(), f"gpipe cp add lua {RIP} 1000")
        if lua:
            out = lab.cli(name.upper(), f"gpipe cp add lua {MT} 1000")
            if "started" not in out:
                raise SystemExit(f"{name}: mcast_tree did not load: {out}")
    return lab


def deliver(lab, ident, wait=1.5):
    lab.hosts["H1"].send_to(GRP, ident)
    time.sleep(wait)
    return lab.hosts["H2"].copies(ident)


def copies_total(lab):
    """Every copy every router has sent on, from the per-source COPIES counters."""
    return sum(int(x) for r in lab.routers
               for x in re.findall(r" COPIES (\d+)", lab.cli(r.upper(), "gpipe cp status")))


def wait_rip(lab, router, net, cost, secs=25):
    end = time.time() + secs
    while time.time() < end:
        if f"N {net} COST {cost} " in lab.cli(router, "gpipe cp status"):
            return True
        time.sleep(0.25)
    return False


def h1_net(lab):
    ip = next(m["ifaces"][0]["ip"] for m in lab.cfg.to_runtime(docker=False)["machines"]
              if m["hostname"] == "H1")
    return ip.rsplit(".", 1)[0] + ".0/24"


def until_one(lab, first_ident, ttl, secs):
    """Send a datagram every 0.4 s until one arrives at `ttl`. Returns (seconds it took, every
    copy count seen) -- a copy count above 1 at ANY point is a duplicate."""
    t0, ident, counts = time.time(), first_ident, []
    while time.time() - t0 < secs:
        got = deliver(lab, ident, 0.4)
        counts.append(len(got))
        if got == [ttl]:
            time.sleep(0.5)
            counts[-1] = len(lab.hosts["H2"].copies(ident))
            return time.time() - t0, counts
        ident += 1
    return None, counts


def line():
    t = Topology("line")
    t.routing_mode = "static"
    r1, r2 = t.add_device("router", "R1"), t.add_device("router", "R2")
    h1, h2 = t.add_device("host", "H1"), t.add_device("host", "H2")
    t.add_link(r1.id, r2.id)
    t.add_link(h1.id, r1.id)
    t.add_link(h2.id, r2.id)
    return t


def mesh(seed):
    rng = random.Random(seed)
    t = Topology(f"mesh{seed}")
    t.routing_mode = "static"
    rs = [t.add_device("router", f"R{i + 1}") for i in range(5)]
    edges = {(rng.randrange(i), i) for i in range(1, 5)}
    while len(edges) < 7:
        edges.add(tuple(sorted(rng.sample(range(5), 2))))
    for a, b in sorted(edges):
        t.add_link(rs[a].id, rs[b].id)
    s, r = rng.sample(range(5), 2)
    t.add_link(t.add_device("host", "H1").id, rs[s].id)
    t.add_link(t.add_device("host", "H2").id, rs[r].id)
    return t


def lua_part() -> bool:
    ok = True
    print("[1] a line: one copy, one TTL per router")
    lab = start(line(), "ml")
    try:
        time.sleep(2.5)
        lab.hosts["H2"].join_v3(GRP)          # Linux's first report is IGMPv3
        time.sleep(0.3)                       # well before the next query could teach it
        got = deliver(lab, 1)
        ok &= check("an IGMPv3 join is heard at once; H2 got exactly one copy, at TTL 62",
                    got == [62], got)
    finally:
        lab.stop()

    print("[2] the weighted triangle")
    t, direct = topology("static")
    lab = start(t, "mt")
    try:
        time.sleep(2.5)
        lab.hosts["H2"].join(GRP)
        time.sleep(1)
        got = deliver(lab, 2)
        ok &= check("one copy, along the cheap detour through R3 (TTL 61)", got == [61], got)
        for i in range(3, 9):
            deliver(lab, i, 0.5)
        allc = [len(lab.hosts["H2"].copies(i)) for i in range(3, 9)]
        ok &= check("six more datagrams, one copy each", allc == [1] * 6, allc)
        st = lab.cli("R1", "gpipe cp status")
        m = re.search(r"^S \S+ G 239\.1\.1\.1 RPF (\d+) OIF (\S+) PRUNED (\S+)", st, re.M)
        d1 = tun(lab, "R1", direct)
        ok &= check(f"R1 has pruned the direct link (tun{d1}) and sends only towards R3",
                    m is not None and m.group(3) == str(d1) and str(d1) not in m.group(2).split(","),
                    st)
        a = copies_total(lab)
        time.sleep(2)
        ok &= check("nothing is still circulating", copies_total(lab) == a)
    finally:
        lab.stop()

    print("[3] the triangle with the direct link at cost 1")
    t, direct = topology("static")
    LP.set_prop(direct, "cost", 1)
    lab = start(t, "mc")
    try:
        time.sleep(2.5)
        lab.hosts["H2"].join(GRP)
        time.sleep(1)
        got = deliver(lab, 1)
        ok &= check("one copy, straight across (TTL 62)", got == [62], got)
    finally:
        lab.stop()

    print("[4] random five-router meshes (the old forwarder looped forever on most)")
    for seed in (2, 3, 4, 5, 7, 8):
        lab = start(mesh(seed), f"mm{seed}")
        try:
            time.sleep(2.5)
            lab.hosts["H2"].join(GRP)
            time.sleep(1)
            got = deliver(lab, 1, 2)
            a = copies_total(lab)
            time.sleep(1.5)
            b = copies_total(lab)
            ok &= check(f"seed {seed}: one copy, nothing circulating", len(got) == 1 and a == b,
                        f"copies {got}, router counters {a} -> {b}")
        finally:
            lab.stop()

    print("[5] RIP: the cheap link fails, and multicast follows the routes")
    t, direct = topology("dynamic")
    left = named(t, "R1", "R3")
    lab = start(t, "mr", rip=True)
    try:
        net = h1_net(lab)
        ok &= check("R2 learns H1's network over the detour (cost 5)", wait_rip(lab, "R2", net, 5))
        lab.hosts["H2"].join(GRP)
        time.sleep(1)
        got = deliver(lab, 1)
        ok &= check("one copy along the detour (TTL 61)", got == [61], got)
        for _k, rt, cmd in link_faults.commands(lab.cfg, left.id, up=False):
            lab.cli(rt, cmd)
        took, counts = until_one(lab, 100, 62, 15)
        ok &= check("after the failure it arrives straight across (TTL 62)",
                    took is not None, counts)
        if took is not None:
            ok &= check(f"within seconds ({took:.1f} s)", took < 6, f"{took:.1f} s")
        ok &= check("and never as two copies", max(counts, default=0) <= 1, counts)
        for _k, rt, cmd in link_faults.commands(lab.cfg, left.id, up=True):
            lab.cli(rt, cmd)
        took, counts = until_one(lab, 200, 61, 15)
        ok &= check("repaired: back to the detour (TTL 61)", took is not None, counts)
        ok &= check("never two copies on the way back", max(counts, default=0) <= 1, counts)
    finally:
        lab.stop()

    print("[6] static routes: the cheap link fails")
    t, direct = topology("static")
    left = named(t, "R1", "R3")
    lab = start(t, "ms")
    try:
        time.sleep(2.5)
        lab.hosts["H2"].join(GRP)
        time.sleep(1)
        ok &= check("before: one copy (TTL 61)", deliver(lab, 1) == [61])
        for _k, rt, cmd in link_faults.commands(lab.cfg, left.id, up=False):
            lab.cli(rt, cmd)
        time.sleep(3)
        got = [deliver(lab, i, 0.5) for i in range(10, 14)]
        ok &= check("nothing arrives: R2's static route to H1 still says via R3, so it refuses "
                    "what R1 can still send straight across", got == [[]] * 4, got)
        for _k, rt, cmd in link_faults.commands(lab.cfg, left.id, up=True):
            lab.cli(rt, cmd)
        took, counts = until_one(lab, 20, 61, 15)
        ok &= check("repaired: it resumes, one copy (TTL 61)",
                    took is not None and max(counts) <= 1, counts)
    finally:
        lab.stop()

    print("[7] soft state: a member goes silent, then joins again")
    t = line()
    lab = start(t, "mf")
    rr = named(t, "R1", "R2")
    try:
        time.sleep(2.5)
        h2 = lab.hosts["H2"]
        h2.join(GRP)
        time.sleep(1)
        ok &= check("delivered while H2 answers queries", deliver(lab, 1) == [62])
        h2.answer = False                    # gone without a word: no leave, no reports
        time.sleep(9)                        # HOST_HOLD (6) and a prune
        for i in range(2, 5):
            deliver(lab, i, 0.5)
        st2 = lab.cli("R2", "gpipe cp status")
        ok &= check("R2 forgot the member", re.search(r"^G 239\.1\.1\.1 ", st2, re.M) is None,
                    st2)
        st1 = lab.cli("R1", "gpipe cp status")
        r1 = tun(lab, "R1", rr)
        ok &= check(f"and R1 pruned the branch towards R2 (tun{r1})",
                    re.search(rf"^S \S+ G 239\.1\.1\.1 .* PRUNED {r1} ", st1, re.M) is not None, st1)
        h2.answer = True
        h2.join(GRP)
        time.sleep(0.5)
        got = deliver(lab, 9, 1.0)
        ok &= check("joining again grafts it straight back", got == [62], got)
    finally:
        lab.stop()

    print("[8] a link fails, the member leaves meanwhile, the link recovers")
    t = line()
    lab = start(t, "mg")
    rr = named(t, "R1", "R2")
    try:
        time.sleep(2.5)
        h2 = lab.hosts["H2"]
        h2.join(GRP)
        time.sleep(1)
        deliver(lab, 1)
        for _k, rt, cmd in link_faults.commands(lab.cfg, rr.id, up=False):
            lab.cli(rt, cmd)
        h2.leave(GRP)
        time.sleep(1)
        for _k, rt, cmd in link_faults.commands(lab.cfg, rr.id, up=True):
            lab.cli(rt, cmd)
        time.sleep(2)
        for i in range(2, 8):
            deliver(lab, i, 0.5)
        st1 = lab.cli("R1", "gpipe cp status")
        r1 = tun(lab, "R1", rr)
        ok &= check("R1 has pruned the branch nobody wants any more",
                    re.search(rf"^S \S+ G 239\.1\.1\.1 .* PRUNED {r1} ", st1, re.M) is not None, st1)
        ok &= check("and H2 received nothing after it left",
                    all(not h2.copies(i) for i in range(2, 8)))
    finally:
        lab.stop()
    return ok


def join_everywhere(lab, t):
    """`gpipe mcast join` on every router-to-router link at both ends, and on H2's link: joins in
    a cycle, the case that used to loop."""
    for l in t.links.values():
        for k, r, h in link_faults.endpoints(lab.cfg, l.id):
            if k == "router":
                other = {t.devices[l.source_id].name, t.devices[l.target_id].name} - {r}
                if other != {"H1"}:
                    lab.cli(r, f"gpipe mcast join {GRP} {h[3:]}")


def builtin_part() -> bool:
    ok = True
    print("[9] built-in forwarder, joins on every link")
    t, direct = topology("static")
    lab = start(t, "bt", lua=False)
    try:
        join_everywhere(lab, t)
        lab.cli("R2", "watch on")
        got = deliver(lab, 1, 2)
        ok &= check("one copy, along the detour (TTL 61)", got == [61], got)
        dump = lab.cli("R2", "watch dump 0")
        ok &= check("R2 refused the direct copy: dropped by rpf",
                    re.search(r"^W \d+ \d+ D \d+ -1 -2 rpf \S+ 239\.1\.1\.1 ", dump, re.M)
                    is not None, dump[-600:])
        got = deliver(lab, 2, 2)
        ok &= check("and again: no copy is still going round", got == [61], got)
    finally:
        lab.stop()

    print("[10] built-in forwarder with RIP: the cheap link fails")
    t, direct = topology("dynamic")
    left = named(t, "R1", "R3")
    lab = start(t, "br", lua=False, rip=True)
    try:
        join_everywhere(lab, t)
        net = h1_net(lab)
        ok &= check("R2 learns H1's network over the detour", wait_rip(lab, "R2", net, 5))
        got = deliver(lab, 1)
        ok &= check("one copy along the detour (TTL 61)", got == [61], got)
        for _k, rt, cmd in link_faults.commands(lab.cfg, left.id, up=False):
            lab.cli(rt, cmd)
        took, counts = until_one(lab, 100, 62, 15)
        ok &= check("after the failure: straight across (TTL 62)", took is not None, counts)
        ok &= check("never two copies", max(counts, default=0) <= 1, counts)
        for _k, rt, cmd in link_faults.commands(lab.cfg, left.id, up=True):
            lab.cli(rt, cmd)
        took, counts = until_one(lab, 200, 61, 15)
        ok &= check("repaired: back to the detour (TTL 61)", took is not None, counts)
    finally:
        lab.stop()
    return ok


def main() -> int:
    ok = lua_part()
    ok &= builtin_part()
    print()
    if ok:
        print("RESULT: PASS — multicast takes the cheapest path, delivers one copy, and follows "
              "the routes through a failure.")
        return 0
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
