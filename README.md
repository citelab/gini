# GINI — gBuilder 6.0

**A visual lab for computer networks *and* cloud computing — draw a system, press Run, and it comes to life as real containers you can inspect, drive, and observe.**

GINI lets students and instructors build a topology on a canvas, then launches it as
honest, running infrastructure on Docker: a real C router that actually forwards packets,
a real OpenFlow controller programming a real switch, and real cloud services (databases,
object stores, message queues, dashboards) discoverable by name. A built-in AI tutor —
**GINI** — explains what's on the canvas, animates how packets flow, and can scaffold
whole working systems from a one-line description.

It's designed to anchor three courses:

- **Computer Networks** — switches, routers, subnets, firewalls, and real OpenFlow SDN.
- **Cloud Computing** — VPC-style networking, managed services, autoscaling, observability.
- **Operating Systems** — containers as live namespaces/cgroups, plus a hackable ~20k-line
  C network stack you can read and extend.

> gBuilder 6.0 is the modern rewrite of the classic GINI Toolkit. The original
> Python 2.7 / PyQt4 / SCons app lives under `legacy/` for reference.

---

## Highlights

- **Visual builder** — a fast PySide6/Qt 6 canvas with a searchable palette of ~40
  networking and cloud elements, theming, save/load, and an inspector.
- **It actually runs** — Run compiles the canvas to a Docker Compose project and brings it
  up. Machines, routers, switches, controllers, and cloud services all start as containers.
- **The real C gRouter** — the genuine GINI router (built with `zig cc`), forwarding
  packets over a portable user-space fabric. No kernel modules, no privileges.
- **Real SDN** — drop an *OpenFlow Controller* + *OpenVSwitch*; GINI runs **POX** (Python 3)
  programming the gRouter in OpenFlow-1.0 switch mode. Watch flows install on the first
  packet, then forward at wire speed.
- **Cloud services as containers** — MinIO, PostgreSQL, Redis, MongoDB, RabbitMQ, Kafka
  (Redpanda), NATS, nginx, Traefik, Prometheus, Grafana, Jaeger, Fortio, and more — each a
  real, off-the-shelf image reachable by service name.
- **Live observability** — drop *Metrics* + *Dashboards* and GINI auto-wires
  cAdvisor → Prometheus → Grafana with a prebuilt dashboard. Generate load and watch the
  graphs move.
- **GINI AI** — an in-app tutor with **Explain**, **Tutor**, and **Wizard** modes. Ask it
  to explain a device, trace a path, or describe a system you want and it lays out a
  working blueprint. Runs against a local LLM (Ollama) or fully offline.

---

## Requirements

