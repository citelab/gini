#!/usr/bin/env python3
"""End-to-end: link costs decide the path, in static routing and in RIP, and changing one in a
running lab moves the traffic (Phase 2 of docs/design/link-properties.md).

            R1 ---- cost 10 ---- R2 -- H2
   H1 -- R1   \\                /
               cost 2      cost 3
                  \\        /
                     R3

The direct R1-R2 link costs 10; the detour through R3 costs 2 + 3 = 5. Which way a ping went is
read from its TTL on arrival: 64 - 3 routers = 61 through R3, 62 straight across.

  [1] static routing, compiled by gBuilder from the costs: the ping takes the detour (61)
  [2] the direct link's cost drops to 1 while running; gBuilder's own planner (link_push) pushes
      the recomputed routes over the console: the ping goes straight across (62)
  [3] dynamic mode, rip_reference.lua on every router, costs from the links: RIP converges on the
      detour at cost 5, and the ping takes it (61)
  [4] the direct link's cost drops to 1 at both ends (the planner's ifconfig commands): RIP moves
      to it within a few ticks, cost 1, and the ping goes straight across (62)

Every step fails on a router or a compiler without link costs.

  GROUTER_BIN=/path/to/grouter python3 cost_test.py
"""
import ipaddress
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in ("../../../frontend-ng/src", "../../../core/src"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, _cand)))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))     # grconsole.py, run_grouter.py

from gini.domain import link_props as LP             # noqa: E402
from gini.domain.topology import Topology             # noqa: E402
from gini.services import link_push                   # noqa: E402
from gini.services.compiler import RuntimeCompiler    # noqa: E402
from gini_tun import GRouter, Host                     # noqa: E402
import grconsole                                       # noqa: E402
import run_grouter                                     # noqa: E402

RIP = os.path.abspath(os.path.join(HERE, "../../../frontend-ng/src/gini/data/examples/"
                                         "rip_reference.lua"))
run_grouter.resolve = lambda host: "127.0.0.1"        # loopback peers (docker=False)


def topology(mode):
    t = Topology("cost")
    t.routing_mode = mode
    r1, r2, r3 = (t.add_device("router", n) for n in ("R1", "R2", "R3"))
    h1, h2 = t.add_device("host", "H1"), t.add_device("host", "H2")
    direct = t.add_link(r1.id, r2.id)
    left, right = t.add_link(r1.id, r3.id), t.add_link(r3.id, r2.id)
    t.add_link(h1.id, r1.id)
    t.add_link(h2.id, r2.id)
    for l, c in ((direct, 10), (left, 2), (right, 3)):
        LP.set_prop(l, "cost", c)
    return t, direct


class Lab:
    """Real routers and hosts for one compiled topology."""

    def __init__(self, t, tag):
        self.t = t
        self.cfg = RuntimeCompiler().compile(t)
        rt = self.cfg.to_runtime(docker=False)
        self.routers = {r["name"]: GRouter(f"{tag}_{r['name']}", run_grouter.build_config(r))
                        for r in rt["routers"]}
        time.sleep(2.0)
        for name, gr in self.routers.items():
            if not gr.alive():
                raise SystemExit(f"router {name} died:\n{gr.tail()}")
        self.hosts = {}
        for m in rt["machines"]:
            itf = m["ifaces"][0]
            self.hosts[m["hostname"]] = Host(m["hostname"], itf["ip"].split("/")[0], itf["mac"],
                                             m["gw"], itf["port"]["bind_port"],
                                             itf["port"]["peer_port"])
        time.sleep(0.3)
        for h in self.hosts.values():
            h.resolve()

    def cli(self, router, cmd):
        gr = self.routers[router.lower()]
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(os.path.join(gr.home, f"{gr.name}.ctl"))
        try:
            return grconsole.query(s, cmd)
        finally:
            s.close()

    def ping_ttl(self, ident):
        """TTL at which H2 received H1's echo request (None if it never arrived)."""
        h1, h2 = self.hosts["H1"], self.hosts["H2"]
        h2.echo_requests.clear()
        h1.ping(h2.ip, ident=ident)
        for _ in range(30):
            time.sleep(0.1)
            got = [ttl for (src, ttl) in h2.echo_requests if src == h1.ip]
            if got:
                return got[0]
        return None

    def apply(self, cmds):
        for router, cmd in cmds:
            out = self.cli(router, cmd)
            if "usage" in out or "must be" in out:
                raise SystemExit(f"{router} refused `{cmd}`: {out}")

    def stop(self):
        for gr in self.routers.values():
            gr.stop()
        for h in self.hosts.values():
            h.stop()                         # clears the loop flag only; the socket stays bound
        time.sleep(0.4)                      # let each receive loop (0.3 s timeout) see the flag
        for h in self.hosts.values():
            h.s.close()                      # free the port: the next compile reuses it


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def h2_net(lab):
    itf = lab.cfg.to_runtime(docker=False)["machines"]
    ip = next(m["ifaces"][0]["ip"] for m in itf if m["hostname"] == "H2")
    return str(ipaddress.ip_interface(ip).network)


