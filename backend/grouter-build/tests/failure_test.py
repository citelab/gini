#!/usr/bin/env python3
"""End-to-end: a link failure, as the router and a routing protocol see it (Phase 3 of
docs/design/link-properties.md).

Part 1, one router, A on tun1 and B on tun2, with a small Lua probe that records what the control
plane is told:
  [1] `ifconfig down tun2` withdraws tun2's connected route, as a real router does on carrier
      loss, and `up` puts exactly that back
  [2] Lua sees it: interfaces() reports up=false, on_link_change(2, false, cost) fires once per
      real change (a second `down` is not a change), and a cost change fires it too
  [3] multicast is not copied onto a down interface

Part 2, the weighted triangle (direct R1-R2 costs 10, the detour through R3 costs 2 + 3), with
rip_reference.lua on every router:
  [4] the cheap R1-R3 link fails at both ends -- the exact commands gBuilder sends
      (link_faults.commands) -- and RIP fails over to the direct link at cost 10 FAST, through
      on_link_change and a triggered update rather than waiting for the route to age out; the
      ping follows (TTL 62)
  [5] the link is repaired and RIP goes back to the detour at cost 5 (TTL 61)

  GROUTER_BIN=/path/to/grouter python3 failure_test.py
"""
import os
import re
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in ("../../../frontend-ng/src", "../../../core/src"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, _cand)))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import gini_tun as g                                  # noqa: E402
from gini_tun import ROUTER_IP, GRouter               # noqa: E402
from bcast_mcast_test import Recorder                 # noqa: E402
from cost_test import RIP, Lab, topology              # noqa: E402
from gini.domain import link_faults                   # noqa: E402
import grconsole                                      # noqa: E402

CONFIG = f"""ifconfig add tun1 -dstip {ROUTER_IP} -dstport 24002 -addr 10.0.1.1 -hwaddr 02:00:00:00:01:01 -srcport 24001 -netmask 255.255.255.0
ifconfig add tun2 -dstip {ROUTER_IP} -dstport 24004 -addr 10.0.2.1 -hwaddr 02:00:00:00:02:01 -srcport 24003 -netmask 255.255.255.0 -metric 3
route add -dev tun1 -net 10.0.1.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.2.0 -netmask 255.255.255.0
"""

PROBE = """
events = {}
function snap()
  local up = {}
  for _, i in ipairs(interfaces()) do up[#up + 1] = i.iface .. "=" .. tostring(i.up) end
  publish("UP " .. table.concat(up, " ") .. "\\nEV " .. table.concat(events, " "))
end
function init(list) snap() end
function tick() snap() end
function on_link_change(iface, up, cost)
  events[#events + 1] = iface .. ":" .. tostring(up) .. ":" .. cost
  snap()
end
"""


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def part1() -> bool:
    ok = True
    R = GRouter("fl", CONFIG)
    time.sleep(1.5)
    A = Recorder("A", "10.0.1.10", "02:00:00:aa:00:10", "10.0.1.1", 24002, 24001)
    B = Recorder("B", "10.0.2.10", "02:00:00:bb:00:10", "10.0.2.1", 24004, 24003)
    time.sleep(0.3); A.resolve(); B.resolve()

    def cli(cmd):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(os.path.join(R.home, "fl.ctl"))
        try:
            return grconsole.query(s, cmd)
        finally:
            s.close()

    def status():
        time.sleep(0.4)                       # events arrive on the control thread
        return cli("gpipe cp status")

    try:
        probe = os.path.join(R.home, "probe.lua")
        open(probe, "w").write(PROBE)
        cli(f"gpipe cp add lua {probe} 500")
        print("[1] the connected route follows the interface")
        cli("ifconfig down tun2")
        routes = cli("route show")
        ok &= check("down withdraws tun2's connected route", "10.0.2.0" not in routes, routes)
        cli("ifconfig up tun2")
        routes = cli("route show")
        ok &= check("up restores it, as connected (C)",
                    re.search(r"10\.0\.2\.0\s+255\.255\.255\.0\s+0\.0\.0\.0\s+tun2\s+C", routes)
                    is not None, routes)

        print("[2] the control plane is told")
        cli("ifconfig down tun2")
        st = status()
        ok &= check("interfaces() says tun2 is down", "2=false" in st and "1=true" in st, st)
        cli("ifconfig down tun2")                # not a change
        cli("ifconfig up tun2")
        cli("ifconfig mod tun2 -metric 5")
        st = status()
        ev = re.search(r"EV (.*)", st).group(1).split()
        # [1]'s down and up, then [2]'s down -- the second `down` is not a change and fires
        # nothing -- its up, and the cost change, each carrying the cost at that moment
        ok &= check("on_link_change once per real change, and for the cost change",
                    ev == ["2:false:3", "2:true:3", "2:false:3", "2:true:3", "2:true:5"],
                    " ".join(ev))

        print("[3] multicast skips a down interface")
        cli("gpipe mcast join 224.1.2.3 2")
        cli("ifconfig down tun2")
        A._send(g.eth("01:00:5e:01:02:03", A.mac, g.ETH_IP,
                      g.ip_pkt(A.ip, "224.1.2.3", 8, 1, g.icmp(8, 1, 1, b"x"))))
        time.sleep(0.6)
        ok &= check("no copy reached B", not B.got("224.1.2.3"))
    finally:
        R.stop(); A.stop(); B.stop()
        time.sleep(0.4); A.s.close(); B.s.close()
    return ok


