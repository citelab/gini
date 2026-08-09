# GINI 6 — repository layout

GINI is being modernized **in place** (strangler-fig): the new toolkit grows alongside
the original, and the C gRouter is *adopted and evolved*, not rewritten. This file says
what is part of the new version (v6) and what is archived legacy, so the tree is easy to
navigate during the transition.

## Active — the GINI 6 toolkit

```
frontend-ng/                 The new app (PySide6 / Qt 6 / Python 3.12)
  src/gini/
    domain/                  pure-Python model (devices, topology) — no Qt
    ui/                      canvas, palette, inspector, themes, Router Lab
    agent/                   GiniAPI + tool registry + Ollama loop + MCP server
    runtime/                 portable user-space data plane (switch, host, shuttle)
    services/                compiler (topology→wiring), orchestrator (Docker), persistence
  tests/                     pytest suite (35 passing)

backend/                     The real gRouter (adopted C, modernized)
  src/grouter/               ~20k-line C router + the Z1/Z2 module seams
  include/                   gRouter headers
  build.zig                  build the router with Zig (`zig build`)
  grouter-build/
    build.sh                 zig cc build (used by Docker)
    Dockerfile               `gini-grouter` image (libslack + readline + the router)
    run_grouter.py           entrypoint: ROUTER_CONFIG → ifconfig/route → grouter
    tests/                   end-to-end forwarding proofs (forward_test, multihop_test)
  third-party/               vendored helpers used by the router/tests (mut)
```

## Legacy — kept for reference, not part of v6

```
legacy/
  frontend/                  the original gBuilder (Python 2.7 / PyQt4) — superseded by frontend-ng
  runtime-spike/             the R0 portability spike — superseded by frontend-ng/.../runtime
  SConstruct, site_scons/    the old SCons build — the router now builds with Zig
  scripts/                   old launcher/setup scripts
  backend-src/
    gcloud/ gloader/         legacy cloud scripts + the old XML topology loader
    gvirtual_switch/ pox/     old UML switch + the POX SDN controller
```

Nothing under `legacy/` is referenced by the active build or tests (verified). It stays
as a harvest source until the modernization (Track B/Z) has taken what it needs, then it
can be deleted — it lives in git history regardless.

## Run it

```bash
# the app
cd frontend-ng && pip install -e . && python -m gini      # or: gbuilder

# build the real router image once (so "Run" can launch real routers)
cd backend && docker build -f grouter-build/Dockerfile -t gini-grouter .

# prove the router forwards (host build or in the image)
GROUTER_BIN=/path/to/grouter python3 backend/grouter-build/tests/forward_test.py
```

## Graduating to a clean `gini6/`

A clean fork is deliberately deferred. The gRouter is still being ported to Zig (Z3/Z4)
in place, so copying it now would freeze a snapshot that immediately diverges. Once the
Zig port is mature and `legacy/` is fully harvested, the repo can simply be renamed/cut
to `gini6` with only the active tree.
