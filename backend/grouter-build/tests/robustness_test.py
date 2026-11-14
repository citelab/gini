#!/usr/bin/env python3
"""The router survives what students do to it: typos, wrong addresses, and changing it under load.

Every case here crashed the router before it was fixed, reproduced on a real one; each section
names the bug. Run in the router image (or anywhere with the binary):

  GROUTER_BIN=/usr/local/bin/grouter python3 robustness_test.py

The router must be ALIVE after every step, and the steps that have a right answer must give it.
"""
from __future__ import annotations

import os
import re
import socket
import struct
import sys
import threading
import time

from gini_tun import (ETH_IP, ROUTER_IP, GRouter, Host, _cksum, b2ip, eth, icmp, ip2b, ip_pkt)

END = "__END__"
TUN1_LOCAL, A_BIND = 25001, 25002          # tun1 <-> host A (10.0.1.10)
TUN2_LOCAL, B_BIND = 25003, 25004          # tun2 <-> host B (10.0.2.10)
CONFIG = f"""ifconfig add tun1 -dstip {ROUTER_IP} -dstport {A_BIND} -addr 10.0.1.1 -hwaddr 02:00:00:00:01:01 -mtu 1400 -srcport {TUN1_LOCAL}
ifconfig add tun2 -dstip {ROUTER_IP} -dstport {B_BIND} -addr 10.0.2.1 -hwaddr 02:00:00:00:02:01 -mtu 1400 -srcport {TUN2_LOCAL}
route add -dev tun1 -net 10.0.1.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.2.0 -netmask 255.255.255.0
"""


class RawHost(Host):
    """A Host that also keeps every IP datagram it receives, for checking fragments and ICMP."""

    def __init__(self, *a, **kw):
        self.raw = []
        super().__init__(*a, **kw)

    def _handle(self, data):
        if len(data) >= 34 and struct.unpack("!H", data[12:14])[0] == ETH_IP:
            self.raw.append(data[14:])
        super()._handle(data)


def query(sock_path: str, line: str, timeout: float = 40.0) -> str:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    s.sendall((line + "\n").encode())
    buf = ""
    while END not in buf:
        d = s.recv(8192).decode(errors="replace")
        if not d:
            break
        buf += d
    s.close()
    return buf.split(END)[0]


def ip_hdr(src, dst, ttl, proto, payload, ident=0x2222, frag=0):
    """ip_pkt with a chosen identifier and flags/offset field (DF = 0x4000)."""
    total = 20 + len(payload)
    h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total, ident, frag, ttl, proto, 0,
                    ip2b(src), ip2b(dst))
    h = h[:10] + struct.pack("!H", _cksum(h)) + h[12:]
    return h + payload


class Case:
    def __init__(self, r, sock):
        self.r, self.sock, self.failed = r, sock, []

    def cli(self, line):
        return query(self.sock, line)

    def check(self, what, ok, detail=""):
        alive = self.r.alive()
        status = "ok " if ok and alive else "FAIL"
        print(f"  [{status}] {what}" + ("" if alive else "  -- ROUTER DIED"))
        if not (ok and alive):
            self.failed.append(what)
            if detail:
                print("        " + detail.replace("\n", "\n        ")[:600])
        if not alive:
            print(self.r.tail())
            raise SystemExit(1)


def cli_typos(c: Case):
    print("1. commands missing a word, with a wrong word, or too long (each was a SIGSEGV)")
    expect = {
        "route del 10.0.2.0/24": "usage",           # read as table index 10020024
        "route del": "usage",
        "route del 25": "usage",                    # past the 20-entry table
        "route add -dev tun1 -net": "missing",
        "route add -dev": "missing",
        "class add": "missing class name",
        "class add " + "n" * 40: "too long",        # cname[32] on the stack
        "qdisc": "taildrop",
        "qdisc add": "usage",
        "qdisc add red -min": "needs a value",
        "queue add": "missing class name",
        "queue add flow1": "missing qdisc",
        "queue mod default -weight": "usage",
        "queue del default": "cannot be deleted",   # every unclassified packet then exit(1)'d
        "class del default": "cannot be deleted",
        "ping -3": "usage",
        "ping 10.0.1.1 -size": "needs a number",
        "ping -1 10.0.1.1 -size 2000": "must be",   # written past the 1500-byte packet
        "ifconfig add tun9 -dstip": "missing",
        "gnc -l 5000": "interactive console",       # never returned, held the console
        "class add " + "x" * 300: "too long",       # a 256+ character line overran orig_str
    }
    for line, want in expect.items():
        out = c.cli(line)
        c.check(f"{line[:44]!r} -> {want!r}", want in out, out)
    for line in ("class add web -dst ( -port 80 )",        # a single port: atoi(NULL)
                 "filter move 0", "arp del -ip", "arp add -ip 10.0.1.5 -mac",
                 "queue mod web -weight", "queue mod web -qdisc"):
        c.cli(line)
        c.check(f"{line!r} survives", True)
    out = c.cli("class add f -src ( -net 10.0.1.0/40 ) -dst ( -net 10.0.2.0/24 )")
    shown = c.cli("class show")
    c.check("-src and -dst both kept, /40 clamped to /32",
            "10.0.1.0/32" in shown and "10.0.2.0/24" in shown, shown)
    c.cli("class del f")
    c.cli("class del web")


