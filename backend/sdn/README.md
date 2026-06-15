# GINI SDN

The software-defined-networking half of GINI. A topology drawn as
`Controller → OVS → hosts` runs as:

- **Controller** — a [POX](https://github.com/noxrepo/pox) container (Python 3,
  `gar` branch, v0.7.0) running an OpenFlow 1.0 controller app
  (`forwarding.l2_learning` by default). Listens on TCP 6633.
- **OVS** — the GINI **gRouter** launched in OpenFlow-switch mode
  (`--openflow`). It connects out to the controller, sends `PACKET_IN` on a
  flow-table miss, and forwards by the flows the controller installs. The
  gRouter speaks OpenFlow 1.0, so it is wire-compatible with POX.
- **Hosts** — ordinary machine containers attached to the OVS data ports.

No Python 2 anywhere: POX `gar` is the first Python-3 line of POX, and it still
speaks OpenFlow 1.0 (POX never moved off 1.0), so the pairing with the gRouter's
1.0 switch is unchanged.

## `pox/` — vendored POX (gar, v0.7.0)

Vendored from `noxrepo/pox` @ `gar-experimental` for offline, reproducible image
builds. Stock controller apps live in `pox/pox/forwarding/` (`l2_learning`,
`l2_pairs`, `l3_learning`). The legacy GINI teaching modules (`of_tutorial`,
`firewall`, `service_function_chaining`) were written against the old Python-2
POX API and are **not** ported yet — see `legacy/backend-src/pox/ext/gini/`.

> Cleanup note: the vendored tree still carries the clone's `.git/` and `tests/`
> directories (the build sandbox cannot delete files). Remove them once with
> `rm -rf backend/sdn/pox/.git backend/sdn/pox/tests`.

## Building & running the SDN lab

Build the two images once:

```sh
cd backend && docker build -f grouter-zig/Dockerfile -t gini-grouter .   # OVS datapath
cd backend/sdn && docker build -t gini-pox .                             # POX controller
```

Then draw `Controller → OVS → hosts` in gBuilder and press Run, or let the app
build them for you (`GINI_AUTOBUILD_GROUTER=1`, `GINI_AUTOBUILD_POX=1`). The OVS
container is launched with `--openflow` and `GINI_OF_CONTROLLER=<ofc>:6633`.

### gRouter ↔ POX wiring (the env the orchestrator sets)

- `GINI_OF_CONTROLLER` — `host[:port]` of the controller. The gRouter used to
  hardwire `127.0.0.1`; it now resolves this (a Docker service name works).
- `GINI_OF_CONNECT_DELAY` — seconds to wait before the first connect (default 30;
  the orchestrator sets 2).

### Handshake probe

`of_hello_probe.py` is a 60-line OpenFlow-1.0 switch that performs the exact
HELLO → FEATURES_REPLY → echo/barrier exchange the gRouter does, so you can
confirm a controller binds without building the C router:

```sh
cd backend/sdn/pox && python3 pox.py openflow.of_01 --port=6633 forwarding.l2_learning &
python3 backend/sdn/of_hello_probe.py 127.0.0.1 6633   # -> "completed the OpenFlow 1.0 handshake"
```

Verified against POX `gar`: it logs `[… ] connected` and exit code is 0.
