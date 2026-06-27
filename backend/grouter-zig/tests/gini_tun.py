"""Shared helpers for end-to-end gRouter tests over the legacy `tun` fabric.

The tun wire format is a raw Ethernet frame (dst6 / src6 / ethertype2 + payload)
carried as a UDP datagram. On the wire everything is standard network byte order,
so these emulated hosts send exactly what a real host would. A `Host` is an ARP
responder + ICMP echo responder + ping source; `GRouter` launches the real binary
with a generated config and tears it down.
"""
from __future__ import annotations

import os
import socket
import struct
import subprocess
import time

ROUTER_IP = "127.0.0.1"
ETH_IP = 0x0800
ETH_ARP = 0x0806
GROUTER_BIN = os.environ.get("GROUTER_BIN", "/tmp/build/grouter")


def mac2b(s): return bytes(int(x, 16) for x in s.split(":"))
def b2mac(b): return ":".join("%02x" % x for x in b)
def ip2b(s):  return bytes(int(x) for x in s.split("."))
def b2ip(b):  return ".".join(str(x) for x in b)


def eth(dst_mac, src_mac, etype, payload):
    return mac2b(dst_mac) + mac2b(src_mac) + struct.pack("!H", etype) + payload


def arp(op, sha, sip, tha, tip):
    return (struct.pack("!HHBBH", 1, ETH_IP, 6, 4, op)
            + mac2b(sha) + ip2b(sip) + mac2b(tha) + ip2b(tip))


def _cksum(data):
    s = 0
    for i in range(0, len(data) - len(data) % 2, 2):
        s += (data[i] << 8) + data[i + 1]
    if len(data) % 2:
        s += data[-1] << 8
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def icmp(kind, ident, seq, payload):
    base = struct.pack("!BBHHH", kind, 0, 0, ident, seq) + payload
    return struct.pack("!BBHHH", kind, 0, _cksum(base), ident, seq) + payload


def ip_pkt(src, dst, ttl, proto, payload):
    total = 20 + len(payload)
    base = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total, 0x1111, 0, ttl, proto, 0,
                       ip2b(src), ip2b(dst))
    base = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total, 0x1111, 0, ttl, proto,
                       _cksum(base), ip2b(src), ip2b(dst))
    return base + payload


class Host:
    """A host on the tun fabric: ARP responder, ICMP echo responder, ping source."""

    def __init__(self, name, ip, mac, gateway, bind_port, router_port):
        self.name, self.ip, self.mac, self.gateway = name, ip, mac, gateway
        self.router = (ROUTER_IP, router_port)
        self.gw_mac = None
        self.echo_requests = []     # (src_ip, ttl)
        self.echo_replies = []      # (src_ip, ttl, ident, seq)
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind((ROUTER_IP, bind_port))
        self.s.settimeout(0.3)
        self.alive = True
        import threading
        threading.Thread(target=self._loop, daemon=True).start()

    def _send(self, frame):
        self.s.sendto(frame, self.router)

    def _loop(self):
        while self.alive:
            try:
                data, _ = self.s.recvfrom(4096)
            except socket.timeout:
                continue
            self._handle(data)

    def _handle(self, data):
        if len(data) < 14:
            return
        etype = struct.unpack("!H", data[12:14])[0]
        body = data[14:]
        if etype == ETH_ARP and len(body) >= 28:
            op = struct.unpack("!H", body[6:8])[0]
            sha, sip, tip = body[8:14], body[14:18], body[24:28]
            if op == 1 and tip == ip2b(self.ip):
                self._send(eth(b2mac(sha), self.mac, ETH_ARP,
                               arp(2, self.mac, self.ip, b2mac(sha), b2ip(sip))))
            elif op == 2:
                self.gw_mac = b2mac(sha)
        elif etype == ETH_IP and len(body) >= 28 and body[9] == 1:
            src, dst, ttl = b2ip(body[12:16]), b2ip(body[16:20]), body[8]
            icmp_off = (body[0] & 0x0F) * 4
            itype, _, _, ident, seq = struct.unpack("!BBHHH", body[icmp_off:icmp_off + 8])
            pl = body[icmp_off + 8:]
            if itype == 8 and dst == self.ip:                  # echo request -> reply
                self.echo_requests.append((src, ttl))
                self._reply(src, ident, seq, pl)
            elif itype == 0:                                   # echo reply
                self.echo_replies.append((src, ttl, ident, seq))

    def resolve(self, gw_ip=None, tries=20):
        gw_ip = gw_ip or self.gateway
        for _ in range(tries):
            self._send(eth("ff:ff:ff:ff:ff:ff", self.mac, ETH_ARP,
                           arp(1, self.mac, self.ip, "00:00:00:00:00:00", gw_ip)))
            time.sleep(0.15)
            if self.gw_mac:
                return self.gw_mac
        return None

    def ping(self, dst, ident=0x1234, seq=1, payload=b"gini-ping"):
        if not self.gw_mac and not self.resolve():
            return False
        self._send(eth(self.gw_mac, self.mac, ETH_IP,
                       ip_pkt(self.ip, dst, 64, 1, icmp(8, ident, seq, payload))))
        return True

    def _reply(self, dst, ident, seq, payload):
        # Runs inside the recv thread — must NOT block on resolve() (the ARP reply
        # we'd wait for arrives on this same thread). Callers pre-resolve the gateway.
        if self.gw_mac:
            self._send(eth(self.gw_mac, self.mac, ETH_IP,
                           ip_pkt(self.ip, dst, 64, 1, icmp(0, ident, seq, payload))))

    def stop(self):
        self.alive = False


class GRouter:
    """Launches the real gRouter binary with a generated config; tears it down."""

    def __init__(self, name, config, home=None):
        self.name = name
        self.home = home or f"/tmp/build/run/{name}"
        os.makedirs(self.home, exist_ok=True)
        for stale in ("%s.pid" % name, "%s.port" % name):
            try:
                os.remove(os.path.join(self.home, stale))
            except OSError:
                pass
        self.conf = os.path.join(self.home, f"{name}.conf")
        open(self.conf, "w").write(config)
        self.log = os.path.join(self.home, f"{name}.out")
        self.proc = subprocess.Popen(
            [GROUTER_BIN, f"--config={self.conf}", f"--confpath={self.home}", name],
            stdin=subprocess.DEVNULL, stdout=open(self.log, "w"),
            stderr=subprocess.STDOUT, env=dict(os.environ, GINI_HOME=self.home),
            cwd=self.home)

    def alive(self):
        return self.proc.poll() is None

    def tail(self, n=2000):
        try:
            return open(self.log).read()[-n:]
        except OSError:
            return ""

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
