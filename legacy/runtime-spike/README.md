# GINI R0 — portable user-space data-plane spike

Proves the core idea from `GINI_Runtime_and_gRouter_Plan.md`: a GINI data plane that is
**100% user space**, where every link is **Ethernet-in-UDP** and the **host only runs
Docker** — so it works the same on macOS, Linux, or Windows, with no host-kernel
networking (no netns, veth, bridges, or OVS).

```
  10.0.1.0/24:  m1(.10)   m2(.11)  ──┐
                                     ├─ s1 (user-space learning switch) ── r1.eth0(.1)
  10.0.2.0/24:  m3(.10) ── r1.eth1(.1)
```

- **m1, m2, m3** — containers, each with a TAP (`gini0`) bridged to the fabric over UDP.
- **fabric** — one container running the **switch** and the **gRouter** as *separate
  processes* (process-per-gRouter, supervised/restarted).
- Links carry one Ethernet frame per UDP datagram.

## What it demonstrates

- Same-subnet **L2 switching** (m1 ↔ m2) with MAC learning + flooding.
- Cross-subnet **L3 routing** through the user-space gRouter (ARP both sides, TTL
  decrement, checksum fix).
- Pinging the **gateway** (router answers ICMP echo).
- All of it portable — the host kernel is never touched.

## A. Prove the logic now — no Docker, no privileges

The forwarding/ARP/switching code runs anywhere with just Python (this is what CI runs):

```bash
python3 tests/loopback_test.py
```

Expected:

```
m1 -> m2  (L2 switch)     : PASS
m1 -> m3  (routed)        : PASS
m1 -> 10.0.1.1 (gateway)  : PASS
switch learned m1 & m2    : PASS

R0 DATA PLANE: ALL PASS
```

It wires the *real* `switch.py` and `grouter.py` to three simulated hosts over
localhost UDP and drives real ICMP — so the algorithm is verified without containers.

## B. Prove portability on your Mac — with Docker

```bash
cd runtime-spike
docker compose up --build -d

# same-subnet, through the user-space switch
docker compose exec m1 ping -c 3 10.0.1.11        # m1 -> m2

# cross-subnet, through the user-space gRouter
docker compose exec m1 ping -c 3 10.0.2.10        # m1 -> m3

# ping the gateway (router itself)
docker compose exec m1 ping -c 3 10.0.1.1

# throughput across the router
docker compose exec -d m3 iperf3 -s
docker compose exec m1 iperf3 -c 10.0.2.10

docker compose down
```

If those pings succeed on macOS, the kernel-free data plane is proven end-to-end and
the architecture is safe to build on.

## Files

```
dataplane/
  frame.py       Ethernet / ARP / IPv4 / ICMP helpers (stdlib only)
  transport.py   Ethernet-in-UDP Port + select loop
  switch.py      user-space learning switch (LearningSwitch)
  grouter.py     minimal user-space L3 router (ARP + forwarding + ICMP-to-self)
  shuttle.py     machine entrypoint: creates TAP gini0, bridges TAP <-> UDP
  hostsim.py     simulated host for the no-Docker test
tests/loopback_test.py   the no-Docker proof
docker/                  Dockerfiles + fabric supervisor
docker-compose.yml       the 4-container topology above
```

## Notes & troubleshooting

- **`/dev/net/tun` missing** — the machine containers need it (`--device /dev/net/tun`
  + `cap_add: NET_ADMIN`, already set in compose). It's present in Docker Desktop's VM
  and Colima. If a platform lacks it, the unprivileged `passt`/`pasta` path (in the
  plan) is the fallback.
- **Routing split** — experiment subnets (`10.0.0.0/8`) route via the TAP; the default
  route stays on Docker's `eth0` so the UDP transport between containers keeps working.
- **MTU** — `gini0` is set to 1400 to absorb UDP encapsulation without fragmentation.
- **Scope** — this gRouter is intentionally minimal (just enough to prove the plane).
  The real one is the modular, Lua-scriptable module-graph router from the plan; this
  spike's `grouter.py` is what that replaces.
