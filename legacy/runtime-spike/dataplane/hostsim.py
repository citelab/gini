"""A simulated machine for the no-Docker loopback test.

Behaves like a real host at the Ethernet layer (ARP + ICMP echo) but originates and
receives frames over a UDP Port instead of a TAP. This lets the loopback test drive
real pings through the real switch and router code with no containers, no TAP, and no
privileges — so the forwarding logic can be verified anywhere, including CI.
"""
from __future__ import annotations

import ipaddress

from .frame import (
    BROADCAST, ETH_ARP, ETH_IP, ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST, PROTO_ICMP,
    ZERO_MAC, build_arp, build_eth, build_icmp, build_ipv4,
    parse_arp, parse_eth, parse_icmp, parse_ipv4,
)
from .transport import Port, run_loop


class HostSim:
    def __init__(self, cfg: dict) -> None:
        self.name = cfg["name"]
        iface = ipaddress.ip_interface(cfg["ip"])
        self.ip = str(iface.ip)
        self.network = iface.network
        self.mac = cfg["mac"]
        self.gw = cfg.get("gw")
        self.port = Port.from_cfg(cfg["port"], name="eth0")
        self.arp: dict[str, str] = {}
        self.pending: list[tuple[str, bytes]] = []
        self.replies: set[tuple[int, int]] = set()    # (ident, seq) seen as echo replies

    # receive ---------------------------------------------------------------- #
    def handle(self, _inport: Port, frame: bytes) -> None:
        dst_mac, src_mac, etype, payload = parse_eth(frame)
        if etype == ETH_ARP:
            op, sha, spa, _tha, tpa = parse_arp(payload)
            if spa != "0.0.0.0":
                self.arp[spa] = sha
                self._flush()
            if op == 1 and tpa == self.ip:
                reply = build_arp(2, self.mac, self.ip, sha, spa)
                self.port.send(build_eth(sha, self.mac, ETH_ARP, reply))
            return
        if etype == ETH_IP:
            ip = parse_ipv4(payload)
            self.arp.setdefault(ip["src"], src_mac)
            if ip["dst"] != self.ip or ip["proto"] != PROTO_ICMP:
                return
            typ, ident, seq, data = parse_icmp(ip["payload"])
            if typ == ICMP_ECHO_REQUEST:
                rep = build_icmp(ICMP_ECHO_REPLY, ident, seq, data)
                pkt = build_ipv4(self.ip, ip["src"], PROTO_ICMP, rep)
                self.port.send(build_eth(src_mac, self.mac, ETH_IP, pkt))
            elif typ == ICMP_ECHO_REPLY:
                self.replies.add((ident, seq))

    # send ------------------------------------------------------------------- #
    def _next_hop(self, dst_ip: str) -> str:
        if ipaddress.ip_address(dst_ip) in self.network:
            return dst_ip
        return self.gw

    def ping(self, dst_ip: str, ident: int, seq: int) -> None:
        next_hop = self._next_hop(dst_ip)
        icmp = build_icmp(ICMP_ECHO_REQUEST, ident, seq)
        pkt = build_ipv4(self.ip, dst_ip, PROTO_ICMP, icmp)
        mac = self.arp.get(next_hop)
        if mac is not None:
            self.port.send(build_eth(mac, self.mac, ETH_IP, pkt))
        else:
            self.pending.append((next_hop, pkt))
            req = build_arp(1, self.mac, self.ip, ZERO_MAC, next_hop)
            self.port.send(build_eth(BROADCAST, self.mac, ETH_ARP, req))

    def _flush(self) -> None:
        still: list[tuple[str, bytes]] = []
        for next_hop, pkt in self.pending:
            mac = self.arp.get(next_hop)
            if mac is not None:
                self.port.send(build_eth(mac, self.mac, ETH_IP, pkt))
            else:
                still.append((next_hop, pkt))
        self.pending = still

    def run(self) -> None:
        run_loop([self.port], self.handle)
