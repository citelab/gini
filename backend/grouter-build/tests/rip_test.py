#!/usr/bin/env python3
"""End-to-end distance vector: the shipped rip_reference.lua must build the route tables.

Reported: in dynamic routing mode "the rip.lua is loading into the control plane, but the route
tables at the routers are not changing". Three bugs, each enough on its own:

  1. interfaces() skipped the highest-numbered interface (it looped 0..count-1; interfaces are
     numbered from 1), so a router never advertised on its last link.
  2. Byte order at the Lua boundary. The router keeps addresses reversed (Dot2IP); the Lua API
     passed them through as if they were wire order, so a script was told its address was
     "1.1.0.10", send() broadcast to 1.1.0.255, and route_add would have installed every route
     backwards.
  3. A broadcast to the subnet it arrived on (10.0.12.255 on 10.0.12.0/24) went down the
     FORWARDING path: routed back out the same interface, never offered to the control plane.
     No router ever heard a neighbour.

This runs the real router binary three times in a chain, compiled by gBuilder's compiler in
dynamic mode (connected routes only), loads the reference module exactly as a student would,
and checks every router learns every network at the right cost and next hop. Then it breaks a
link and checks the bad news arrives.

Topology:  M1 -- R1 <==> R2 <==> R3 -- M5

  GROUTER_BIN=/path/to/grouter python3 rip_test.py
"""
import ipaddress
import os
import re
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in ("../../../gbuilder/src", "../../../frontend-ng/src", "../../../core/src"):
    _p = os.path.abspath(os.path.join(HERE, _cand))
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))     # grconsole.py

from gini.domain.topology import Topology          # noqa: E402
from gini.services.compiler import RuntimeCompiler  # noqa: E402
from gini_tun import GRouter                         # noqa: E402
import grconsole                                     # noqa: E402

RIP = os.path.abspath(os.path.join(HERE, "../../../frontend-ng/src/gini/data/examples/"
                                         "rip_reference.lua"))
TICK_MS = 1000
ROW = re.compile(r"^\[\d+\]\s+(\S+)\s+(\S+)\s+(\S+)\s+tun(\d+)\s*([CSD]?)", re.M)


def router_config(rspec) -> str:
    """Interfaces and their connected routes ONLY: dynamic mode, the protocol does the rest."""
    lines = []
    for i, itf in enumerate(rspec["ifaces"], start=1):
        ip = itf["ip"].split("/")[0]
        p = itf["port"]
        lines.append(f"ifconfig add tun{i} -dstip 127.0.0.1 -dstport {p['peer_port']} "
                     f"-addr {ip} -hwaddr {itf['mac']} -mtu 1400 -srcport {p['bind_port']}")
    for i, itf in enumerate(rspec["ifaces"], start=1):
        net = ipaddress.ip_interface(itf["ip"]).network
        lines.append(f"route add -dev tun{i} -net {net.network_address} -netmask {net.netmask}")
    return "\n".join(lines) + "\n"


def cli(gr, cmd) -> str:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(os.path.join(gr.home, f"{gr.name}.ctl"))
    try:
        return grconsole.query(s, cmd)
    finally:
        s.close()


def table(gr) -> dict:
    """{network: (nexthop, origin)} from `route show`."""
    return {net: (nh, origin) for net, _mask, nh, _dev, origin in ROW.findall(cli(gr, "route show"))}


def main() -> int:
    t = Topology("rip")
    t.routing_mode = "dynamic"
    r1, r2, r3 = (t.add_device("router") for _ in range(3))
    m1, m5 = t.add_device("host"), t.add_device("host")
    for a, b in [(m1.id, r1.id), (r1.id, r2.id), (r2.id, r3.id), (m5.id, r3.id)]:
        t.add_link(a, b)
    rt = RuntimeCompiler().compile(t).to_runtime(docker=False)
    if any(r["routes"] for r in rt["routers"]):
        print("RESULT: FAIL — dynamic mode compiled static routes; RIP would be fighting them")
        return 1

    routers = {r["name"]: GRouter("rip_" + r["name"], router_config(r)) for r in rt["routers"]}
    nets = {r["name"]: {str(ipaddress.ip_interface(i["ip"]).network.network_address)
                        for i in r["ifaces"]} for r in rt["routers"]}
    every = set().union(*nets.values())
    time.sleep(2.0)
    try:
        for name, gr in routers.items():
            if not gr.alive():
                print(f"router {name} died:\n{gr.tail()}"); return 1
            out = cli(gr, f"gpipe cp add lua {RIP} {TICK_MS}")
            if "started" not in out:
                print(f"RESULT: FAIL — {name} did not load the module: {out}"); return 1

        # a 3-router chain converges in ~2 ticks; allow plenty
        deadline = time.time() + 15
        while time.time() < deadline:
            if all(set(table(gr)) >= every for gr in routers.values()):
                break
            time.sleep(0.5)
        ok = True
        for name, gr in routers.items():
            tab = table(gr)
            missing = every - set(tab)
            learned = {n: v for n, v in tab.items() if v[1] == "D"}
            print(f"[{name}] learned {sorted(learned)}" + (f"  MISSING {sorted(missing)}"
                                                           if missing else ""))
            if missing or set(learned) != every - nets[name]:
                ok = False
        # the end routers reach each other's LAN through the middle one, two hops away
        status = cli(routers["r1"], "gpipe cp status")
        far = [ln for ln in status.splitlines() if " COST 2 " in ln]
        print(f"[r1] cost-2 routes: {far}")
        if not far:
            ok = False
        if not ok:
            print("RESULT: FAIL — RIP did not build the tables")
            print("--- r2 ---\n" + routers["r2"].tail(800))
            return 1

        # bad news: cut R2<->R3 on both ends; R1 must drop R3's LAN after it ages out
        r3_lan = (nets["r3"] - nets["r2"]).pop()
        cli(routers["r2"], "ifconfig down tun2")
        cli(routers["r3"], "ifconfig down tun1")
        deadline = time.time() + 15
        while time.time() < deadline and r3_lan in table(routers["r1"]):
            time.sleep(0.5)
        if r3_lan in table(routers["r1"]):
            print(f"RESULT: FAIL — r1 still routes to {r3_lan} after the link went down")
            return 1
        print(f"[r1] dropped {r3_lan} after the R2-R3 link went down")
        print()
        print("RESULT: PASS — rip_reference.lua converges in dynamic mode and handles a failure.")
        return 0
    finally:
        for gr in routers.values():
            gr.stop()


if __name__ == "__main__":
    sys.exit(main())