def main() -> int:
    ok = True

    print("[1] static routing from link costs")
    t, direct = topology("static")
    lab = Lab(t, "st")
    try:
        ttl = lab.ping_ttl(1)
        ok &= check("H1 -> H2 takes the cheaper detour through R3 (TTL 61)", ttl == 61, f"ttl {ttl}")

        print("[2] the direct link's cost drops to 1 while running")
        installed = link_push.installed_routes(lab.cfg)
        LP.set_prop(direct, "cost", 1)
        new_cfg = RuntimeCompiler().compile(t)
        cmds, _new, msgs = link_push.plan(new_cfg, installed, {direct.id: 1}, static=True)
        lab.apply(cmds)
        print("      pushed: " + "; ".join(msgs))
        ttl = lab.ping_ttl(2)
        ok &= check("the pushed routes send it straight across (TTL 62)", ttl == 62, f"ttl {ttl}")
        cost_col = lab.cli("R1", "ifconfig show")
        ok &= check("R1 reports the new cost on the direct link", "\t1\n" in cost_col or
                    cost_col.rstrip().endswith("1"), cost_col)
    finally:
        lab.stop()

    print("[3] dynamic routing: rip_reference.lua with link costs")
    t, direct = topology("dynamic")
    lab = Lab(t, "dy")
    try:
        if any(r.routes for r in lab.cfg.routers):
            ok &= check("dynamic mode compiles no static routes", False)
        for name in lab.routers:
            out = lab.cli(name.upper(), f"gpipe cp add lua {RIP} 1000")
            if "started" not in out:
                raise SystemExit(f"{name}: RIP did not load: {out}")
        net = h2_net(lab)
        status = ""
        for _ in range(40):
            time.sleep(0.5)
            status = lab.cli("R1", "gpipe cp status")
            if f"N {net} COST 5 " in status:
                break
        ok &= check(f"R1 learns {net} at cost 5 (2 + 3, the detour)", f"N {net} COST 5 " in status,
                    status.strip())
        ttl = lab.ping_ttl(3)
        ok &= check("and the ping takes the detour (TTL 61)", ttl == 61, f"ttl {ttl}")

        print("[4] the direct link's cost drops to 1 at both ends, live")
        LP.set_prop(direct, "cost", 1)
        cmds, _new, _msgs = link_push.plan(RuntimeCompiler().compile(t), {}, {direct.id: 1},
                                           static=False)
        lab.apply(cmds)
        for _ in range(40):
            time.sleep(0.5)
            status = lab.cli("R1", "gpipe cp status")
            if f"N {net} COST 1 " in status:
                break
        ok &= check(f"RIP moves {net} to the direct link at cost 1", f"N {net} COST 1 " in status,
                    status.strip())
        ttl = lab.ping_ttl(4)
        ok &= check("and the ping goes straight across (TTL 62)", ttl == 62, f"ttl {ttl}")
    finally:
        lab.stop()

    print()
    if ok:
        print("RESULT: PASS — link costs pick the path, statically and in RIP, and live changes "
              "move it.")
        return 0
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
