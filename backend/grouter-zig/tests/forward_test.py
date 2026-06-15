#!/usr/bin/env python3
"""End-to-end round-trip proof for the REAL C gRouter (single router).

  A 10.0.1.10  <-->  tun1 10.0.1.1 [ R ] 10.0.2.1 tun2  <-->  B 10.0.2.10

A pings B across subnets; B (a real ICMP responder) replies; A receives the reply.
We confirm BOTH directions forward with TTL decremented 64 -> 63. Same proof we ran
for the Python router, on the genuine ~20k-line GINI data plane.

  GROUTER_BIN=/path/to/grouter python3 forward_test.py
"""
import sys
import time

from gini_tun import ROUTER_IP, GRouter, Host

TUN1_LOCAL, A_BIND = 20001, 20002       # tun1 <-> host A
TUN2_LOCAL, B_BIND = 20003, 20004       # tun2 <-> host B

CONFIG = f"""# gRouter native config: two tun interfaces, literal ports
ifconfig add tun1 -dstip {ROUTER_IP} -dstport {A_BIND} -addr 10.0.1.1 -hwaddr 02:00:00:00:01:01 -mtu 1400 -srcport {TUN1_LOCAL}
ifconfig add tun2 -dstip {ROUTER_IP} -dstport {B_BIND} -addr 10.0.2.1 -hwaddr 02:00:00:00:02:01 -mtu 1400 -srcport {TUN2_LOCAL}
route add -dev tun1 -net 10.0.1.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.2.0 -netmask 255.255.255.0
"""


def main():
    R = GRouter("r1", CONFIG)
    time.sleep(1.5)
    if not R.alive():
        print("ROUTER EXITED EARLY\n" + R.tail())
        return 1

    A = Host("A", "10.0.1.10", "02:00:00:aa:00:10", "10.0.1.1", A_BIND, TUN1_LOCAL)
    B = Host("B", "10.0.2.10", "02:00:00:bb:00:10", "10.0.2.1", B_BIND, TUN2_LOCAL)
    time.sleep(0.3)
    A.resolve(); B.resolve()        # pre-learn gateway MACs (so echo-reply doesn't block)

    print("[1] A -> ping 10.0.2.10 (across the router) ...")
    if not A.ping("10.0.2.10"):
        print("    A could not resolve its gateway"); R.stop(); A.stop(); B.stop(); return 2

    fwd = rev = False
    for _ in range(30):
        time.sleep(0.2)
        if any(s == "10.0.1.10" and ttl == 63 for (s, ttl) in B.echo_requests):
            fwd = True
        if any(s == "10.0.2.10" and ttl == 63 for (s, ttl, _i, _q) in A.echo_replies):
            rev = True
        if fwd and rev:
            break

    if fwd:
        print("[2] B received echo REQUEST from 10.0.1.10 at ttl=63   (A->B forwarded)")
    if rev:
        print("[3] A received echo REPLY  from 10.0.2.10 at ttl=63   (B->A forwarded)")

    log = R.tail()
    R.stop(); A.stop(); B.stop()
    print()
    if fwd and rev:
        print("RESULT: PASS — real gRouter round-trips A<->B across subnets, TTL 64->63 each way.")
        return 0
    print(f"RESULT: FAIL — forward={fwd} reverse={rev}")
    print("--- router log ---\n" + log[-1500:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