def arp_buffer(c: Case, A: Host):
    print("2. 60 packets toward a next hop that never answers ARP (the 51st freed ARPbuffer[50])")
    for i in range(60):
        A._send(eth(A.gw_mac, A.mac, ETH_IP,
                    ip_pkt(A.ip, "10.0.2.99", 64, 1, icmp(8, 0x4242, i, b"nobody-home"))))
        time.sleep(0.005)
    time.sleep(0.5)
    c.check("router alive after 60 unresolved packets", True)


def fragmentation(c: Case, A: RawHost, B: RawHost):
    print("3. fragmenting (every fragmented packet crashed) and DF (never seen: wrong byte order)")
    c.cli("ifconfig mod tun2 -mtu 600")
    B.raw.clear()
    payload = bytes(range(256)) * 4                       # 1024 bytes of ICMP data
    A._send(eth(A.gw_mac, A.mac, ETH_IP,
                ip_hdr(A.ip, B.ip, 64, 1, icmp(8, 0x5151, 1, payload), ident=0x3333)))
    time.sleep(0.5)
    frags = [d for d in B.raw if struct.unpack("!H", d[4:6])[0] == 0x3333]
    fields = [(struct.unpack("!H", d[6:8])[0], struct.unpack("!H", d[2:4])[0]) for d in frags]
    data = 8 + len(payload)
    offsets = sorted((f & 0x1fff) * 8 for f, _ in fields)
    ok = (len(frags) >= 2 and all(t <= 600 for _, t in fields) and offsets[0] == 0
          and sum(t - 20 for _, t in fields) == data
          and sum(1 for f, _ in fields if not f & 0x2000) == 1          # exactly one last
          and not any(f & 0x4000 for f, _ in fields))                   # DF clear on fragments
    c.check(f"a {data + 20}-byte datagram leaves a 600-MTU link as {len(frags)} RFC 791 fragments",
            ok, repr(fields))
    A.raw.clear()
    A._send(eth(A.gw_mac, A.mac, ETH_IP,
                ip_hdr(A.ip, B.ip, 64, 1, icmp(8, 0x5252, 1, payload), ident=0x4444, frag=0x4000)))
    time.sleep(0.5)
    unreach = [d for d in A.raw if d[9] == 1 and d[20] == 3 and d[21] == 4]
    ok = False
    if unreach:
        d = unreach[0]
        ilen = struct.unpack("!H", d[2:4])[0] - 20
        mtu = struct.unpack("!H", d[26:28])[0]
        ok = mtu == 600 and _cksum(d[20:20 + ilen]) == 0
    c.check("DF set: 'fragmentation needed', MTU 600 in network order, valid checksum", ok,
            repr(unreach[:1]))
    c.cli("ifconfig mod tun2 -mtu 1400")


def flood(A: Host, dst: str, stop: threading.Event, proto=17, size=200):
    body = b"\x00" * size
    frame = eth(A.gw_mac, A.mac, ETH_IP, ip_pkt(A.ip, dst, 64, proto, body))
    while not stop.is_set():
        for _ in range(50):
            A._send(frame)
        time.sleep(0.002)


