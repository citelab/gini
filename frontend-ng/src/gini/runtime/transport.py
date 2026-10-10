"""Ethernet-in-UDP transport: a Port is a UDP socket carrying one frame per datagram.

Same abstraction works two ways:
  * Docker: bind 0.0.0.0:P, peer_host = a service name (Docker DNS), peer_port = P.
  * Loopback test: bind 127.0.0.1 on distinct ports per endpoint.

A "port" on a switch/router/host is exactly this. Internal fabric links would later
swap this for an in-memory ring; the node code wouldn't change.
"""
from __future__ import annotations

import selectors
import socket


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
        if self._peer_ip is None:
            try:
                self._peer_ip = socket.gethostbyname(self.peer_host)
            except OSError:
                return None  # DNS not ready yet (Docker startup) — caller drops
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
            data, _ = self.sock.recvfrom(65535)
        except BlockingIOError:
            return None
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
