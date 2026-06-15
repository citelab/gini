"""Minimal user-space L3 gRouter for the R0 spike.

Enough to prove the data plane end-to-end across subnets: ARP (answers for its own
interfaces, resolves next hops, queues packets pending resolution), connected-route
forwarding with TTL decrement + checksum fix, and ICMP echo to its own interfaces
(so you can ping the gateway). This is intentionally NOT the modular module-graph
router from the plan — it's the smallest thing that proves portability.

Run as a process: ROUTER_CONFIG='{"name":"r1","ifaces":[...]}' python -m dataplane.grouter
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys

from .frame import (
    BROADCAST, ETH_ARP, ETH_IP, ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST, PROTO_ICMP,
    ZERO_MAC, build_arp, build_eth, build_icmp, build_ipv4, dec_ttl,
    parse_arp, parse_eth, parse_icmp, parse_ipv4,
)
from .control import maybe_start
from .transport import Port, run_loop


class Iface:
    def __init__(self, cfg: dict, name: str) -> None:
        self.port = Port.from_cfg(cfg["port"], name=name)
        iface = ipaddress.ip_interface(cfg["ip"])
        self.ip = str(iface.ip)
        self.network = iface.network
        self.mac = cfg["mac"]


class Router:
    def __init__(self, cfg: dict) -> None:
        self.name = cfg["name"]
        self.ifaces = [Iface(f, name=f"eth{i}") for i, f in enumerate(cfg["ifaces"])]
        self.arp: dict[str, str] = {}                 # ip -> mac
        self.pending: dict[str, list[tuple[Iface, bytes]]] = {}
        self.log = cfg.get("log", False)
        self.pipeline: list[tuple[str, str]] = []     # Z2: inline modules (type, param)
        self._ctrl = maybe_start(self.name, self._control, f"router {self.name}")

    def _control(self, cmd: str) -> str:
        cmd = cmd.lower()
        if cmd.startswith("gpipe"):
            return self._gpipe(cmd[len("gpipe"):].strip())
        if cmd in ("help", "?", "h"):
            return "commands: interfaces, routes, arp, gpipe, help, exit"
        if cmd in ("interfaces", "int", "if"):
            return "\n".join(f"  {i.port.name}  {i.ip}/{i.network.prefixlen}  {i.mac}"
                             for i in self.ifaces)
        if cmd in ("routes", "route"):
            return "\n".join(f"  {i.network}  dev {i.port.name}" for i in self.ifaces)
        if cmd == "arp":
            if not self.arp:
                return "(arp cache empty)"
            return "\n".join(f"  {ip}  ->  {mac}" for ip, mac in self.arp.items())
        return f"unknown command: {cmd} (try 'help')"

    def _gpipe(self, args: str) -> str:
        """Z2 inline module pipeline — same vocabulary as the C gRouter's gr_control."""
        parts = args.split()
        if not parts or parts[0] in ("help", ""):
            return "gpipe: add acl <cidr> | add nat <ip> | add counter | list | clear | trace <ip>"
        op = parts[0]
        if op == "add" and len(parts) >= 2:
            kind = parts[1]
            if kind in ("acl", "nat") and len(parts) >= 3:
                self.pipeline.append((kind, parts[2]))
                return f"added {kind} {parts[2]}  (pipeline: {len(self.pipeline)})"
            if kind == "counter":
                self.pipeline.append(("counter", ""))
                return f"added counter  (pipeline: {len(self.pipeline)})"
            return f"unknown module: {kind}"
        if op == "list":
            chain = " ".join(f"-> [{i}:{t}]" for i, (t, _) in enumerate(self.pipeline))
            return f"base: parse {chain} -> route -> rewrite"
        if op == "clear":
            self.pipeline.clear()
            return "pipeline cleared (base only)"
        if op == "trace" and len(parts) >= 2:
            dst = parts[1]
            out = [f"trace dst {dst}:"]
            dropped = False
            for i, (t, p) in enumerate(self.pipeline):
                verdict = "CONTINUE"
                if t == "acl":
                    try:
                        if ipaddress.ip_address(dst) in ipaddress.ip_network(p, strict=False):
                            verdict, dropped = "DROP", True
                    except ValueError:
                        pass
                out.append(f"  {i}. {t:<8} -> {verdict}")
                if dropped:
                    break
            if not dropped:
                out.append("  -> base forwarding (route, rewrite, egress)")
            return "\n".join(out)
        return f"gpipe: unknown ({args})"

    # routing ---------------------------------------------------------------- #
    def _egress_for(self, dst_ip: str) -> Iface | None:
        addr = ipaddress.ip_address(dst_ip)
        for itf in self.ifaces:
            if addr in itf.network:          # connected route
                return itf
        return None

    def _ingress(self, port: Port) -> Iface:
        return next(itf for itf in self.ifaces if itf.port is port)

    # main handler ----------------------------------------------------------- #
    def handle(self, inport: Port, frame: bytes) -> None:
        ingress = self._ingress(inport)
        dst_mac, src_mac, etype, payload = parse_eth(frame)

        if etype == ETH_ARP:
            op, sha, spa, _tha, tpa = parse_arp(payload)
            if spa != "0.0.0.0":
                self.arp[spa] = sha
                self._flush(spa)
            if op == 1 and tpa == ingress.ip:      # who-has my IP -> reply
                reply = build_arp(2, ingress.mac, ingress.ip, sha, spa)
                inport.send(build_eth(sha, ingress.mac, ETH_ARP, reply))
            return

        if etype == ETH_IP:
            ip = parse_ipv4(payload)
            self.arp.setdefault(ip["src"], src_mac)   # learn from data plane

            if any(ip["dst"] == itf.ip for itf in self.ifaces):
                self._handle_local(ingress, inport, src_mac, ip)
                return

            egress = self._egress_for(ip["dst"])
            if egress is None:
                return                                 # no route -> drop
            new_ip = dec_ttl(payload)
            if new_ip is None:
                return                                 # TTL expired -> drop
            self._forward(egress, ip["dst"], new_ip)

    def _handle_local(self, ingress: Iface, inport: Port, src_mac: str, ip: dict) -> None:
        if ip["proto"] != PROTO_ICMP:
            return
        typ, ident, seq, data = parse_icmp(ip["payload"])
        if typ == ICMP_ECHO_REQUEST:
            rep = build_icmp(ICMP_ECHO_REPLY, ident, seq, data)
            pkt = build_ipv4(ip["dst"], ip["src"], PROTO_ICMP, rep)
            inport.send(build_eth(src_mac, ingress.mac, ETH_IP, pkt))

    def _forward(self, egress: Iface, next_hop: str, ip_pkt: bytes) -> None:
        mac = self.arp.get(next_hop)
        if mac is not None:
            egress.port.send(build_eth(mac, egress.mac, ETH_IP, ip_pkt))
            if self.log:
                print(f"[{self.name}] fwd -> {next_hop} via {egress.port.name}",
                      file=sys.stderr)
        else:                                          # resolve, queue, retransmit-safe
            self.pending.setdefault(next_hop, []).append((egress, ip_pkt))
            req = build_arp(1, egress.mac, egress.ip, ZERO_MAC, next_hop)
            egress.port.send(build_eth(BROADCAST, egress.mac, ETH_ARP, req))

    def _flush(self, ip: str) -> None:
        queued = self.pending.pop(ip, [])
        mac = self.arp.get(ip)
        if mac is None:
            return
        for egress, ip_pkt in queued:
            egress.port.send(build_eth(mac, egress.mac, ETH_IP, ip_pkt))

    def run(self) -> None:
        nets = ", ".join(str(i.network) for i in self.ifaces)
        print(f"[{self.name}] gRouter up: {nets}", file=sys.stderr)
        run_loop([itf.port for itf in self.ifaces], self.handle)


def main() -> None:
    cfg = json.loads(os.environ["ROUTER_CONFIG"])
    Router(cfg).run()


if __name__ == "__main__":
    main()
