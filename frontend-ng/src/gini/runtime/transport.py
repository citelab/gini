"""Ethernet-in-UDP transport: a Port is a UDP socket carrying one frame per datagram.

Same abstraction works two ways:
  * Docker: bind 0.0.0.0:P, peer_host = a service name (Docker DNS), peer_port = P.
  * Loopback test: bind 127.0.0.1 on distinct ports per endpoint.

A "port" on a switch/router/host is exactly this. Internal fabric links would later
swap this for an in-memory ring; the node code wouldn't change.
"""
from __future__ import annotations

import ipaddress
import os
import selectors
import socket
import struct
import time

#: Where a container's DNS server is named. Docker's embedded resolver is 127.0.0.11; Podman's
#: aardvark-dns is the network gateway. Both are named here, so neither is hard-coded.
RESOLV_CONF = "/etc/resolv.conf"
#: How long to wait for that server. It is on loopback or the bridge and answers in a millisecond;
#: this only bounds a server that is not there, and it is paid on the shuttle's one thread.
DNS_TIMEOUT_S = 0.3
#: After a lookup that found nothing — the peer's container is not registered yet — wait this long
#: before asking again, rather than asking once per frame.
RETRY_S = 0.5


def _nameservers(path: str = RESOLV_CONF) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.split()[1] for ln in f
                    if ln.split()[:1] == ["nameserver"] and len(ln.split()) > 1]
    except OSError:
        return []


def _skip_name(msg: bytes, i: int) -> int:
    while i < len(msg):
        n = msg[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:                         # a compression pointer ends the name
            return i + 2
        i += 1 + n
    raise ValueError("name runs off the end")


def ask_dns(name: str, server: str, port: int = 53,
            timeout: float = DNS_TIMEOUT_S) -> str | None:
    """`name`'s IPv4 address, asked of `server` ITSELF — never of /etc/hosts. None if it has none.

    Raises OSError when the server cannot be reached at all, so a caller can tell "no such name"
    from "no DNS here".
    """
    qid = int.from_bytes(os.urandom(2), "big")
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)          # one question, recursion asked
    for label in name.rstrip(".").split("."):
        q += bytes([len(label)]) + label.encode("ascii")
    q += b"\x00" + struct.pack(">HH", 1, 1)                        # A, IN
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(q, (server, port))
        msg, _ = s.recvfrom(4096)
    try:
        rid, flags, qd, an = struct.unpack(">HHHH", msg[:8])
        if rid != qid or flags & 0x000F:                           # not ours, or an error rcode
            return None
        i = 12
        for _ in range(qd):
            i = _skip_name(msg, i) + 4
        for _ in range(an):
            i = _skip_name(msg, i)
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", msg[i:i + 10])
            i += 10
            if rtype == 1 and rdlen == 4:
                return socket.inet_ntoa(msg[i:i + 4])
            i += rdlen
    except (struct.error, ValueError):
        return None
    return None


def resolve_peer(host: str, servers: list[str] | None = None) -> str | None:
    """A peer CONTAINER's address on the engine's network, from DNS and never from /etc/hosts.

    The hosts file is the lab's, not ours. gBuilder writes the overlay names into it just after
    Run — `R1  R1-eth0 …  10.0.1.1` — so that `ping R1` from a station rides the drawn network.
    Name lookup there ignores case and reads the file before DNS, so once that block is in, the
    container name `r1` resolves to 10.0.1.1 — an address INSIDE the lab. A shuttle that looked its
    peer up after the write sent every frame into its own tap, and the machine could send nothing
    for the rest of the run: no ping, no multicast, nothing. Whether a machine lost the race
    depended on whether its first frame went out in the half-second before the write, which is
    why one machine of three was dead and the next Run might be fine.

    Asking the DNS server named in resolv.conf directly cannot meet the hosts file. Only with no
    DNS server at all (not a container) does this fall back to the ordinary lookup.
    """
    try:
        return str(ipaddress.IPv4Address(host))                    # already an address
    except ValueError:
        pass
    servers = _nameservers() if servers is None else servers
    reached = False
    for srv in servers:
        try:
            ip = ask_dns(host, srv)
            reached = True
        except OSError:
            continue
        if ip:
            return ip
    if reached:
        return None                    # DNS is there and does not know it YET: ask again later
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


