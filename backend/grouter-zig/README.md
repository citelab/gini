# Z0 — the real gRouter, built with Zig, in the fabric

Phase **Z0** of the gRouter modernization (see `GINI Project/GINI_gRouter_Modularization_and_Zig_Plan.md`):
build the **existing** C gRouter (`backend/src/grouter/`, ~20k lines, unchanged) with a
modern Zig toolchain and run it in the portable fabric over its native `tun` (UDP)
interface — restoring the familiar CLI and full router before any C is reshaped.

## Files

```
backend/build.zig                 # `zig build` -> zig-out/bin/grouter (Zig 0.16 API)
backend/grouter-zig/
  build.sh                        # robust `zig cc` build (used by Docker)
  Dockerfile                      # Linux image: libslack + readline + zig-built grouter
  run_grouter.py                  # entrypoint: ROUTER_CONFIG -> ifconfig/route -> grouter
  tests/gini_tun.py               # shared e2e harness: emulated hosts + GRouter launcher
  tests/forward_test.py           # 1 router, A<->B round-trip (TTL 64->63)
  tests/multihop_test.py          # 2 routers, A<->B over 2 hops (TTL 64->62)
  README.md                       # this file
```

## What's PROVEN (built + forwards, reproducibly)

The real C gRouter (GINI **v2.1**, unchanged ~20k-line C) builds with `zig cc` + libslack
and **routes real traffic end-to-end**:

- **`forward_test.py`** — one router, two subnets: A pings B, B replies, A receives it.
  Both directions forward with **TTL 64 -> 63**.
- **`multihop_test.py`** — two real gRouter processes joined by a router-to-router `tun`
  link with `-gw` next-hop routes: A<->B across **two hops, TTL 64 -> 62**.

The hosts in these tests speak the genuine `tun` wire format (a standard Ethernet frame
in a UDP datagram, standard network byte order), so they send exactly what a real machine
would. Run them against any built binary via `GROUTER_BIN`.

### Build flags (legacy C under a modern toolchain)

The code predates modern compiler hardening, so `build.sh`/`build.zig` pass:
`-fcommon` (header-defined tentative globals must merge), `-Wno-implicit-function-declaration`
+ `-Wno-int-conversion` (K&R style), `-fno-sanitize=undefined` (zig cc traps UB by default),
and `-fno-stack-protector -D_FORTIFY_SOURCE=0` (latent overwrites trip the canary).
**libslack must be built with the same hardening-off flags** (the Dockerfile does this),
else its canary aborts the protector-free router at startup.

### tun ports — literal vs. legacy

`ifconfig add tun` now takes an optional **`-srcport`**: when present, `-srcport`/`-dstport`
are literal UDP ports (the portable fabric); when absent, the legacy
`BASEPORTNUM + iface + name*100` formula applies (backward compatible). `-srcport` must
appear after `-addr`/`-hwaddr`. `run_grouter.py` emits it from each iface's `bind_port`.

## Build & validate on your machine (Docker)

```bash
cd backend
docker build -f grouter-zig/Dockerfile -t gini-grouter .       # context MUST be backend/  (note: ".")

# prove forwarding inside the image (loopback, self-contained):
docker run --rm -e GROUTER_BIN=/usr/local/bin/grouter gini-grouter \
  python3 /build/grouter-zig/tests/forward_test.py
docker run --rm -e GROUTER_BIN=/usr/local/bin/grouter gini-grouter \
  python3 /build/grouter-zig/tests/multihop_test.py

# run one router wired into a live fabric:
docker run --rm -e ROUTER_CONFIG='{"name":"r1","ifaces":[
  {"ip":"10.0.1.1/24","mac":"02:00:00:01:01:01","port":{"peer_host":"<peer>","peer_port":5000,"bind_port":5001}},
  {"ip":"10.0.2.1/24","mac":"02:00:00:02:01:01","port":{"peer_host":"<peer>","peer_port":5003,"bind_port":5004}}]}' \
  gini-grouter
```

Direct build once deps exist (`libreadline-dev libncurses-dev` + libslack from source):

```bash
ZIG="python3 -m ziglang" PREFIX=/usr/local backend/grouter-zig/build.sh   # -> grouter-zig/grouter
```

## Dependencies

GINI headers `backend/include` (in-tree) · **libslack** (built from source, hardening-off) ·
**readline** + **termcap/ncurses** · pthread/util/m (libc). lwIP and OpenFlow are in-tree C.

## Next

Switch the orchestrator (`frontend-ng/.../services/orchestrator.py`) to launch the
`gini-grouter` image for router nodes instead of the Python stub — same `gpipe`/`ifconfig
tun` vocabulary, so no GUI change. Then **Z3** (port owned modules to Zig) / **Z4**
(packetcore), each regression-tested by the harness above.