def part2() -> bool:
    ok = True
    t, direct = topology("dynamic")
    left = next(l for l in t.links.values()
                if {t.devices[l.source_id].name, t.devices[l.target_id].name} == {"R1", "R3"})
    lab = Lab(t, "fo")
    try:
        for name in lab.routers:
            lab.cli(name.upper(), f"gpipe cp add lua {RIP} 1000")
        net = next(m["ifaces"][0]["ip"] for m in lab.cfg.to_runtime(docker=False)["machines"]
                   if m["hostname"] == "H2").rsplit(".", 1)[0] + ".0/24"

        def h2(cost, secs):
            end = time.time() + secs
            while time.time() < end:
                if f"N {net} COST {cost} " in lab.cli("R1", "gpipe cp status"):
                    return True
                time.sleep(0.1)
            return False

        assert h2(5, 20), lab.cli("R1", "gpipe cp status")
        print("[4] the cheap R1-R3 link fails at both ends")
        t0 = time.time()
        for _kind, router, cmd in link_faults.commands(lab.cfg, left.id, up=False):
            lab.cli(router, cmd)
        moved = h2(10, 10)
        took = time.time() - t0
        ok &= check(f"RIP fails over to the direct link at cost 10 ({took:.1f} s)", moved,
                    lab.cli("R1", "gpipe cp status"))
        ok &= check("quickly: well inside the 3 ticks a silent route takes to age out",
                    took < 2.5, f"{took:.1f} s")
        ttl = lab.ping_ttl(10)
        ok &= check("and the ping follows it straight across (TTL 62)", ttl == 62, f"ttl {ttl}")

        print("[5] the link is repaired")
        for _kind, router, cmd in link_faults.commands(lab.cfg, left.id, up=True):
            lab.cli(router, cmd)
        ok &= check("RIP goes back to the detour at cost 5", h2(5, 10),
                    lab.cli("R1", "gpipe cp status"))
        ttl = lab.ping_ttl(11)
        ok &= check("and so does the ping (TTL 61)", ttl == 61, f"ttl {ttl}")
    finally:
        lab.stop()
    return ok


def main() -> int:
    ok = part1()
    ok &= part2()
    print()
    if ok:
        print("RESULT: PASS — a failed link withdraws its route, tells the control plane, and "
              "RIP fails over and back.")
        return 0
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
