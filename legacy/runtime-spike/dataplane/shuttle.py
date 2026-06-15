"""Machine shuttle: the real entrypoint for a 'machine' container.

Creates a TAP interface inside the container (Linux, needs NET_ADMIN + /dev/net/tun),
assigns the experiment IP/route, and bridges that TAP to the link's UDP endpoint:
TAP frame -> UDP datagram, and back. The container's own docker eth0 stays as the
management/transport NIC; experiment traffic rides the TAP (named 'gini0' so it
doesn't clash with docker's eth0).

Because the TAP lives inside the container, this is portable: the host can be macOS,
Linux, or Windows — Docker provides the Linux kernel that owns the TAP.

Run: NODE_CONFIG='{"name":"m1","ip":"10.0.1.10/24","gw":"10.0.1.1","mac":"..","port":{...}}' \
     python -m dataplane.shuttle
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


def configure(name: str, mac: str, cidr: str, gw: str | None,
              exp_net: str = "10.0.0.0/8") -> None:
    subprocess.run(["ip", "link", "set", name, "address", mac], check=True)
    subprocess.run(["ip", "addr", "add", cidr, "dev", name], check=True)
    subprocess.run(["ip", "link", "set", name, "up"], check=True)
    # lower MTU to absorb UDP encapsulation without fragmentation
    subprocess.run(["ip", "link", "set", name, "mtu", "1400"], check=True)
    # IMPORTANT: route only the *experiment* supernet via the TAP. The default route
    # is left on docker's eth0 so the UDP transport (to peer services) keeps working.
    if gw:
        subprocess.run(["ip", "route", "replace", exp_net, "via", gw, "dev", name],
                       check=False)


def main() -> None:
    cfg = json.loads(os.environ["NODE_CONFIG"])
    tap_name = cfg.get("tap", "gini0")
    fd = open_tap(tap_name)
    configure(tap_name, cfg["mac"], cfg["ip"], cfg.get("gw"),
              cfg.get("exp_net", "10.0.0.0/8"))
    port = Port.from_cfg(cfg["port"], name="uplink")
    print(f"[{cfg['name']}] shuttle up: {tap_name} {cfg['ip']} <-> UDP "
          f"{port.peer_host}:{port.peer_port}", file=sys.stderr)

    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ, "tap")
    sel.register(port.sock, selectors.EVENT_READ, "udp")
    while True:
        for key, _ in sel.select():
            if key.data == "tap":
                frame = os.read(fd, 65535)         # TAP -> UDP
                if frame:
                    port.send(frame)
            else:
                frame = port.recv()                 # UDP -> TAP
                if frame:
                    os.write(fd, frame)


if __name__ == "__main__":
    main()