- **macOS or Linux**
- **Python 3.11+** (3.12 recommended) for the gBuilder app
- **Docker** (Docker Desktop on macOS) — used to run topologies
- *Optional:* a local **[Ollama](https://ollama.com)** model for richer GINI AI answers

---

## Quick start

### 1. Run the app

```bash
cd frontend-ng
python -m pip install -e .
python -m gini            # or: gbuilder
```

This opens gBuilder. You can build, save, and explore topologies right away — the AI tutor
works offline, and "Run" needs Docker (next step).

### 2. Build the two local images (once)

Cloud-service images are pulled from Docker Hub automatically on first Run. The **router**
and **SDN controller** images are built locally:

```bash
# the real C gRouter (used by Router and OpenVSwitch elements)
cd backend && docker build -f grouter-build/Dockerfile -t gini-grouter .

# the POX OpenFlow controller (used by the OpenFlow Controller element)
cd backend/sdn && docker build -t gini-pox .
```

Prefer the app to build them for you? Run with `GINI_AUTOBUILD_GROUTER=1` and
`GINI_AUTOBUILD_POX=1`.

### 3. Draw, Run, explore

- **Place** a device by dragging it from the palette onto the canvas.
- **Connect** two devices: click the **Connect** tool in the toolbar (the link icon), then
  click the first device and then the second — a link appears. Click the tool again (or
  press Esc) to leave Connect mode. You can also ask GINI: "connect R1 and S1".
- **Run** the topology with the ▶ button.

Once it's running:

- **Double-click** a machine to open a shell; a service with a web UI (Grafana, MinIO …)
  to open its dashboard; a router to open the **Router Lab**.
- **Right-click** any node for **Open console**, **Log in**, **View logs**, or **Delete**.
- The console log prints each running service's web URL.

---

## GINI AI

The right-hand **Ask GINI** panel is a teaching assistant that always sees the live canvas.
Modes are toggle buttons; the toolbar shows the current **mode** and whether GINI is
**thinking**.

- **Explain** — click any device and GINI explains it on the canvas (spotlight, callouts,
  animated packet flows). It also explains palette elements ("when do I use a switch vs a
  hub?").
- **Tutor** — overlays highlights and animations as it teaches.
- **Wizard** — describe what you want ("something I can watch under load", "a web app with
  a database") and GINI matches a curated, guaranteed-to-work **recipe** and lays it out
  with one click. The model only *selects and explains*; the building is deterministic, so
  even a small local model can't produce a broken topology.

Connect a model by pointing GINI at Ollama:

```bash
export GINI_LLM_URL=http://localhost:11434
export GINI_LLM_MODEL=llama3.1        # or gemma, qwen, …
python -m gini
```

Without a model, GINI still builds, inspects, traces paths, and ranks recipes
deterministically.

---

## Software-Defined Networking

The SDN stack is the original GINI design, made real:

- **OpenVSwitch** element → the gRouter launched in `--openflow` mode (a real OpenFlow 1.0
  switch).
- **OpenFlow Controller** element → a **POX** (`gar`, Python 3) container running an app
  you choose from the inspector (`l2_learning`, `hub`, or the classic `of_tutorial`).

Draw `Controller → OVS → hosts`, Run, and ping between hosts: the first packet misses the
flow table → goes up to POX → a flow is installed → the rest forward in the datapath. You
can watch flows appear with `openflow entry all` in the OVS console, and the controller's
decisions in its logs.

---

## Cloud service catalog

Each of these palette elements runs as a real container, reachable by name on the lab's
network (cloud-style service discovery):

| Element | Backed by | Console |
|---|---|---|
| Object Storage | MinIO | ✓ |
| Managed Database | PostgreSQL | — |
| NoSQL Database | MongoDB | — |
| Cache | Redis | — |
| Message Queue | RabbitMQ | ✓ |
| Event Stream | Redpanda (Kafka API) | — |
| Pub/Sub | NATS | ✓ |
| Reverse Proxy | Traefik | ✓ |
| Load Balancer | nginx | — |
| Web App | nginxdemos/hello | ✓ |
| Container Registry | registry:2 | — |
| Metrics | Prometheus | ✓ |
| Dashboards | Grafana | ✓ |
| Tracing | Jaeger | ✓ |
| Load Generator | Fortio | ✓ |

Compute elements (**Instance**, **Container**) run as plain containers on the same network,
so a program inside them reaches services by name (`psql -h database1`,
`http://objectstore1:9000`).

---

## Repository layout

```
frontend-ng/        gBuilder 6.0 — PySide6 app (domain · ui · agent · runtime · services)
backend/
  src/grouter/      the real C gRouter (~20k lines) incl. OpenFlow/SDN mode
  grouter-build/      C build + Dockerfile (gini-grouter) + e2e forwarding tests
  sdn/              POX (gar) controller + Dockerfile (gini-pox)
legacy/             the original Python 2.7 / PyQt4 GINI, kept for reference
ARCHITECTURE.md     what's active vs legacy, and how it fits together
```

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the full map.

---

## Testing

```bash
cd frontend-ng
pytest                                   # ~95 tests
# headless / CI:
QT_QPA_PLATFORM=offscreen pytest
```

The gRouter has end-to-end forwarding proofs under `backend/grouter-build/tests/`
(`forward_test.py`, `multihop_test.py`, …), runnable against a built `grouter` binary.

---

## Status

gBuilder 6.0 is under active development. Working today: the visual builder, real packet
forwarding through the C gRouter (single- and multi-router), OpenFlow SDN (POX + gRouter),
the cloud service catalog, observability auto-wiring, and the GINI AI tutor with Explain /
Tutor / Wizard modes. On the roadmap: configuring services from the inspector, VPC-level
isolation, a managed Kubernetes element, and more Wizard recipes.

---

## License & contact

GINI is free software — see `COPYING` for copyright information. Questions, bugs, or ideas:
open an issue on this repository, or email `maheswar@cs.mcgill.ca`.