class Port:
    def __init__(self, bind_port: int, peer_host: str, peer_port: int,
                 bind_host: str = "0.0.0.0", name: str = "") -> None:
        self.name = name
        # The drawn link this port is one end of (the compiler's port["link"]["id"]), and whether
        # that link is down. A down link carries nothing either way: send() drops, recv() drains
        # and discards -- the socket is never left to fill, so nothing sent while the link was down
        # arrives as a stale burst when it comes back (the gRouter does the same, gnet_rx_discard).
        # Every Python node -- machine shuttle, switch, gbridge, the in-process simulator -- goes
        # through here, so this is the one place a link failure has to be honoured on this side.
        self.link_id = ""
        self.down = False
        self.peer_host = peer_host
        self.peer_port = peer_port
        self._peer_ip: str | None = None
        self._retry_at = 0.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind_host, bind_port))
        self.sock.setblocking(False)

    @classmethod
    def from_cfg(cls, cfg: dict, name: str = "") -> "Port":
        port = cls(cfg["bind_port"], cfg["peer_host"], cfg["peer_port"],
                   cfg.get("bind_host", "0.0.0.0"), name)
        port.link_id = str((cfg.get("link") or {}).get("id", "") or "")
        return port

    def _resolve(self) -> str | None:
        """The peer's address: from DNS (see `resolve_peer`), or as LEARNED from what it sent us.

        Lazy, because a peer's container may not be registered yet when this one starts; the
        frames sent before then are dropped, as on any link that is not up yet.
        """
        if self._peer_ip is None and time.monotonic() >= self._retry_at:
            self._peer_ip = resolve_peer(self.peer_host)
            if self._peer_ip is None:
                self._retry_at = time.monotonic() + RETRY_S
        return self._peer_ip

    def send(self, frame: bytes) -> None:
        if self.down:
            return                                   # a cut cable carries nothing
        ip = self._resolve()
        if ip is None:
            return
        try:
            self.sock.sendto(frame, (ip, self.peer_port))
        except OSError:
            pass

    def recv(self) -> bytes | None:
        try:
            data, src = self.sock.recvfrom(65535)
        except BlockingIOError:
            return None
        # LEARN the peer from what it sends, as the gRouter does (tun.c, tun_recvfrom). Every
        # link's far end sends from the port we send to, so a datagram from that port IS the
        # peer, at the address it really has. That heals an address that was wrong when we
        # looked it up — or that changed, because the peer's container came back.
        if src[1] == self.peer_port and src[0] != self._peer_ip:
            self._peer_ip = src[0]
        while self.down:                             # drain and discard: down receives nothing
            try:
                self.sock.recvfrom(65535)
            except BlockingIOError:
                return None
        return data


def run_loop(ports: list[Port], handler, tick=None, tick_interval: float = 0.5,
             stop=None) -> None:
    """Select over all ports; call handler(port, frame) for each frame.

    handler receives the Port the frame arrived on, so a node can identify its
    ingress port without any per-frame addressing.

    `stop` is an optional `threading.Event` that ends the loop. In production nothing passes one:
    a node IS its container's process and the loop is meant to run until the container stops, so
    the default is exactly the `while True:` this has always been. It exists because the same
    nodes are also run IN-PROCESS by `orchestrator.simulate()`, where a loop that cannot end
    outlives whatever started it — and in the test suite that meant threads still selecting on
    live sockets while a later Qt test tore its widgets down, which is a segfault with no
    connection to the test that appears to fail. The loop already wakes every `tick_interval`, so
    checking a flag costs nothing.
    """
    sel = selectors.DefaultSelector()
    for p in ports:
        sel.register(p.sock, selectors.EVENT_READ, p)
    while not (stop is not None and stop.is_set()):
        events = sel.select(timeout=tick_interval)
        for key, _ in events:
            port: Port = key.data
            while True:
                frame = port.recv()
                if frame is None:
                    break
                handler(port, frame)
        if tick is not None:
            tick()
