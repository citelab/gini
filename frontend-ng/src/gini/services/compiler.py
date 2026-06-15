"""RuntimeCompiler — lower a canvas Topology onto the portable user-space runtime.

Generalizes the hand-written R0 wiring to any topology:
  * classify devices (machine / switch / router / grouping),
  * find L2 broadcast domains (segments) and give each a subnet,
  * assign IPs, gateways, MACs, and per-endpoint UDP ports,
  * emit machine/switch/router specs that gini.runtime can run (in-process or Docker).

Cloud "grouping" devices (VPC, subnet, region, cluster, pod, instance-group) are
organizational in R0 and are skipped as runtime nodes (links touching them are
dropped, with a note). Cloud endpoints (instances, containers, LBs, …) run as machines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..domain.topology import Topology
from .cloud_catalog import is_service, service_for

ROUTERS = {"router", "firewall"}
SWITCHES = {"switch", "hub"}        # plain L2 — live in the shared `fabric` container
GROUPS = {"vpc", "subnet", "cloud_subnet", "region", "k8s_cluster", "instance_group", "pod"}

# SDN: an OVS is an OpenFlow switch that runs as its OWN container (the gRouter in
# --openflow mode), programmed by a controller over a management channel. A controller
# is the control plane — it is NOT a data host and gets no data-plane IP/gateway.
DEFAULT_OF_PORT = 6633
DEFAULT_OF_APP = "forwarding.l2_learning"


def _role(type_key: str) -> str:
    if type_key in ROUTERS:
        return "router"
    if type_key in SWITCHES:
        return "switch"
    if type_key == "ovs":
        return "ovs"
    if type_key == "controller":
        return "controller"
    if is_service(type_key):           # cloud managed service (own container, bridge net)
        return "service"
    if type_key in ("instance", "container"):   # cloud compute — own bridge container
        return "compute"
    if type_key in GROUPS:
        return "group"
    return "machine"                   # host = a node on the simulated tun fabric


def _svc(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower()) or "node"


def _norm_image(raw: str) -> str:
    """Turn a friendly image property into a real Docker tag.
    'ubuntu-22.04' -> 'ubuntu:22.04'; an explicit tag/registry is kept as-is."""
    raw = (raw or "").strip()
    if not raw:
        return "ubuntu:22.04"
    if ":" in raw or "/" in raw:
        return raw
    if "-" in raw:                     # ubuntu-22.04 -> ubuntu:22.04
        n, _, v = raw.partition("-")
        return f"{n}:{v}"
    return raw + ":latest"


class _UF:
    def __init__(self) -> None:
        self.p: dict = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


@dataclass
class Endpoint:
    device: str
    location: str          # service name (machine) or "fabric"
    bind_port: int = 0
    peer: "Endpoint | None" = None

    def peer_host(self, docker: bool) -> str:
        if not docker:
            return "127.0.0.1"
        if self.location == "fabric" and self.peer.location == "fabric":
            return "127.0.0.1"
        return self.peer.location

    def wiring(self, docker: bool) -> dict:
        return {"bind_host": "0.0.0.0" if docker else "127.0.0.1",
                "bind_port": self.bind_port,
                "peer_host": self.peer_host(docker),
                "peer_port": self.peer.bind_port}


@dataclass
class MachineSpec:
    name: str
    ifaces: list["IfaceSpec"]      # one per segment the machine is on (multi-homing)
    gw: str | None                 # default gateway (first segment that has a router)


@dataclass
class IfaceSpec:
    ip: str
    mac: str
    ep: Endpoint


@dataclass
class SwitchSpec:
    name: str
    eps: list[Endpoint]


@dataclass
class RouterSpec:
    name: str
    ifaces: list[IfaceSpec]
    routes: list = field(default_factory=list)   # static inter-router routes:
    #                                              {net, mask, gw, dev} (dev = tun index)


@dataclass
class OvsSpec:
    """An OpenFlow switch: the gRouter in --openflow mode, in its own container,
    programmed by `controller` (a service name) over OpenFlow on `controller_port`."""
    name: str
    eps: list[Endpoint]
    controller: str | None = None          # controller service name (host), or None
    controller_port: int = DEFAULT_OF_PORT


@dataclass
class ControllerSpec:
    """An SDN controller: a POX container running `app` on `port`, programming the
    OVS switches in `switches` (their service names)."""
    name: str
    app: str = DEFAULT_OF_APP
    port: int = DEFAULT_OF_PORT
    switches: list[str] = field(default_factory=list)


@dataclass
class ServiceSpec:
    """A managed cloud service backed by an off-the-shelf container image (MinIO,
    Postgres, …). Runs on the shared bridge network, reachable by its service name.
    `ports` is a list of {container, host, label, web} — host is a unique published
    port so multiple consoles don't collide. `volumes` are compose volume strings;
    `files` are generated config files (relative path -> content) written into the
    project and bind-mounted — this is how the observability stack is auto-wired."""
    name: str
    type_key: str
    image: str
    summary: str
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    ports: list[dict] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    privileged: bool = False
    files: dict[str, str] = field(default_factory=dict)


@dataclass
class RuntimeConfig:
    machines: list[MachineSpec] = field(default_factory=list)
    switches: list[SwitchSpec] = field(default_factory=list)
    routers: list[RouterSpec] = field(default_factory=list)
    ovs_switches: list[OvsSpec] = field(default_factory=list)
    controllers: list[ControllerSpec] = field(default_factory=list)
    services: list[ServiceSpec] = field(default_factory=list)
    subnets: dict[int, str] = field(default_factory=dict)   # seg -> cidr
    notes: list[str] = field(default_factory=list)

    # -- emit for the in-process simulator / Docker ------------------------- #
    def to_runtime(self, docker: bool) -> dict:
        return {
            "machines": [
                {"name": _svc(m.name), "gw": m.gw,
                 "ifaces": [{"ip": i.ip, "mac": i.mac, "tap": f"gini{idx}",
                             "port": i.ep.wiring(docker)}
                            for idx, i in enumerate(m.ifaces)]}
                for m in self.machines
            ],
            "switches": [
                {"name": _svc(s.name), "ports": [e.wiring(docker) for e in s.eps]}
                for s in self.switches
            ],
            "routers": [
                {"name": _svc(r.name),
                 "ifaces": [{"ip": i.ip, "mac": i.mac, "port": i.ep.wiring(docker)}
                            for i in r.ifaces],
                 "routes": r.routes}
                for r in self.routers
            ],
            # SDN: OVS switches run as their own gRouter --openflow containers; each
            # data port is a cross-container UDP link, just like a router interface.
            # Ports carry a link-local placeholder IP only so the gRouter can bring the
            # tun up — in OpenFlow mode frames are switched by the flow table (shunted
            # before the L3 stack), so the address is never used for forwarding.
            "ovs": [
                {"name": _svc(s.name), "openflow": True,
                 "controller": s.controller, "controller_port": s.controller_port,
                 "ports": [
                     {"ip": f"169.254.{si}.{pi + 1}/16",
                      "mac": f"02:00:fe:{si:02x}:00:{pi + 1:02x}",
                      "port": e.wiring(docker)}
                     for pi, e in enumerate(s.eps)]}
                for si, s in enumerate(self.ovs_switches)
            ],
            "controllers": [
                {"name": _svc(c.name), "app": c.app, "port": c.port,
                 "switches": c.switches}
                for c in self.controllers
            ],
            # Managed cloud services — ordinary containers from public images on the
            # shared bridge network, reachable by service name (cloud-style discovery).
            "services": [
                {"name": _svc(s.name), "type": s.type_key, "image": s.image,
                 "summary": s.summary, "command": s.command, "env": s.env,
                 "ports": s.ports, "volumes": s.volumes, "privileged": s.privileged,
                 "files": s.files}
                for s in self.services
            ],
        }


# --- observability auto-wiring config (generated into the project, bind-mounted) --- #
_PROMETHEUS_YML = (
    "global:\n"
    "  scrape_interval: 5s\n"
    "scrape_configs:\n"
    "  - job_name: prometheus\n"
    "    static_configs:\n"
    "      - targets: ['localhost:9090']\n"
    "  - job_name: cadvisor\n"
    "    static_configs:\n"
    "      - targets: ['cadvisor:8080']\n"
)
_GRAFANA_DS = (
    "apiVersion: 1\n"
    "datasources:\n"
    "  - name: Prometheus\n"
    "    type: prometheus\n"
    "    uid: prometheus\n"            # fixed uid so the dashboard panels bind reliably
    "    access: proxy\n"
    "    url: http://{prom}:9090\n"
    "    isDefault: true\n"
)
_GRAFANA_PROVIDER = (
    "apiVersion: 1\n"
    "providers:\n"
    "  - name: gini\n"
    "    type: file\n"
    "    options:\n"
    "      path: /var/lib/grafana/dashboards\n"
)


def _grafana_dashboard_json() -> str:
    """A starter Grafana dashboard over cAdvisor metrics — CPU, memory, network per
    container. Built via json.dumps so the PromQL (with quotes) is always valid JSON."""
    import json

    ds = {"type": "prometheus", "uid": "prometheus"}      # bind panels to the datasource

    def panel(pid, title, expr, x, y, w=12, h=9):
        return {"id": pid, "type": "timeseries", "title": title, "datasource": ds,
                "gridPos": {"h": h, "w": w, "x": x, "y": y},
                "fieldConfig": {"defaults": {}, "overrides": []},
                "targets": [{"expr": expr, "legendFormat": "{{name}}", "refId": "A",
                             "datasource": ds}]}

    return json.dumps({
        "title": "Container resources", "uid": "gini-containers",
        "schemaVersion": 39, "version": 1, "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"},
        "panels": [
            panel(1, "CPU (cores) by container",
                  'rate(container_cpu_usage_seconds_total{name!=""}[1m])', 0, 0),
            panel(2, "Memory (bytes) by container",
                  'container_memory_usage_bytes{name!=""}', 12, 0),
            panel(3, "Network RX (bytes/s) by container",
                  'rate(container_network_receive_bytes_total{name!=""}[1m])', 0, 9, w=24),
        ],
    }, indent=2)


class RuntimeCompiler:
    def compile(self, topo: Topology) -> RuntimeConfig:
        cfg = RuntimeConfig()
        role = {d.id: _role(d.type_key) for d in topo.devices.values()}
        name = {d.id: d.name for d in topo.devices.values()}

        # 1. keep links not touching grouping devices; pull out SDN control links
        #    (controller↔OVS) — they are a management association, not a data segment.
        kept = []
        control_links = []
        ovs_controller: dict[str, str] = {}        # ovs did -> controller did
        ctrl_switches: dict[str, list[str]] = {}   # controller did -> [ovs did]
        for l in topo.links.values():
            rs, rt = role.get(l.source_id), role.get(l.target_id)
            if rs == "group" or rt == "group":
                cfg.notes.append(f"skipped link touching grouping: "
                                 f"{name.get(l.source_id)}–{name.get(l.target_id)}")
            elif rs in ("service", "compute") or rt in ("service", "compute"):
                # cloud services + compute live on the bridge net and are reached by
                # name, so a link to one is intent ("uses"), not a data-plane segment.
                cfg.notes.append(f"service link (reach by name): "
                                 f"{name.get(l.source_id)}–{name.get(l.target_id)}")
            elif rs == "controller" or rt == "controller":
                control_links.append(l)
                ctrl = l.source_id if rs == "controller" else l.target_id
                other = l.target_id if rs == "controller" else l.source_id
                if role.get(other) == "ovs":       # only OVS↔controller is a real assoc
                    ovs_controller[other] = ctrl
                    ctrl_switches.setdefault(ctrl, []).append(other)
                else:
                    cfg.notes.append(f"controller {name.get(ctrl)} should attach to an "
                                     f"OVS, not {name.get(other)}")
            else:
                kept.append(l)

        # 2. (machines may be multi-homed: a machine keeps ALL its links and gets one
        #     interface/IP per segment — see the machine build + shuttle.)

        # 3. segments: union links that share an L2 (switch) device
        uf = _UF()
        for l in kept:
            uf.find(l.id)
        by_device: dict[str, list] = {}
        for l in kept:
            by_device.setdefault(l.source_id, []).append(l)
            by_device.setdefault(l.target_id, []).append(l)
        for did, links in by_device.items():
            if role[did] in ("switch", "ovs"):     # OVS is an L2 domain too
                for l in links[1:]:
                    uf.union(links[0].id, l.id)
        seg_of_link = {l.id: uf.find(l.id) for l in kept}
        seg_ids = {}
        for root in dict.fromkeys(seg_of_link.values()):
            seg_ids[root] = len(seg_ids)
        for root, i in seg_ids.items():
            cfg.subnets[i] = f"10.0.{i + 1}.0/24"

        # 4. endpoints per kept link
        eps: dict[tuple, Endpoint] = {}

        def endpoint(device_id: str) -> Endpoint:
            # Switches live inside the shared `fabric` container; routers each run as
            # their own `gini-grouter` container (the real C gRouter), so a router's
            # location is its own service name. Machines are their own containers too.
            # This makes every router link a symmetric cross-container UDP link.
            loc = "fabric" if role[device_id] == "switch" else _svc(name[device_id])
            return Endpoint(device=name[device_id], location=loc)

        port = 5000
        link_eps: dict[str, tuple[Endpoint, Endpoint]] = {}
        for l in kept:
            a, b = endpoint(l.source_id), endpoint(l.target_id)
            a.bind_port = port; port += 1
            b.bind_port = port; port += 1
            a.peer, b.peer = b, a
            link_eps[l.id] = (a, b)
            eps[(l.id, l.source_id)] = a
            eps[(l.id, l.target_id)] = b

        # 5. IP assignment per segment
        seg_hosts: dict[int, int] = {}      # next machine host octet
        seg_rtr: dict[int, int] = {}        # next router host octet
        seg_gateway: dict[int, str] = {}
        # interfaces on each segment: (device_id, link)
        iface_ip: dict[tuple, str] = {}
        # routers first (so .1 is the gateway), then machines
        ordered = sorted(kept, key=lambda l: 0)
        for did_role in ("router", "machine"):
            for l in kept:
                seg = seg_ids[seg_of_link[l.id]]
                base = f"10.0.{seg + 1}."
                for end in (l.source_id, l.target_id):
                    if role[end] != did_role:
                        continue
                    key = (l.id, end)
                    if key in iface_ip:
                        continue
                    if did_role == "router":
                        n = seg_rtr.get(seg, 0) + 1
                        seg_rtr[seg] = n
                        ip = base + str(n)            # .1, .2 ...
                        seg_gateway.setdefault(seg, ip)
                    else:
                        n = seg_hosts.get(seg, 9) + 1
                        seg_hosts[seg] = n
                        ip = base + str(n)            # .10, .11 ...
                    iface_ip[key] = ip

        def mac(seg: int, kind: int, idx: int) -> str:
            return f"02:00:00:{seg + 1:02x}:{kind:02x}:{idx:02x}"

        # 6. build specs
        # machines
        midx = 0
        m_ifaces: dict[str, list] = {}     # machine did -> [IfaceSpec] (one per segment)
        m_gw: dict[str, str] = {}          # machine did -> default gateway
        m_order: list[str] = []            # preserve first-seen order
        for l in kept:
            seg = seg_ids[seg_of_link[l.id]]
            for end in (l.source_id, l.target_id):
                if role[end] != "machine":
                    continue
                key = (l.id, end)
                if key not in iface_ip:
                    continue
                midx += 1
                if end not in m_ifaces:
                    m_ifaces[end] = []
                    m_order.append(end)
                m_ifaces[end].append(IfaceSpec(ip=iface_ip[key] + "/24",
                                               mac=mac(seg, 2, midx), ep=eps[key]))
                gw = seg_gateway.get(seg)        # default route via the first router seen
                if gw and end not in m_gw:
                    m_gw[end] = gw
        for did in m_order:
            cfg.machines.append(MachineSpec(name=name[did], ifaces=m_ifaces[did],
                                            gw=m_gw.get(did)))

        # switches
        for did, r in role.items():
            if r != "switch":
                continue
            ports = [eps[(l.id, did)] for l in by_device.get(did, []) if l in kept]
            if ports:
                cfg.switches.append(SwitchSpec(name=name[did], eps=ports))

        # OVS switches — own gRouter --openflow container, programmed by a controller
        props = {d.id: getattr(d, "properties", {}) or {} for d in topo.devices.values()}
        for did, r in role.items():
            if r != "ovs":
                continue
            ports = [eps[(l.id, did)] for l in by_device.get(did, []) if l in kept]
            ctrl_did = ovs_controller.get(did)
            ctrl_name = _svc(name[ctrl_did]) if ctrl_did else None
            ctrl_port = DEFAULT_OF_PORT
            if ctrl_did:
                ctrl_port = int(props[ctrl_did].get("Port") or DEFAULT_OF_PORT)
            cfg.ovs_switches.append(OvsSpec(name=name[did], eps=ports,
                                            controller=ctrl_name,
                                            controller_port=ctrl_port))

        # controllers — POX containers; each programs the OVS switches linked to it
        for did, r in role.items():
            if r != "controller":
                continue
            p = props[did]
            cfg.controllers.append(ControllerSpec(
                name=name[did],
                app=p.get("App") or DEFAULT_OF_APP,
                port=int(p.get("Port") or DEFAULT_OF_PORT),
                switches=[_svc(name[o]) for o in ctrl_switches.get(did, [])]))

        # managed cloud services — each backed by an off-the-shelf image. Web consoles
        # get a unique published host port so several services can coexist.
        host_port = 38000
        for d in topo.devices.values():
            if role.get(d.id) != "service":
                continue
            svc = service_for(d.type_key)
            if svc is None:
                continue
            ports = []
            for p in svc.ports:
                ports.append({"container": p.container, "host": host_port,
                              "label": p.label, "web": p.web, "path": p.path})
                host_port += 1
            # some images need to advertise their own service name (e.g. Redpanda's
            # kafka address); `{svc}` in the catalog command/env is filled in here.
            sname = _svc(d.name)
            command = [a.replace("{svc}", sname) for a in svc.command]
            env = {k: v.replace("{svc}", sname) for k, v in svc.env.items()}
            cfg.services.append(ServiceSpec(
                name=d.name, type_key=d.type_key, image=svc.image,
                summary=svc.summary, command=command, env=env, ports=ports))

        # cloud compute (instance / container) — a plain bridge container the student can
        # log into and run an app on, reaching services by name. Image from the element's
        # property; keep it alive (base OS images would exit) unless a Command is given.
        import shlex
        for d in topo.devices.values():
            if role.get(d.id) != "compute":
                continue
            p = props[d.id]
            if d.type_key == "container":
                image = _norm_image(p.get("Image") or "alpine:latest")
                summary = f"Container ({image})."
            else:
                image = _norm_image(p.get("Image") or "ubuntu:22.04")
                summary = f"Compute instance ({image}, {p.get('Type', 'vm')})."
            cmd = p.get("Command") or ""
            command = shlex.split(cmd) if cmd.strip() else ["tail", "-f", "/dev/null"]
            cfg.services.append(ServiceSpec(
                name=d.name, type_key=d.type_key, image=image, summary=summary,
                command=command, env={}, ports=[]))

        # auto-wire an observability stack so Prometheus/Grafana actually show data
        host_port = self._wire_observability(cfg, host_port)

        # routers
        ridx = 0
        spec_of: dict[str, RouterSpec] = {}        # router did -> its spec
        rtr_seg_ip: dict[tuple, str] = {}          # (did, seg) -> this router's ip on seg
        rtr_seg_dev: dict[tuple, int] = {}         # (did, seg) -> tun index (1-based)
        seg_routers: dict[int, list] = {}          # seg -> [router dids on it]
        for did, r in role.items():
            if r != "router":
                continue
            ifaces = []
            pos = 0
            for l in by_device.get(did, []):
                if l not in kept:
                    continue
                seg = seg_ids[seg_of_link[l.id]]
                key = (l.id, did)
                ridx += 1
                pos += 1
                ifaces.append(IfaceSpec(ip=iface_ip[key] + "/24",
                                        mac=mac(seg, 1, ridx), ep=eps[key]))
                rtr_seg_ip[(did, seg)] = iface_ip[key]
                rtr_seg_dev[(did, seg)] = pos          # matches run_grouter's tun{pos}
                seg_routers.setdefault(seg, []).append(did)
            if ifaces:
                spec = RouterSpec(name=name[did], ifaces=ifaces)
                cfg.routers.append(spec)
                spec_of[did] = spec

        # static inter-router routes: each router needs a route to every subnet it is
        # NOT directly on, via the neighbouring router on the shortest path. (There is no
        # routing protocol between the C routers, so we compute the static routes here.)
        self._add_static_routes(cfg, spec_of, rtr_seg_ip, rtr_seg_dev, seg_routers)

        return cfg

    @staticmethod
    def _add_static_routes(cfg, spec_of, rtr_seg_ip, rtr_seg_dev, seg_routers) -> None:
        import ipaddress
        from collections import deque

        routers = list(spec_of.keys())
        # router adjacency: two routers are neighbours if they share a segment (a
        # router-to-router link), which gives the gateway IPs on that link.
        adj: dict = {d: {} for d in routers}
        for _seg, rtrs in seg_routers.items():
            for a in rtrs:
                for b in rtrs:
                    if a != b:
                        adj[a][b] = _seg

        for did in routers:
            my_segs = {seg for (d, seg) in rtr_seg_ip if d == did}
            # BFS: first-hop neighbour toward every reachable router
            dist = {did: 0}
            firsthop: dict = {did: None}
            q = deque([did])
            while q:
                cur = q.popleft()
                for nb in adj[cur]:
                    if nb not in dist:
                        dist[nb] = dist[cur] + 1
                        firsthop[nb] = nb if cur == did else firsthop[cur]
                        q.append(nb)
            routes = []
            for seg, cidr in cfg.subnets.items():
                if seg in my_segs:
                    continue                                   # directly connected
                cand = [c for c in seg_routers.get(seg, []) if c in dist and c != did]
                if not cand:
                    continue                                   # unreachable from here
                best = min(cand, key=lambda c: dist[c])
                nh = firsthop[best]
                if nh is None:
                    continue
                shared = adj[did][nh]
                net = ipaddress.ip_network(cidr)
                routes.append({"net": str(net.network_address),
                               "mask": str(net.netmask),
                               "gw": rtr_seg_ip[(nh, shared)],
                               "dev": rtr_seg_dev[(did, shared)]})
            spec_of[did].routes = routes

    @staticmethod
    def _wire_observability(cfg: RuntimeConfig, host_port: int) -> int:
        """If the canvas has Metrics/Dashboards, make them actually observe the lab:
        add a cAdvisor sidecar (universal per-container metrics), point Prometheus at it,
        and provision Grafana with the datasource + a starter dashboard. Returns the next
        free host port."""
        metrics = [s for s in cfg.services if s.type_key == "metrics"]
        dashboards = [s for s in cfg.services if s.type_key == "dashboard"]
        if not metrics and not dashboards:
            return host_port

        # cAdvisor — exposes CPU/mem/net for EVERY container, so any topology is
        # observable without the apps exporting anything. It's infra (not a canvas node).
        cfg.services.append(ServiceSpec(
            name="cAdvisor", type_key="_cadvisor",
            image="gcr.io/cadvisor/cadvisor:v0.49.1",
            summary="Per-container CPU / memory / network metrics for the whole lab.",
            command=["-housekeeping_interval=2s", "-docker_only=true"],   # fresher data
            ports=[{"container": 8080, "host": host_port, "label": "cadvisor", "web": True}],
            volumes=["/:/rootfs:ro", "/var/run:/var/run:ro", "/sys:/sys:ro",
                     "/var/lib/docker/:/var/lib/docker:ro", "/dev/disk/:/dev/disk:ro"],
            privileged=True))
        host_port += 1

        for prom in metrics:        # Prometheus scrapes cAdvisor (+ itself)
            prom.files["observability/prometheus.yml"] = _PROMETHEUS_YML
            prom.volumes.append(
                "./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro")

        if dashboards and metrics:  # Grafana: datasource -> Prometheus + a dashboard
            prom_svc = _svc(metrics[0].name)
            for graf in dashboards:
                graf.files["observability/grafana/ds.yml"] = _GRAFANA_DS.format(prom=prom_svc)
                graf.files["observability/grafana/dash.yml"] = _GRAFANA_PROVIDER
                graf.files["observability/grafana/container.json"] = _grafana_dashboard_json()
                graf.volumes += [
                    "./observability/grafana/ds.yml:"
                    "/etc/grafana/provisioning/datasources/ds.yml:ro",
                    "./observability/grafana/dash.yml:"
                    "/etc/grafana/provisioning/dashboards/dash.yml:ro",
                    "./observability/grafana/container.json:"
                    "/var/lib/grafana/dashboards/container.json:ro",
                ]
        return host_port


def validate(topo: Topology) -> list[dict]:
    """Advisory topology lint — never blocks, just surfaces issues a student should see.

    Returns a list of {level: 'warn'|'info', device: name|None, message}.
    """
    issues: list[dict] = []
    role = {d.id: _role(d.type_key) for d in topo.devices.values()}
    name = {d.id: d.name for d in topo.devices.values()}
    nbrs: dict[str, list] = {d.id: [] for d in topo.devices.values()}
    for l in topo.links.values():
        nbrs[l.source_id].append(l.target_id)
        nbrs[l.target_id].append(l.source_id)

    # 1. isolated devices (degree 0) — not part of any network
    for did, r in role.items():
        if r != "group" and not nbrs[did]:
            issues.append({"level": "warn", "device": name[did],
                           "message": f"{name[did]} isn't connected to anything."})

    # 2. machines with no gateway (no router on any of their subnets) — islands.
    #    A host on a switched/SDN L2 domain is fine without a router (it reaches its
    #    LAN at layer 2), so only warn for hosts NOT on any switch/OVS.
    cfg = RuntimeCompiler().compile(topo)
    id_of = {n: i for i, n in name.items()}
    for m in cfg.machines:
        if m.gw:
            continue
        did = id_of.get(m.name)
        on_lan = did is not None and any(
            role.get(nb) in ("switch", "ovs") for nb in nbrs[did])
        if on_lan:
            continue
        issues.append({"level": "warn", "device": m.name,
                       "message": f"{m.name} has no gateway (no router on its "
                                  f"subnet) — it can only reach hosts on its own subnet."})

    # 2b. SDN advisories — teach the control-plane relationship
    for o in cfg.ovs_switches:
        if not o.controller:
            issues.append({"level": "warn", "device": o.name,
                           "message": f"{o.name} has no controller — it runs fail-secure "
                                      f"with an empty flow table, so it drops ALL traffic. "
                                      f"Connect a controller to give it switching behavior."})
    for c in cfg.controllers:
        if not c.switches:
            issues.append({"level": "warn", "device": c.name,
                           "message": f"{c.name} isn't programming any switch — connect "
                                      f"it to an OVS for it to control."})

    # 3. L2 loop among switches/hubs — our switches don't run STP, so a loop floods
    l2 = {"switch", "hub", "ovs"}
    uf = _UF()
    looped = False
    for l in topo.links.values():
        if role.get(l.source_id) in l2 and role.get(l.target_id) in l2:
            if uf.find(l.source_id) == uf.find(l.target_id):
                looped = True
            uf.union(l.source_id, l.target_id)
    if looped:
        issues.append({"level": "warn", "device": None,
                       "message": "Switch loop detected — the switches have no spanning "
                                  "tree, so a loop will flood broadcasts. Remove a link."})

    # 4. compiler notes (e.g. grouping devices whose links are organizational only)
    for note in cfg.notes:
        issues.append({"level": "info", "device": None, "message": note})
    return issues


def address_map(topo: Topology) -> dict[str, dict]:
    """Per-device addressing for the inspector / canvas labels.

    Returns {device_name: {role, interfaces:[{name, ip, mac, subnet, gateway, peer}], …}}.
    IPs/MACs come from compiling the topology, so they exist before anything runs.
    """
    import ipaddress
    cfg = RuntimeCompiler().compile(topo)

    def subnet(cidr: str) -> str:
        return str(ipaddress.ip_interface(cidr).network)

    out: dict[str, dict] = {}
    for m in cfg.machines:
        out[m.name] = {"role": "machine", "interfaces": [
            {"name": f"eth{i}", "ip": itf.ip, "mac": itf.mac, "subnet": subnet(itf.ip),
             "gateway": m.gw if i == 0 else None, "peer": itf.ep.peer.device}
            for i, itf in enumerate(m.ifaces)]}
    for r in cfg.routers:
        out[r.name] = {"role": "router", "interfaces": [
            {"name": f"eth{i}", "ip": itf.ip, "mac": itf.mac, "subnet": subnet(itf.ip),
             "gateway": None, "peer": itf.ep.peer.device}
            for i, itf in enumerate(r.ifaces)]}
    for s in cfg.switches:
        out[s.name] = {"role": "switch", "ports": len(s.eps), "interfaces": [],
                       "peers": [e.peer.device for e in s.eps]}
    return out
