#!/usr/bin/env python3
"""End-to-end multi-router routing, driven by the COMPILER's auto-generated config.

Regression for the bug where a machine could reach everything on its own router but
nothing across a router-to-router link: the compiler only emitted *connected* routes.
It now computes static inter-router routes; this proves a host on R1 reaches a host on
R2 (2 hops, TTL 64->62) using exactly what the compiler produces.

Topology:  M1 -- R1 <==> R2 -- M5

  GROUTER_BIN=/path/to/grouter python3 multirouter_test.py
"""
import ipaddress
import os
import sys
import time

# the app package lives in the sibling app dir's src/ (gbuilder/, formerly frontend-ng/)
for _cand in ("../../../gbuilder/src", "../../../frontend-ng/src"):
    _p = os.path.abspath(os.path.join(os.path.dirname(__file__), _cand))
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
        break

from gini.domain.topology import Topology          # noqa: E402
from gini.services.compiler import RuntimeCompiler  # noqa: E402
from gini_tun import GRouter, Host                  # noqa: E402


def router_config(rspec) -> str:
    lines = []
    for i, itf in enumerate(rspec["ifaces"], start=1):
        ip = itf["ip"].split("/")[0]
        p = itf["port"]
        lines.append(f"ifconfig add tun{i} -dstip 127.0.0.1 -dstport {p['peer_port']} "
                     f"-addr {ip} -hwaddr {itf['mac']} -mtu 1400 -srcport {p['bind_port']}")
    for i, itf in enumerate(rspec["ifaces"], start=1):
        net = ipaddress.ip_interface(itf["ip"]).network
        lines.append(f"route add -dev tun{i} -net {net.network_address} -netmask {net.netmask}")
    for rr in rspec["routes"]:                       # the static inter-router routes
        lines.append(f"route add -dev tun{rr['dev']} -net {rr['net']} "
                     f"-netmask {rr['mask']} -gw {rr['gw']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    t = Topology("lab")
    r1 = t.add_device("router"); r2 = t.add_device("router")
    m1 = t.add_device("host"); m5 = t.add_device("host")
    for a, b in [(m1.id, r1.id), (r1.id, r2.id), (m5.id, r2.id)]:
        t.add_link(a, b)
    rt = RuntimeCompiler().compile(t).to_runtime(docker=False)   # loopback peers

    routers = {r["name"]: GRouter("mr_" + r["name"], router_config(r))
               for r in rt["routers"]}
    time.sleep(2.0)
    try:
        for name, gr in routers.items():
            if not gr.alive():
                print(f"router {name} died:\n{gr.tail()}"); return 1

        hosts = {}
        for m in rt["machines"]:
            ip = m["ip"].split("/")[0]
            p = m["port"]
            hosts[m["name"]] = Host(m["name"], ip, m["mac"], m["gw"],
                                    p["bind_port"], p["peer_port"])
        time.sleep(0.3)
        for h in hosts.values():
            h.resolve()

        names = list(hosts.keys())                  # two hosts, one per router
        a = hosts[names[0]]; b = hosts[names[1]]
        print(f"[1] {a.ip} (on R1) -> ping {b.ip} (on R2), across the R1<->R2 link ...")
        a.ping(b.ip)
        fwd = rev = False
        for _ in range(40):
            time.sleep(0.2)
            if any(s == a.ip and ttl == 62 for (s, ttl) in b.echo_requests):
                fwd = True
            if any(s == b.ip and ttl == 62 for (s, ttl, _i, _q) in a.echo_replies):
                rev = True
            if fwd and rev:
                break
        if fwd:
            print("[2] M5 received the echo request at ttl=62  (M1->R1->R2->M5)")
        if rev:
            print("[3] M1 received the echo reply  at ttl=62  (M5->R2->R1->M1)")
        print()
        if fwd and rev:
            print("RESULT: PASS — auto-routed multi-router path works (M1<->M5, TTL 64->62).")
            return 0
        print(f"RESULT: FAIL — forward={fwd} reverse={rev}")
        print("--- R1 ---\n" + routers["r1"].tail(600))
        return 1
    finally:
        for gr in routers.values():
            gr.stop()


if __name__ == "__main__":
    sys.exit(main())