def under_load(c: Case, A: Host, B: Host):
    print("4. changing queues, classes, the pipeline and delays while a flood runs")
    for line in ("set sched-cycle 20", "class add flow1 -src ( -net 10.0.1.10 )",
                 "queue add flow1 taildrop -size 64 -weight 1", "spolicy set drr"):
        c.cli(line)
    stop = threading.Event()
    t = threading.Thread(target=flood, args=(A, B.ip, stop), daemon=True)
    t.start()
    try:
        for _ in range(8):           # `queue del` mid-pass freed the name the scheduler held
            c.cli("queue del flow1"); c.cli("class del flow1")
            c.cli("class add flow1 -src ( -net 10.0.1.10 )")
            c.cli("queue add flow1 taildrop -size 64 -weight 2")
            c.cli("spolicy set rr"); c.cli("queue mod flow1 -weight 1"); c.cli("spolicy set drr")
        c.check("queues and classes deleted and re-added under a flood", True)
        for _ in range(10):          # `gpipe clear` freed modules the worker was running
            c.cli("gpipe add counter"); c.cli("gpipe add acl 10.0.9.0/24"); c.cli("gpipe clear")
        c.check("pipeline modules added and cleared under a flood", True)
        for _ in range(4):           # `delay … off` freed the line a sender was pushing into
            c.cli("delay egress 5 1 0.5"); c.cli("delay egress off")
        c.check("egress delay switched on and off under a flood", True)
    finally:
        stop.set(); t.join()
    stats = c.cli("queue stats")
    c.check("the scheduler kept forwarding", re.search(r"flow1\s+\S+\s+\S+\s+\d+\s+[1-9]", stats)
            is not None, stats)
    c.cli("set sched-cycle 0"); c.cli("queue del flow1"); c.cli("class del flow1")


def to_the_router(c: Case, A: Host):
    print("5. UDP and TCP addressed to the router itself (lwIP freed an uninitialised pbuf)")
    for i in range(300):
        udp = struct.pack("!HHHH", 40000 + i, 5201, 8 + 4, 0) + b"ping"
        tcp = struct.pack("!HHIIBBHHH", 40000 + i, 80, i, 0, 5 << 4, 0x02, 1024, 0, 0)
        A._send(eth(A.gw_mac, A.mac, ETH_IP, ip_pkt(A.ip, "10.0.1.1", 64, 17, udp)))
        A._send(eth(A.gw_mac, A.mac, ETH_IP, ip_pkt(A.ip, "10.0.1.1", 64, 6, tcp)))
    time.sleep(0.5)
    c.check("600 datagrams to the router's own address", True)


def rss_kb(pid):
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0


def no_route_leak(c: Case, A: Host):
    print("6. a flood with no route (each packet leaked ~1.5 KB until the OOM killer came)")
    frame = eth(A.gw_mac, A.mac, ETH_IP, ip_pkt(A.ip, "10.99.0.1", 64, 17, b"\x00" * 1200))
    before = rss_kb(c.r.proc.pid)
    for i in range(20000):
        A._send(frame)
        if i % 200 == 0:
            time.sleep(0.005)
    time.sleep(1.0)
    grew = rss_kb(c.r.proc.pid) - before
    c.check(f"20,000 unroutable packets grew the router by {grew} kB (leak would be ~25 MB)",
            grew < 4000)


def main() -> int:
    r = GRouter("rb", CONFIG)
    time.sleep(1.5)
    sock = os.path.join(r.home, "rb.ctl")
    if not r.alive() or not os.path.exists(sock):
        print("router did not start:\n" + r.tail())
        return 1
    A = RawHost("A", "10.0.1.10", "02:00:00:aa:00:10", "10.0.1.1", A_BIND, TUN1_LOCAL)
    B = RawHost("B", "10.0.2.10", "02:00:00:bb:00:10", "10.0.2.1", B_BIND, TUN2_LOCAL)
    time.sleep(0.3)
    A.resolve(); B.resolve()
    c = Case(r, sock)
    sections = {"cli": lambda: cli_typos(c), "arp": lambda: arp_buffer(c, A),
                "frag": lambda: fragmentation(c, A, B), "load": lambda: under_load(c, A, B),
                "local": lambda: to_the_router(c, A), "leak": lambda: no_route_leak(c, A)}
    only = [x for x in os.environ.get("ROBUSTNESS_ONLY", "").split(",") if x]   # e.g. arp,leak
    try:
        for name, run in sections.items():
            if not only or name in only:
                run()
        A.ping(B.ip, seq=99)
        time.sleep(0.5)
        c.check("still forwards A -> B afterwards",
                any(s == A.ip for s, _ttl in B.echo_requests))
    finally:
        A.stop(); B.stop(); r.stop()
    if c.failed:
        print(f"RESULT: FAIL — {len(c.failed)} check(s): {c.failed}")
        return 1
    print("RESULT: PASS — typos, unanswered ARP, fragments, DF, live reconfiguration, packets to "
          "the router and unroutable floods: the router answers and stays up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
