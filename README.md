# GINI — gBuilder 6.0

**A visual lab for computer networks, cloud computing, *and* operating systems — draw a system, press Run, and it comes to life as real containers (and a real xv6 kernel) you can inspect, drive, and observe.**

💬 **Join the community:** [GINI Discord](https://discord.gg/s5zTAgdKQd) — questions, help, and discussion.

GINI lets students and instructors build a topology on a canvas, then launches it as
honest, running infrastructure on Docker: a real C router that actually forwards packets,
a real OpenFlow controller programming a real switch, and real cloud services (databases,
object stores, message queues, dashboards) discoverable by name — plus a real **xv6** operating-system
kernel you can watch and extend in a visual **Machine Lab**. A built-in AI tutor —
**GINI** — explains what's on the canvas, animates how packets flow, and can scaffold
whole working systems from a one-line description.

It's designed to anchor three courses:

- **Computer Networks** — switches, routers, subnets, firewalls, and real OpenFlow SDN.
- **Cloud Computing** — VPC-style networking, managed services, autoscaling, observability.
- **Operating Systems** — a real **xv6** (RISC-V) kernel in a visual **Machine Lab**: watch the
  scheduler, system calls, traps, virtual memory, and the file system live, then extend the kernel
  yourself with **shadows** — a novel way to drop your own code into a *running* xv6 machine to
  experiment (fix the scheduler, add a syscall) without forking the kernel.

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
- **A real OS to hack** — a genuine **xv6** kernel in a visual **Machine Lab** (scheduler, system
  calls, traps, virtual memory, file system — all live over the serial, no gdb), plus **shadows**:
  drop your own code into a running xv6 machine for experiments and graded assignments, no kernel
  fork required.
- **GINI AI** — an in-app tutor with **Explain**, **Tutor**, and **Wizard** modes. Ask it
  to explain a device, trace a path, or describe a system you want and it lays out a
  working blueprint. Runs against a local LLM (Ollama) or fully offline.

---

## Requirements

- **Python 3.10+** (3.12 recommended). The Qt 6 GUI — **PySide6** plus **QtWebEngine** (for the
  embedded Desktop / OS-Zoo screens) — installs **automatically** as a dependency; you never install
  Qt separately, whichever install route you pick.
- A **container runtime** — Docker, or **Colima**/**Podman** (gBuilder detects it at launch and can help
  install it) — needed to *Run* topologies. You can explore fully in **Demo mode** without one.
- Works on **macOS, Linux, and Windows**. *Optional:* a local **[Ollama](https://ollama.com)** model
  for richer GINI AI answers.

---

## Install

**Two ways, both fully supported** — pick one (don't mix them; see Troubleshooting). Either way the
Qt GUI (PySide6 + QtWebEngine) is pulled in automatically, and **there is no setup command to run**:
gBuilder checks what the machine needs the first time it launches and offers to do it.

### 1. Pre-compiled package — the simplest, most stable

The hands-off route, and the right one for students and classroom setups.

```bash
pipx install gini-toolkit     # the app, isolated. `pip install gini-toolkit` also works.
gbuilder                      # launch — it offers the container setup on first run
```

> No `pipx`? Install it once (`brew install pipx` on macOS, or `pip install pipx`), or use
> `pip install gini-toolkit` inside a virtual environment.

### 2. From source — the latest code

For the newest features, building the xv6 kernel image yourself, or contributing.

```bash
git clone https://github.com/citelab/gini && cd gini
python3 -m venv .venv && source .venv/bin/activate
./scripts/dev.sh install      # editable installs of all three packages
gbuilder                      # launch
```

**Use the script rather than `pip install -e .`.** The `gini` package is a *namespace* split across
two distributions — `gini-core` (the domain model and proof format) and `gini-toolkit` (the app) —
so installing one alone leaves half the tree unimportable, and the error names a missing module
rather than a missing install. The script also installs them in the right order: `gini-core` first,
or pip fetches the published one from PyPI over the top of your checkout. `./scripts/dev.sh check`
prints what is installed and from where.

Either way `gbuilder` opens immediately — build, save, and explore topologies, with the AI tutor and
everything in **Demo mode**, before any container exists. Live **Run** lights up once the runtime and
images are in place. After an upgrade, gBuilder notices the version moved and offers to refresh the
images; nothing to remember.

<details>
<summary><b>The Teaching Center (instructors)</b></summary>

A separate package, deliberately: it holds no Qt, so a headless VM installs 2.3MB instead of 400MB
of PySide6.

```bash
pip install gini-teaching-center
gini-tc --data ./data --port 8080
```

`teaching-center/README.md` has the full server setup — pm2/systemd, TLS, upgrades.
</details>

<details>
<summary><b>macOS details</b></summary>

gBuilder's first-run setup uses **Colima** — a free, lightweight Docker runtime, no Docker Desktop
license needed. On a clean Mac with [Homebrew](https://brew.sh) it offers to run:

```bash
brew install colima docker
colima start --cpu 2 --memory 4 --disk 30
```

If Docker Desktop (or Colima) is already running, it detects that and just pulls the images.
</details>

<details>
<summary><b>Linux details</b></summary>

Install **Docker Engine** first — it needs `sudo`, so gBuilder guides rather than auto-installs:

```bash
# https://docs.docker.com/engine/install/ for your distro, then:
sudo usermod -aG docker $USER      # log out / back in afterwards
```

Podman works too. Then launch gBuilder and accept the setup it offers.
</details>

<details>
<summary><b>Windows details</b></summary>

Colima isn't available on Windows — use **Docker Desktop** or **Podman Desktop**:

```powershell
winget install -e --id Docker.DockerDesktop
```

Start it, then launch gBuilder. (Live-Run networking on Windows is still being validated;
Demo mode works fully.)
</details>

<details>
<summary><b>Dev tools & building images locally</b></summary>

```bash
./scripts/dev.sh install                      # all three packages, editable
cd frontend-ng && pip install -e ".[dev]"     # + tests and linters
./scripts/dev.sh test                         # the suite

# build the container images yourself instead of pulling them
# (needed to hack the xv6 kernel via shadows):
./scripts/images.sh build 6.1.0
```

Point the app at a different image registry with `GINI_REGISTRY=ghcr.io/<owner>`.
See [`scripts/README.md`](scripts/README.md) for releases, version bumps and multi-arch images.
</details>

<details>
<summary><b>Troubleshooting</b></summary>

- **"runtime not set up yet"** — accept the setup gBuilder offers at launch, or open it from the
  Help menu. Demo mode works without it.
- **The image pull says `denied` / `not found`** — images unreachable: check your network, or that
  the registry (`ghcr.io/gini-toolkit`) is correct and its packages are public.
- **`ModuleNotFoundError: gini.domain`** — `gini-core` is missing. From a checkout, run
  `./scripts/dev.sh install`; otherwise `pip install gini-core`.
- **Two `gbuilder`s on your PATH** — you installed with both pip *and* pipx; keep one
  (`pip uninstall gini-toolkit` or `pipx uninstall gini-toolkit`).
</details>

---

### Your first topology

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
core/               gini-core — the domain model + proof format, no Qt (PyPI: gini-core)
frontend-ng/        gBuilder 6.0 — PySide6 app (ui · agent · runtime · services) (PyPI: gini-toolkit)
teaching-center/    the course server, no Qt (PyPI: gini-teaching-center)
scripts/            install, test, release, container images — see scripts/README.md
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
./scripts/dev.sh test                    # the suite, minus the Qt tests
./scripts/dev.sh test tests/test_tc_tls.py       # one file

# the Qt suite needs a display, and is slow enough to be excluded by default:
cd frontend-ng && QT_QPA_PLATFORM=offscreen pytest tests/test_qt_suite.py
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
