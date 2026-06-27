#!/usr/bin/env python3
"""The gRouter control socket exposes the real CLI to a console / the Router Lab.

Launches a router, then drives its <name>.ctl Unix socket the way grconsole.py and the
GUI do: each command runs through the real CLI dispatch and returns its printed output.

  GROUTER_BIN=/path/to/grouter python3 test_rctl.py
"""
import os
import socket
import sys
import time

from gini_tun import GRouter

END = "__END__"
CONFIG = """ifconfig add tun1 -dstip 127.0.0.1 -dstport 24102 -addr 10.0.1.1 -hwaddr 02:00:00:00:01:01 -mtu 1400 -srcport 24101
ifconfig add tun2 -dstip 127.0.0.1 -dstport 24104 -addr 10.0.2.1 -hwaddr 02:00:00:00:02:01 -mtu 1400 -srcport 24103
route add -dev tun1 -net 10.0.1.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.2.0 -netmask 255.255.255.0
"""


def query(sock_path: str, line: str, timeout: float = 4.0) -> str:
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


def main() -> int:
    r = GRouter("rctl_t", CONFIG)
    time.sleep(1.5)
    sock = os.path.join(r.home, "rctl_t.ctl")
    try:
        if not r.alive():
            print("router died:\n", r.tail()); return 1
        if not os.path.exists(sock):
            print("control socket not created:", sock, "\n", r.tail()); return 1

        routes = query(sock, "route show")
        assert "10.0.1.0" in routes and "10.0.2.0" in routes, routes
        ifaces = query(sock, "ifconfig show")
        assert "tun1" in ifaces and "10.0.1.1" in ifaces, ifaces
        assert "added acl" in query(sock, "gpipe add acl 10.0.9.0/24")
        assert "DROP" in query(sock, "gpipe trace 10.0.9.5")
        assert "base forwarding" in query(sock, "gpipe trace 10.0.2.7")
        print("test_rctl: ALL PASS — real CLI reachable over the control socket")
        return 0
    finally:
        r.stop()


if __name__ == "__main__":
    sys.exit(main())
