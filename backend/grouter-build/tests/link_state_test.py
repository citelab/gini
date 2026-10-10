#!/usr/bin/env python3
"""End-to-end: a down interface is really down, and the router knows its netmask.

  A 10.0.1.10/24  <-->  tun1 10.0.1.1/24 [ R ] 10.0.2.1/25 tun2  <-->  B 10.0.2.100/25

Phase 0 of docs/design/link-properties.md. A link failure will be an interface going down, so
"down" has to be a clean failure first. It was not:

  - `down` cancelled the receive thread, leaving the socket undrained: everything the peer sent
    while the link was down sat in the kernel buffer and arrived as one stale burst on `up`.
  - `up` always started a reader, so `up` twice left two; `down` then cancelled one, and the
    interface carried on receiving while marked down.
  - every packet routed at a down link leaked (dropped without being freed).

And the router kept no netmask, so every broadcast it worked out assumed /24.

  GROUTER_BIN=/path/to/grouter python3 link_state_test.py
"""
import os
import re
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))     # grconsole.py

import gini_tun as g                       # noqa: E402
from gini_tun import ROUTER_IP, GRouter    # noqa: E402
from bcast_mcast_test import Recorder      # noqa: E402
import grconsole                           # noqa: E402

CONFIG = f"""ifconfig add tun1 -dstip {ROUTER_IP} -dstport 23002 -addr 10.0.1.1 -hwaddr 02:00:00:00:01:01 -srcport 23001 -netmask 255.255.255.0
ifconfig add tun2 -dstip {ROUTER_IP} -dstport 23004 -addr 10.0.2.1 -hwaddr 02:00:00:00:02:01 -srcport 23003 -netmask 255.255.255.128
route add -dev tun1 -net 10.0.1.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.2.0 -netmask 255.255.255.128
"""

PREFIX_LUA = """
function init(list)
  local out = {}
  for _, i in ipairs(list) do out[#out + 1] = i.iface .. ":" .. i.ip .. "/" .. i.prefix end
  publish(table.concat(out, " "))
end
"""


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def main() -> int:
    R = GRouter("ls", CONFIG)
    time.sleep(1.5)
    if not R.alive():
        print("ROUTER EXITED EARLY\n" + R.tail()); return 1
    A = Recorder("A", "10.0.1.10", "02:00:00:aa:00:10", "10.0.1.1", 23002, 23001)
    B = Recorder("B", "10.0.2.100", "02:00:00:bb:00:64", "10.0.2.1", 23004, 23003)
    time.sleep(0.3); A.resolve(); B.resolve()

    def cli(cmd):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(os.path.join(R.home, "ls.ctl"))
        try:
            return grconsole.query(s, cmd)
        finally:
            s.close()

    def rx(iface):
        m = re.search(rf"^IFSTAT {iface} \S+ \S+ (\d+) (\d+)", cli("ifstat"), re.M)
        return int(m.group(2)) if m else -1

    def b_pings_router(n=3):
        for i in range(n):
            B.ping("10.0.2.1", ident=100 + i)
        time.sleep(0.6)

    ok = True
    try:
        print("[1] baseline")
        A.ping("10.0.2.100"); time.sleep(0.6)
        ok &= check("A reaches B", any(r[0] == "10.0.2.100" for r in A.echo_replies))

        print("[2] the router knows its netmask")
        A.ping("10.0.2.127", ident=2); time.sleep(0.6)
        bc = [f for f in B.frames if f[0] == "10.0.2.127"]
        ok &= check("10.0.2.127 is the /25's broadcast: delivered to B as ff:ff:ff:ff:ff:ff",
                    len(bc) == 1 and bc[0][1] == "ff:ff:ff:ff:ff:ff", f"{bc}")
        A.ping("10.0.2.255", ident=3); time.sleep(0.6)
        ok &= check("10.0.2.255 is outside the /25: not broadcast onto it",
                    not [f for f in B.frames if f[0] == "10.0.2.255"])
        script = os.path.join(R.home, "prefix.lua")
        open(script, "w").write(PREFIX_LUA)
        cli(f"gpipe cp add lua {script} 1000")
        status = cli("gpipe cp status")
        ok &= check("Lua interfaces() reports each prefix",
                    "1:10.0.1.1/24" in status and "2:10.0.2.1/25" in status, status.strip())

        print("[3] a down interface receives nothing")
        cli("ifconfig down tun2")
        before, seen = rx(2), len(B.frames)
        b_pings_router()
        A.ping("10.0.2.100", ident=4); time.sleep(0.6)
        ok &= check("frames from B are not counted", rx(2) == before, f"{before} -> {rx(2)}")
        ok &= check("nothing reaches B", len(B.frames) == seen)

        print("[4] no stale burst when it comes back up")
        cli("watch on")
        cli("ifconfig up tun2")
        time.sleep(0.8)
        stale = [ln for ln in cli("watch dump 0").splitlines()
                 if ln.startswith("W ") and " 10.0.2.100 " in ln]
        ok &= check("what B sent while down did not arrive after up", not stale, f"{stale}")
        cli("watch off")

        print("[5] a second `up` does not leave a reader behind")
        cli("ifconfig up tun2")
        cli("ifconfig down tun2")
        before = rx(2)
        b_pings_router()
        ok &= check("up, up, down still stops reception", rx(2) == before, f"{before} -> {rx(2)}")
        cli("ifconfig up tun2")
        A.echo_replies.clear()
        A.ping("10.0.2.100", ident=6); time.sleep(0.6)
        ok &= check("and up brings it back", any(r[0] == "10.0.2.100" for r in A.echo_replies))
    finally:
        log = R.tail()
        R.stop(); A.stop(); B.stop()
    print()
    if ok:
        print("RESULT: PASS — down is down, up is clean, and the router uses its netmask.")
        return 0
    print("RESULT: FAIL\n--- router log ---\n" + log[-1200:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
