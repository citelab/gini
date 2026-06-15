"""Machine shuttle: the real entrypoint for a 'machine' container.

Creates one or more TAP interfaces inside the container (Linux; needs NET_ADMIN +
/dev/net/tun), assigns each its experiment IP, and bridges each TAP to its link's UDP
endpoint: TAP frame -> UDP datagram and back. A machine with several interfaces is
multi-homed (one TAP/IP per subnet); the default route uses the configured gateway.

The container's own docker eth0 stays as the management/transport NIC; experiment
traffic rides the TAPs (gini0, gini1, … so they don't clash with docker's eth0).

NODE_CONFIG = {"name":"m1", "gw":"10.0.1.1", "ifaces":[
    {"ip":"10.0.1.10/24","mac":"..","tap":"gini0","port":{...}},
    {"ip":"10.0.2.10/24","mac":"..","tap":"gini1","port":{...}} ]}
"""
from __future__ import annotations

import fcntl
import json
import os
import selectors
import struct
import subprocess
import sys

from .transport import Port

TUNSETIFF = 0x400454CA
IFF_TAP = 0x0002
IFF_NO_PI = 0x1000


def open_tap(name: str) -> int:
    fd = os.open("/dev/net/tun", os.O_RDWR)
    ifr = struct.pack("16sH", name.encode(), IFF_TAP | IFF_NO_PI)
    fcntl.ioctl(fd, TUNSETIFF, ifr)
    return fd


def configure_iface(name: str, mac: str, cidr: str) -> None:
    subprocess.run(["ip", "link", "set", name, "address", mac], check=True)
    subprocess.run(["ip", "addr", "add", cidr, "dev", name], check=True)
    subprocess.run(["ip", "link", "set", name, "up"], check=True)
    subprocess.run(["ip", "link", "set", name, "mtu", "1400"], check=True)   # absorb UDP encap


def add_default_route(gw: str, exp_net: str = "10.0.0.0/8") -> None:
    # Route the experiment supernet via the gateway. Connected /24s (from `ip addr
    # add`) are more specific and win for on-link subnets; this catches everything
    # else. The container's default route stays on docker eth0 for the UDP transport.
    subprocess.run(["ip", "route", "replace", exp_net, "via", gw], check=False)


def main() -> None:
    cfg = json.loads(os.environ["NODE_CONFIG"])
    ifaces = cfg.get("ifaces")
    if ifaces is None:                       # back-compat with the old single-iface shape
        ifaces = [{"ip": cfg["ip"], "mac": cfg["mac"],
                   "tap": cfg.get("tap", "gini0"), "port": cfg["port"]}]

    sel = selectors.DefaultSelector()
    fd_port: dict[int, Port] = {}            # tap fd -> its uplink Port
    port_fd: dict[int, int] = {}             # port socket fileno -> tap fd
    for idx, itf in enumerate(ifaces):
        tap = itf.get("tap", f"gini{idx}")
        fd = open_tap(tap)
        configure_iface(tap, itf["mac"], itf["ip"])
        port = Port.from_cfg(itf["port"], name=tap)
        fd_port[fd] = port
        port_fd[port.sock.fileno()] = fd
        sel.register(fd, selectors.EVENT_READ, "tap")
        sel.register(port.sock, selectors.EVENT_READ, "udp")
        print(f"[{cfg['name']}] {tap} {itf['ip']} <-> UDP "
              f"{port.peer_host}:{port.peer_port}", file=sys.stderr)

    if cfg.get("gw"):
        add_default_route(cfg["gw"])

    while True:
        for key, _ in sel.select():
            if key.data == "tap":
                frame = os.read(key.fileobj, 65535)          # TAP -> UDP
                if frame:
                    fd_port[key.fileobj].send(frame)
            else:
                port = fd_port[port_fd[key.fileobj.fileno()]]
                frame = port.recv()                          # UDP -> TAP
                if frame:
                    os.write(port_fd[key.fileobj.fileno()], frame)


if __name__ == "__main__":
    main()
