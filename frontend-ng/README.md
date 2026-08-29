# gini-toolkit — gBuilder

The GINI desktop app: a visual builder and live runtime for **computer networking** and **cloud
computing** experiments, with an AI layer that can build, inspect and explain topologies.

Drag routers, switches, machines, VPCs and Kubernetes elements onto a canvas, press **Run**, and
they come up as real containers you can open a shell into.

## Install

```bash
pipx install gini-toolkit     # `pip install gini-toolkit` works too
gbuilder
```

PySide6 and QtWebEngine come with it. **There is no setup command to run** — gBuilder checks what
the machine needs the first time it launches and offers to install the container runtime and pull
the images. Demo mode works before any of that exists, so the app is useful the moment it opens.

Requires Python 3.10+ and, for live Run, a container runtime (Docker, Colima or Podman).

## What you get

- **Canvas and palette** — networking elements (routers, switches, OVS, machines, taps) alongside
  cloud ones (VPCs, subnets, load balancers, Kubernetes pods, serverless functions).
- **Live Run** — each element is a real container. Double-click a machine for a shell, a router for
  the **Router Lab**, a service for its web dashboard.
- **SDN and NFV** — a real OpenFlow data plane with a POX controller, and a flow-table view of what
  the switch is actually doing.
- **xv6** — boot and modify a real RISC-V teaching kernel from inside a topology.
- **Ask GINI** — an assistant grounded in the topology on your canvas. Better with a local
  [Ollama](https://ollama.com) model; the deterministic modes work without one.
- **Proof of work** — record a lab session as a tamper-evident chain and hand in a receipt, for
  courses using the GINI Teaching Center.

## From source

```bash
git clone https://github.com/citelab/gini && cd gini
./scripts/dev.sh install       # editable installs of gini-core, gini-toolkit, gini-teaching-center
gbuilder
```

Use the script rather than `pip install -e ./frontend-ng` on its own: `gini` is a **namespace**
package split across `gini-core` (the domain model and proof format) and this distribution, and
installing one alone leaves half the tree unimportable — with an error that names a missing module
rather than a missing install.

```bash
QT_QPA_PLATFORM=offscreen python -m gini --selftest    # headless smoke test
./scripts/dev.sh test                                  # the suite
```

## Layout

```
src/gini/
  __main__.py     the `gbuilder` entrypoint
  ui/             canvas, palette, inspector, labs, theme system
  services/       persistence, runtime, Teaching Center client, outbox
  runtime/        container orchestration
  agent/          programmatic API + MCP server, so external agents can drive GINI
  setup/          first-run bootstrap (runtime detection, image pulls)
```

The domain model lives in the separate `gini-core` distribution, under `gini.domain`.

## License & links

Part of the GINI project — https://github.com/citelab/gini
