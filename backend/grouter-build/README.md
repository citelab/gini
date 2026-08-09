# The real gRouter, built and run in the fabric

Builds the **existing** C gRouter (`backend/src/grouter/`, ~20k lines) with a plain C compiler and
runs it in the portable fabric over its native `tun` (UDP) interface — the familiar CLI and full
router.

> **Zig has been removed from GINI.** The router was briefly built with `zig cc` and a few leaf
> modules were ported to Zig; that's been reverted. The router is now one systems language — **C** —
> with **Lua** for student-written modules and **Python** for the app. Rationale: xv6 anchors C as
> GINI's systems-teaching language, Lua is the student extension tier, and `zig cc`'s cross-compile
> value was never used here (the router always builds inside a Linux Docker image). This directory
> keeps the name `grouter-build/` only because its path is referenced from the frontend's image layout;
> a rename is a separate, coordinated change.

## Files

```
backend/grouter-build/
  build.sh                        # plain C build (clang/gcc) — used by Docker
  Dockerfile                      # Linux image: libslack + readline + the C-built grouter
  run_grouter.py                  # entrypoint: ROUTER_CONFIG -> ifconfig/route -> grouter
  grconsole.py                    # console client (real CLI over the control socket)
  tests/forward_test.py           # 1 router, A<->B round-trip (TTL 64->63)
  tests/multihop_test.py          # 2 routers, A<->B over 2 hops (TTL 64->62)
  tests/multirouter_test.py       # compiler-generated config, cross-router routing
  tests/test_rctl.py              # control socket / real CLI over the socket
```

## Build & test

```
cd backend && docker build -f grouter-build/Dockerfile -t gini-grouter .
docker run --rm -e GROUTER_BIN=/usr/local/bin/grouter gini-grouter \
    python3 /build/grouter-build/tests/forward_test.py
```

The e2e tests (`forward`, `multihop`, `multirouter`, `test_rctl`) are the regression guardrail for
any change to the router build or the restored C modules.
