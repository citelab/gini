#!/usr/bin/env python3
"""End-to-end: multicast, directed broadcasts, and the unicast hosts the multicast test ate.

  A 10.0.1.10  <-->  tun1 10.0.1.1 [ R ] 10.0.2.1 tun2  <-->  B 10.0.2.x

Three bugs, all byte order (the router keeps addresses reversed; gNtohl yields that order):

  1. Multicast was detected from the LAST octet of the destination. A unicast packet to a host
     numbered .224-.239 went to the multicast path and vanished -- no reply, no watch record.
     Real multicast went to the forwarding path and died as "no route".
  2. The multicast forwarder looked groups up reversed, so it never matched a
     `gpipe mcast join`, and built the multicast MAC from the wrong bytes. It also forwarded a
     TTL-1 packet out at TTL 0.
  3. The directed-broadcast match compared the wrong bytes and never matched, so a ping to
     10.0.2.255 from another subnet was routed as unicast and ARPed for. It is now broadcast
     onto 10.0.2.0/24, as the code intended (the smurf experiment). Real routers turn this off
     (RFC 2644); GINI keeps it on deliberately.
  4. Not byte order: the tun driver -- the one every GINI link uses -- dropped frames sent to a
     multicast MAC (01:00:5e:..) as "not for this router". fromEthernetDev had accepted them
     since B3; tun never got the line. So a host's IGMP join and its multicast never reached
     IP, and mcast_tree.lua could not learn a single member.

Each case below failed before the fix.

  GROUTER_BIN=/path/to/grouter python3 bcast_mcast_test.py
"""
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))     # grconsole.py

import gini_tun as g                      # noqa: E402
from gini_tun import ROUTER_IP, GRouter, Host   # noqa: E402
import grconsole                          # noqa: E402

CONFIG = f"""ifconfig add tun1 -dstip {ROUTER_IP} -dstport 22002 -addr 10.0.1.1 -hwaddr 02:00:00:00:01:01 -srcport 22001
ifconfig add tun2 -dstip {ROUTER_IP} -dstport 22004 -addr 10.0.2.1 -hwaddr 02:00:00:00:02:01 -srcport 22003
route add -dev tun1 -net 10.0.1.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.2.0 -netmask 255.255.255.0
"""


class Recorder(Host):
    """A host that also records every IP frame it receives: (dst ip, dst mac, ttl)."""

    def __init__(self, *a):
        self.frames = []
        super().__init__(*a)

    def _handle(self, data):
        if len(data) >= 34 and data[12:14] == b"\x08\x00":
            self.frames.append((g.b2ip(data[30:34]), g.b2mac(data[0:6]), data[22]))
        super()._handle(data)

    def got(self, dst):
        return [f for f in self.frames if f[0] == dst]


def check(name, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def main() -> int:
    R = GRouter("bm", CONFIG)
    time.sleep(1.5)
    if not R.alive():
        print("ROUTER EXITED EARLY\n" + R.tail()); return 1
    A = Recorder("A", "10.0.1.10", "02:00:00:aa:00:10", "10.0.1.1", 22002, 22001)
    B = Recorder("B", "10.0.2.230", "02:00:00:bb:00:e6", "10.0.2.1", 22004, 22003)
    time.sleep(0.3); A.resolve(); B.resolve()

    def cli(cmd):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(os.path.join(R.home, "bm.ctl"))
        try:
            return grconsole.query(s, cmd)
        finally:
            s.close()

    def mcast_mac(group):
        b = g.ip2b(group)
        return "01:00:5e:%02x:%02x:%02x" % (b[1] & 0x7f, b[2], b[3])

    def send(dst, ttl, ident):
        # to the group's multicast MAC, as a real host sends it -- not to the router's MAC
        A._send(g.eth(mcast_mac(dst), A.mac, g.ETH_IP, g.ip_pkt(A.ip, dst, ttl, 1,
                                                                 g.icmp(8, ident, 1, b"x"))))

    ok = True
    try:
        cli("gpipe mcast join 224.1.2.3 2")
        A.ping("10.0.2.230")                         # 1. a host numbered in .224-.239
        send("224.1.2.3", 8, 2)                      # 2. multicast to a joined group
        send("224.1.2.3", 1, 3)                      #    ...and the same at TTL 1
        send("224.9.9.9", 8, 4)                      #    ...and to a group nobody joined
        A.ping("10.0.2.255", ident=5)                # 3. a directed broadcast
        time.sleep(1.5)

        print("[1] unicast to 10.0.2.230")
        ok &= check("B received the echo request", bool(B.echo_requests))
        ok &= check("A received the reply", any(r[0] == "10.0.2.230" for r in A.echo_replies))
        print("[2] multicast")
        mc = B.got("224.1.2.3")
        ok &= check("one copy of 224.1.2.3 reached the member interface", len(mc) == 1,
                    f"got {mc}")
        ok &= check("at TTL 7, to the multicast MAC 01:00:5e:01:02:03",
                    bool(mc) and mc[0][2] == 7 and mc[0][1] == "01:00:5e:01:02:03", f"{mc}")
        ok &= check("a group with no members was not forwarded", not B.got("224.9.9.9"))
        print("[3] directed broadcast")
        db = B.got("10.0.2.255")
        ok &= check("10.0.2.255 was broadcast onto 10.0.2.0/24",
                    len(db) == 1 and db[0][1] == "ff:ff:ff:ff:ff:ff", f"got {db}")

        print("[4] mcast_tree.lua learns members from IGMP")
        cli("gpipe mcast leave 224.1.2.3 2")         # the Lua module is the forwarder now
        tree = os.path.abspath(os.path.join(HERE, "../../../frontend-ng/src/gini/data/examples/"
                                                  "mcast_tree.lua"))
        cli(f"gpipe cp add lua {tree} 1000")
        grp = "239.1.1.1"
        body = bytes([0x16, 0, 0, 0]) + g.ip2b(grp)                  # IGMPv2 membership report
        body = bytes([0x16, 0]) + g._cksum(body).to_bytes(2, "big") + g.ip2b(grp)
        B._send(g.eth(mcast_mac(grp), B.mac, g.ETH_IP, g.ip_pkt(B.ip, grp, 1, 2, body)))
        time.sleep(1.5)
        send(grp, 8, 6)
        time.sleep(1.5)
        status = cli("gpipe cp status")
        ok &= check("B's interface is a member of 239.1.1.1", f"G {grp} IF 2" in status, status)
        ok &= check("exactly one copy reached B", len(B.got(grp)) == 1, f"got {B.got(grp)}")
    finally:
        log = R.tail()
        R.stop(); A.stop(); B.stop()
    print()
    if ok:
        print("RESULT: PASS — multicast (built-in and mcast_tree.lua), directed broadcast and "
              ".224-.239 hosts all forward.")
        return 0
    print("RESULT: FAIL\n--- router log ---\n" + log[-1200:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
