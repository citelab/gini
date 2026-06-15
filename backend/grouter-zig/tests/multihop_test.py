#!/usr/bin/env python3
"""Two-router multi-hop proof for the REAL C gRouter.

  A 10.0.1.10 -- tun1[ R1 ]tun2 10.0.2.1 <==> 10.0.2.2 tun1[ R2 ]tun2 -- B 10.0.3.10
       10.0.1.0/24            10.0.2.0/24 (router-router)          10.0.3.0/24

Two genuine gRouter processes joined by a router-to-router tun link. A pings B; the
echo crosses BOTH routers (each decrements TTL: 64 -> 62) and B's reply comes back the
same way. Exercises router<->router tun links, ARP between routers, and -gw next-hop
routes. All over literal-port tun (the portable-fabric wiring).

  GROUTER_BIN=/path/to/grouter python3 multihop_test.py
"""
import sys
import time

from gini_tun import ROUTER_IP, GRouter, Host

# UDP ports (all < 32768): (router-local, peer-bind)
A_R1L, A_BIND = 21001, 21002        # A  <-> R1.tun1
R1L_R2, R2L_R1 = 21003, 21004       # R1.tun2 <-> R2.tun1  (router-to-router)
B_R2L, B_BIND = 21005, 21006        # R2.tun2 <-> B

R1_CONFIG = f"""# R1: A-side (tun1) + link to R2 (tun2)
ifconfig add tun1 -dstip {ROUTER_IP} -dstport {A_BIND}  -addr 10.0.1.1 -hwaddr 02:00:00:00:01:01 -mtu 1400 -srcport {A_R1L}
ifconfig add tun2 -dstip {ROUTER_IP} -dstport {R2L_R1} -addr 10.0.2.1 -hwaddr 02:00:00:00:02:01 -mtu 1400 -srcport {R1L_R2}
route add -dev tun1 -net 10.0.1.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.2.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.3.0 -netmask 255.255.255.0 -gw 10.0.2.2
"""

R2_CONFIG = f"""# R2: link to R1 (tun1) + B-side (tun2)
ifconfig add tun1 -dstip {ROUTER_IP} -dstport {R1L_R2} -addr 10.0.2.2 -hwaddr 02:00:00:00:03:01 -mtu 1400 -srcport {R2L_R1}
ifconfig add tun2 -dstip {ROUTER_IP} -dstport {B_BIND}  -addr 10.0.3.1 -hwaddr 02:00:00:00:04:01 -mtu 1400 -srcport {B_R2L}
route add -dev tun1 -net 10.0.2.0 -netmask 255.255.255.0
route add -dev tun2 -net 10.0.3.0 -netmask 255.255.255.0
route add -dev tun1 -net 10.0.1.0 -netmask 255.255.255.0 -gw 10.0.2.1
"""


def main():
    R1 = GRouter("mh_r1", R1_CONFIG)
    R2 = GRouter("mh_r2", R2_CONFIG)
    time.sleep(1.8)
    for R in (R1, R2):
        if not R.alive():
            print(f"{R.name} EXITED EARLY\n" + R.tail()); _stop(R1, R2); return 1

    A = Host("A", "10.0.1.10", "02:00:00:aa:00:10", "10.0.1.1", A_BIND, A_R1L)
    B = Host("B", "10.0.3.10", "02:00:00:bb:00:10", "10.0.3.1", B_BIND, B_R2L)
    time.sleep(0.3)
    A.resolve(); B.resolve()

    print("[1] A -> ping 10.0.3.10 (across TWO routers) ...")
    if not A.ping("10.0.3.10"):
        print("    A gateway unresolved"); _stop(R1, R2, A, B); return 2

    fwd = rev = False
    for _ in range(40):
        time.sleep(0.2)
        if any(s == "10.0.1.10" and ttl == 62 for (s, ttl) in B.echo_requests):
            fwd = True
        if any(s == "10.0.3.10" and ttl == 62 for (s, ttl, _i, _q) in A.echo_replies):
            rev = True
        if fwd and rev:
            break

    if fwd:
        print("[2] B received echo REQUEST from 10.0.1.10 at ttl=62   (2 hops: 64->63->62)")
    if rev:
        print("[3] A received echo REPLY  from 10.0.3.10 at ttl=62   (2 hops back)")

    logs = "=== R1 ===\n" + R1.tail(800) + "\n=== R2 ===\n" + R2.tail(800)
    _stop(R1, R2, A, B)
    print()
    if fwd and rev:
        print("RESULT: PASS — real gRouter chain forwards A<->B across 2 hops, TTL 64->62.")
        return 0
    print(f"RESULT: FAIL — forward={fwd} reverse={rev}")
    print(logs)
    return 1


def _stop(*objs):
    for o in objs:
        o.stop()


if __name__ == "__main__":
    sys.exit(main())
