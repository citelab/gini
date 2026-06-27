"""No-Docker proof of the R0 data plane.

Wires real switch + router code to three simulated hosts over localhost UDP and drives
real pings:

    10.0.1.0/24:  m1(.10)  m2(.11)  r1.eth0(.1)        <- via learning switch s1
    10.0.2.0/24:  m3(.10)  r1.eth1(.1)                 <- m3 wired straight to r1.eth1

Verifies:
  * m1 -> m2  : same-subnet L2 switching (router not involved)
  * m1 -> m3  : cross-subnet routing through gRouter (ARP both sides, TTL decrement)
  * m1 -> 10.0.1.1 : pinging the gateway (router answers ICMP echo)
  * the switch learned m1 and m2 MACs on the right ports

Run:  python tests/loopback_test.py      (exit 0 = pass)
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dataplane.grouter import Router          # noqa: E402
from dataplane.hostsim import HostSim          # noqa: E402
from dataplane.switch import LearningSwitch    # noqa: E402

LH = "127.0.0.1"


def p(bind, peer_port):
    return {"bind_host": LH, "bind_port": bind, "peer_host": LH, "peer_port": peer_port}


def build():
    # distinct localhost ports per endpoint (Docker uses same port across containers)
    m1 = HostSim({"name": "m1", "ip": "10.0.1.10/24", "gw": "10.0.1.1",
                  "mac": "02:00:00:00:01:10", "port": p(6001, 6101)})
    m2 = HostSim({"name": "m2", "ip": "10.0.1.11/24", "gw": "10.0.1.1",
                  "mac": "02:00:00:00:01:11", "port": p(6002, 6102)})
    m3 = HostSim({"name": "m3", "ip": "10.0.2.10/24", "gw": "10.0.2.1",
                  "mac": "02:00:00:00:02:10", "port": p(6004, 6204)})
    s1 = LearningSwitch({"name": "s1", "ports": [
        p(6101, 6001),    # port0 -> m1
        p(6102, 6002),    # port1 -> m2
        p(6103, 6203),    # port2 -> r1.eth0
    ]})
    r1 = Router({"name": "r1", "ifaces": [
        {"ip": "10.0.1.1/24", "mac": "02:00:00:00:01:01", "port": p(6203, 6103)},
        {"ip": "10.0.2.1/24", "mac": "02:00:00:00:02:01", "port": p(6204, 6004)},
    ]})
    return m1, m2, m3, s1, r1


def wait_for(host, ident, sender, dst_ip, timeout=3.0):
    """Retry the ping a few times (covers first-packet ARP) and wait for a reply."""
    deadline = time.time() + timeout
    seq = 0
    while time.time() < deadline:
        seq += 1
        sender.ping(dst_ip, ident, seq)
        end = time.time() + 0.4
        while time.time() < end:
            if (ident, seq) in host.replies or any(i == ident for i, _ in host.replies):
                return True
            time.sleep(0.02)
    return False


def main() -> int:
    m1, m2, m3, s1, r1 = build()
    for node in (s1, r1, m1, m2, m3):
        threading.Thread(target=node.run, daemon=True).start()
    time.sleep(0.3)

    ok_l2 = wait_for(m1, 0x11, m1, m2.ip)
    ok_routed = wait_for(m1, 0x22, m1, m3.ip)
    ok_gw = wait_for(m1, 0x33, m1, "10.0.1.1")
    learned = (m1.mac in s1.table and m2.mac in s1.table)

    print(f"m1 -> m2  (L2 switch)     : {'PASS' if ok_l2 else 'FAIL'}")
    print(f"m1 -> m3  (routed)        : {'PASS' if ok_routed else 'FAIL'}")
    print(f"m1 -> 10.0.1.1 (gateway)  : {'PASS' if ok_gw else 'FAIL'}")
    print(f"switch learned m1 & m2    : {'PASS' if learned else 'FAIL'}")
    if learned:
        print(f"  switch table          : "
              + ", ".join(f"{m.split(':')[-1]}->{pt.name}" for m, pt in s1.table.items()))

    all_ok = ok_l2 and ok_routed and ok_gw and learned
    print("\nR0 DATA PLANE:", "ALL PASS" if all_ok else "FAILURES")
    return 0 if all_ok else 1


# pytest entry points
def test_l2_and_routing():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
