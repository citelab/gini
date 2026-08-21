"""RuntimeCompiler — lower a canvas Topology onto the portable user-space runtime.

Generalizes the hand-written R0 wiring to any topology:
  * classify devices (machine / switch / router / grouping),
  * find L2 broadcast domains (segments) and give each a subnet,
  * assign IPs, gateways, MACs, and per-endpoint UDP ports,
  * emit machine/switch/router specs that gini.runtime can run (in-process or Docker).

Cloud "grouping" devices (VPC, cloud-subnet, region, cluster, pod, instance-group) are
organizational in R0 and are skipped as runtime nodes (links touching them are
dropped, with a note). Cloud endpoints (instances, containers, LBs, …) run as machines.
(Plain IP subnets are NOT a device: each L2 broadcast domain is auto-assigned a /24.)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..domain import devices as _dev
from ..domain.topology import Topology


def _gini_home() -> Path:
    # Same rule as app.paths.gini_home, replicated so this service avoids an `app` import cycle.
    return Path(os.environ.get("GINI_HOME_DIR") or (Path.home() / ".gini")).expanduser()
from .cloud_catalog import is_service, service_for

ROUTERS = {"router", "firewall"}
SWITCHES = {"switch", "hub"}        # plain L2 — live in the shared `fabric` container
GROUPS = {"vpc", "cloud_subnet", "region"}
# OS Zoo: emulated historical OSes, one container each, screen served over noVNC (a web port).
# "BYO-style" elements carry Emulator/Image/Rom properties and boot via the generic BYO path — the
# generic "Classic OS (your image)" plus the convenience presets (Mac System 7, Windows 3.11) whose
# properties are simply pre-filled with a download URL (GINI still ships no proprietary image).
OSZOO_BYO_KEYS = {"oszoo_byo", "msdos", "mac7", "win31"}
OSZOO_KEYS = {"freedos", "kolibri", "menuet"} | OSZOO_BYO_KEYS
# Sources/Sinks: instruments that run INSIDE a donor container — they get no runtime node
# of their own, and their (attach) edges are never wired.
RIDERS = {k for k, dt in _dev.REGISTRY.items() if getattr(dt, "rider", False)}
K8S_ROLES = {"k8s_cluster": "k8scluster", "pod": "k8sworkload",
             "instance_group": "hpa", "k8s_node": "k8snode"}

# SDN: an OVS is an OpenFlow switch that runs as its OWN container (the gRouter in
# --openflow mode), programmed by a controller over a management channel. A controller
# is the control plane — it is NOT a data host and gets no data-plane IP/gateway.
DEFAULT_OF_PORT = 6633
DEFAULT_OF_APP = "gini.samples.switch"

# GINI32: every real board is served by one shared relay container, so a board's fabric
# endpoint lives at this service name. (The board itself is out on the physical LAN.)
GBRIDGE_SVC = "gbridge"


def _role(type_key: str) -> str:
    if type_key in ROUTERS:
        return "router"
    if type_key in SWITCHES:
        return "switch"
    if type_key == "ovs":
        return "ovs"
    if type_key == "controller":
        return "controller"
    if type_key in K8S_ROLES:          # kubernetes: real k3s cluster + manifests
        return K8S_ROLES[type_key]
    if type_key == "function":         # serverless — a handler in the shared faas runtime
        return "function"
    if is_service(type_key):           # cloud managed service (own container, bridge net)
        return "service"
    if type_key in ("instance", "container", "kinstance"):   # cloud compute — own bridge container
        return "compute"                                     # (kinstance = VM-isolated via Kata)
    if type_key == "security_group":   # a policy, not a container — drives member iptables
        return "secgroup"
    if type_key == "vnf":              # NFV: an inline network function (forwarding container)
        return "vnf"
    if type_key == "xv6":              # standalone teaching kernel (QEMU-RISC-V) — no fabric
        return "xv6"
    if type_key in OSZOO_KEYS:         # OS Zoo: emulated historical OS, embedded via noVNC
        return "oszoo"
    if type_key == "gini32":           # a REAL ESP32 board: addressed on the fabric like a
        return "gini32"                # host, but reached through the gbridge relay, not a
                                       # container of its own — see _build_gbridge().
    if type_key in ("terminal", "storage_volume"):   # xv6 peripherals: pure UI, no
        return "peripheral"                          # container; never emitted/addressed
    if type_key in RIDERS:             # Sources/Sinks: run on a donor, no container of their own
        return "rider"
    if type_key in GROUPS:
        return "group"
    return "machine"                   # host = a node on the simulated tun fabric


def _svc(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower()) or "node"


def _hostname(name: str) -> str:
    """The hostname to set inside the machine container — the user's element name (e.g.
    'toronto.on') made into a valid hostname, so `hostname` at the shell matches the label on
    the canvas and the student needs no mental mapping. Falls back to the service name."""
    h = re.sub(r"[^a-zA-Z0-9.-]", "-", (name or "").strip()).strip(".-")
    return h or _svc(name)


def _cpus_for(device) -> float:
    """CPU limit (vCPUs) for a device from its size tier — 0.5/1/2/4 for S/M/L/XL."""
    from ..domain import pricing
    return pricing.size_tier(pricing.size_level(getattr(device, "size", 1)))[1]


def _toolkit_for(device) -> str:
    """Which Machine image this host is built from — read off its `Toolkit` property.

    LEAN is the default and the one to encourage: an Alpine host with the tools a student actually
    types (ip/ping/tcpdump/dig/curl/nc/iperf3/nmap), an order of magnitude smaller than the Debian
    image. A host only opts into FULL when its experiment genuinely needs the heavy services —
    bind9 for the DNS chapter, postfix for mail, ettercap/dsniff for the spoofing labs.

    Anything unrecognised means lean: a typo must not silently pull in the 10x image."""
    if getattr(device, "type_key", "") == "desktop":     # the headful Desktop element is always gui
        return "gui"
    props = getattr(device, "properties", None) or {}
    want = str(props.get("Toolkit", "")).strip().lower()
    return want if want in ("full", "security", "gui") else "lean"


def _xv6_harts(device) -> int:
    """xv6 QEMU CPU count (-smp), driven by the SIZE tier: S/M -> 1 hart, L -> 2, XL -> 4.

    The Size control is the single source of truth for how big a machine is — a student who picks
    S must not silently get a 2-core kernel. (An earlier build floored this at 2 so lock
    contention was always observable; that made the Inspector say "0.5 vCPU" while the Machine Lab
    said "2 cores", which is worse than the thing it fixed.)

    Consequence, and it is deliberate: **contention is impossible on one core**, so an S/M machine
    shows an all-zero Lock Lab. The Lock Lab says so and tells the student to size up to L.
    """
    v = _cpus_for(device)                          # the shown vCPU count: 0.5/1/2/4
    return max(1, min(4, int(round(v))))


def _int(v, default: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _k8s_deployment_yaml(d: dict) -> str:
    return (f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {d['name']}\n"
            f"  labels: {{app: {d['name']}}}\nspec:\n  replicas: {d['replicas']}\n"
            f"  selector:\n    matchLabels: {{app: {d['name']}}}\n  template:\n"
            f"    metadata:\n      labels: {{app: {d['name']}}}\n    spec:\n"
            f"      containers:\n        - name: {d['name']}\n          image: {d['image']}\n"
            f"          ports:\n            - containerPort: {d['port']}\n"
            f"          resources:\n            requests:\n              cpu: 50m")


def _k8s_service_yaml(d: dict) -> str:
    return (f"apiVersion: v1\nkind: Service\nmetadata:\n  name: {d['name']}\nspec:\n"
            f"  selector: {{app: {d['name']}}}\n  ports:\n    - port: {d['port']}\n"
            f"      targetPort: {d['port']}")


def _k8s_hpa_yaml(d: dict) -> str:
    h = d["hpa"]
    return (f"apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n"
            f"  name: {d['name']}\nspec:\n  scaleTargetRef:\n    apiVersion: apps/v1\n"
            f"    kind: Deployment\n    name: {d['name']}\n  minReplicas: {h['min']}\n"
            f"  maxReplicas: {h['max']}\n  metrics:\n    - type: Resource\n"
            f"      resource:\n        name: cpu\n        target:\n"
            f"          type: Utilization\n          averageUtilization: {h['cpu']}")


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
    # --- internet / NAT gateway (the drawn "Internet" element) --------------- #
    gateway: bool = False          # this node is the on-fabric NAT gateway to the world
    fabric_default: bool = False   # send 0.0.0.0/0 INTO the fabric (egress via the gateway)
    fabric_gw: str | None = None   # for the gateway: the local router IP for the return path
    cpus: float = 0.0              # CPU limit from the size tier (0 = unset)
    # Which image this host is built from: "lean" (Alpine, the default — small and fast to boot)
    # or "full" (Debian + bind9/postfix/ettercap…, for the book's heavy experiments). This is a
    # DIFFERENT axis from the size tier above: size = how much CPU it gets and what it costs;
    # toolkit = what software is installed in it. A lean host with an XL cap is perfectly valid.
    toolkit: str = "lean"
    novnc_port: int = 0            # headful ("gui") host: published host port for its noVNC console
    # --- inline VNF (NFV service function) ----------------------------------- #
    forward: bool = False          # IP-forward between its interfaces (a transit node)
    nf: str = ""                   # the network function kind: firewall|block|ids|cache|shaper
    nf_rules: str = ""             # the function's config (e.g. firewall: "deny 10.0.3.0/24")


@dataclass
class IfaceSpec:
    ip: str
    mac: str
    ep: Endpoint
    link_id: str = ""          # the topology link this interface sits on


def _is_ipv4(s: str) -> bool:
    parts = (s or "").strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _valid_cidr(s: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_network((s or "").strip(), strict=False)
        return True
    except (ValueError, TypeError):
        return False


def _parse_ingress(text: str, sg_by_name: dict) -> list:
    """Parse a Security Group's Ingress field into rule dicts {port, cidrs, svcs}. Rules are
    `;`/newline separated, each like '5432 from app', '80 from anywhere', 'from 10.0.0.0/8'
    or '443'. 'from <sg-name>' expands to that SG's member service names (SG→SG rules)."""
    import re as _re
    out = []
    for raw in _re.split(r"[;\n]", text or ""):
        s = raw.strip()
        if not s:
            continue
        m = _re.match(r"(?i)^(?:port\s+)?(\d+|all|any|\*)?\s*(?:from\s+(.+))?$", s)
        if not m:
            continue
        ptok = (m.group(1) or "").lower()
        src = (m.group(2) or "anywhere").strip()
        port = None if ptok in ("", "all", "any", "*") else int(ptok)
        cidrs, svcs, low = [], [], src.lower()
        if low in ("anywhere", "any", "all", "0.0.0.0/0", "public", "internet"):
            cidrs.append("0.0.0.0/0")
        elif "/" in src and _valid_cidr(src):
            cidrs.append(src)
        elif low in sg_by_name:
            svcs.extend(sg_by_name[low])
        else:
            svcs.append(_svc(src))                 # treat an unknown name as a service/host
        out.append({"port": port, "cidrs": cidrs, "svcs": svcs})
    return out


def _sg_script(rules: list) -> str:
    """A stateful default-deny-inbound iptables script for one member's union of SG rules."""
    L = [
        "iptables -P INPUT DROP", "iptables -P FORWARD DROP", "iptables -P OUTPUT ACCEPT",
        "iptables -A INPUT -i lo -j ACCEPT",
        "iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        # the GINI telemetry agent is infra — always allow it (so the dashboard still polls)
        "for ip in $(getent hosts cloudfabric 2>/dev/null | awk '{print $1}'); do "
        'iptables -A INPUT -s "$ip" -j ACCEPT; done',
    ]
    for r in rules:
        dport = f" --dport {r['port']}" if r.get("port") else ""
        for cidr in r.get("cidrs", []):
            L.append(f"iptables -A INPUT -p tcp{dport} -s {cidr} -j ACCEPT")
        if r.get("svcs"):
            names = " ".join(r["svcs"])
            L.append(f"for ip in $(getent hosts {names} 2>/dev/null | awk '{{print $1}}'); "
                     f'do iptables -A INPUT -p tcp{dport} -s "$ip" -j ACCEPT; done')
    return "\n".join(L) + "\n"


@dataclass
class SwitchSpec:
    name: str
    eps: list[Endpoint]
    hub: bool = False          # True = a Layer-1 hub (flood-all repeater), not a learning switch


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
    cpus: float = 0.0              # CPU limit from the size tier (0 = unset)
    networks: list = field(default_factory=lambda: ["gini"])   # Docker networks to attach to
    runtime: str = ""             # OCI runtime override (e.g. "kata" for a Kata Instance)


@dataclass
class K8sSpec:
    """A real Kubernetes cluster (k3s in a container) + the workloads to deploy in it.
    `deployments` = [{name,image,replicas,port,hpa:{min,max,cpu}|None}]; `manifests` is
    the combined YAML applied via `kubectl apply` once the cluster is up."""
    name: str
    svc: str
    image: str
    deployments: list = field(default_factory=list)
    manifests: str = ""


@dataclass
class FabricSpec:
    """The GINI Cloud Fabric agent: one container that polls each cloud service's native
    metrics and serves them to gBuilder. `services` = [{name,type,host,port,creds}]."""
    services: list = field(default_factory=list)
    port: int = 9099


@dataclass
class NetworkSpec:
    """A VPC/subnet rendered as a real Docker bridge network. Containers on different VPC
    networks can't reach each other; within one they can. A VPC's shared net is `internal`
    (no internet of its own — the implicit VPC fabric); a per-VPC *egress* net is a normal
    bridge that public-subnet members also join for real internet + host-published consoles.
    `cidr` is the network's subnet (empty = let Docker auto-assign)."""
    name: str                      # docker network name (the VPC's slug, or <slug>_egress)
    cidr: str                      # e.g. 10.0.0.0/16, or "" for auto
    label: str = ""                # display name (for notes/UI)
    region: str = ""
    internal: bool = False         # True = no external connectivity (the VPC fabric net)


# Fallback resolver when an Internet element carries no DNS of its own (an older saved
# topology, drawn before the property existed). Google's is used because it is the one
# public resolver reachable from essentially every network that has internet at all.
DEFAULT_PUBLIC_DNS = "8.8.8.8"


def _internet_dns(topo) -> str:
    """The resolver to hand out, taken from the Internet element on the canvas.

    Blanking the property is a legitimate choice — "internet, but resolve names
    yourself" — so an explicitly empty value is honoured rather than back-filled.
    """
    for d in topo.devices.values():
        if d.type_key != "cloud":
            continue
        props = d.properties or {}
        if "DNS" not in props:
            return DEFAULT_PUBLIC_DNS          # saved before the property existed
        raw = str(props.get("DNS", "")).strip()
        return raw if _valid_ip(raw) else ""
    return ""


def _valid_ip(text: str) -> bool:
    parts = (text or "").split(".")
    return (len(parts) == 4
            and all(p.isdigit() and len(p) <= 3 and 0 <= int(p) <= 255 for p in parts))


@dataclass
class GBridgeSpec:
    """One drawn GINI32 element: a real board's end of a fabric link.

    Unlike every other spec this does NOT become a container. The `gbridge` relay
    owns `ep` (the fabric-side UDP endpoint) and forwards frames over the physical
    LAN to whichever address the board checked in from. Everything here except
    `board_id` is handed to the board in the relay's HELLO_ACK, so the canvas stays
    the single source of truth for the board's fabric identity.
    """
    name: str
    board_id: str                  # must match the id flashed on the board
    ip: str                        # fabric address assigned from its segment
    mask: str
    gw: str                        # its gateway (the router on that segment)
    mac: str
    ep: Endpoint
    mode: str = "nat"              # nat = devices hidden behind `ip`; routed = own subnet
    physical_subnet: str = ""      # the subnet behind the radio (always allocated)
    mtu: int = 1400
    seg: int = -1                  # the segment it sits on (routed-mode route emission)
    # The hotspot the board raises for real devices. Assigned here, not baked into
    # firmware, so two boards never collide and a lab can be renamed without a reflash.
    ap_ssid: str = ""
    ap_pass: str = ""
    # The resolver the board's DHCP server hands to real devices, or "" when the canvas
    # has no Internet element. Empty is meaningful, not missing: with nothing to egress
    # through, offering a resolver would promise name resolution that cannot work.
    dns: str = ""


@dataclass
class RuntimeConfig:
    machines: list[MachineSpec] = field(default_factory=list)
    gbridge: list[GBridgeSpec] = field(default_factory=list)   # real GINI32 boards
    switches: list[SwitchSpec] = field(default_factory=list)
    routers: list[RouterSpec] = field(default_factory=list)
    ovs_switches: list[OvsSpec] = field(default_factory=list)
    controllers: list[ControllerSpec] = field(default_factory=list)
    services: list[ServiceSpec] = field(default_factory=list)
    fabric: "FabricSpec | None" = None                      # cloud telemetry agent
    k8s: list = field(default_factory=list)                 # real k3s clusters + manifests
    faas: list = field(default_factory=list)                # serverless functions (shared runtime)
    networks: list = field(default_factory=list)            # VPCs as isolated Docker networks
    firewalls: list = field(default_factory=list)           # security-group iptables per member
    subnets: dict[int, str] = field(default_factory=dict)   # seg -> cidr
    notes: list[str] = field(default_factory=list)

    # -- emit for the in-process simulator / Docker ------------------------- #
    def to_runtime(self, docker: bool) -> dict:
        return {
            "machines": [
                {"name": _svc(m.name), "hostname": _hostname(m.name), "gw": m.gw,
                 "gateway": m.gateway, "fabric_default": m.fabric_default,
                 "fabric_gw": m.fabric_gw, "cpus": m.cpus, "toolkit": m.toolkit,
                 "novnc_port": m.novnc_port,
                 "forward": m.forward, "nf": m.nf, "nf_rules": m.nf_rules,
                 "ifaces": [{"ip": i.ip, "mac": i.mac, "tap": f"gini{idx}",
                             "port": i.ep.wiring(docker)}
                            for idx, i in enumerate(m.ifaces)]}
                for m in self.machines
            ],
            "switches": [
                {"name": _svc(s.name), "ports": [e.wiring(docker) for e in s.eps],
                 "hub": s.hub}
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
                 "files": s.files, "cpus": s.cpus, "networks": s.networks,
                 "runtime": s.runtime}
                for s in self.services
            ],
            "fabric": ({"port": self.fabric.port, "services": self.fabric.services}
                       if self.fabric else None),
            "k8s": [{"name": _svc(k.name), "image": k.image,
                     "deployments": k.deployments} for k in self.k8s],
            "faas": self.faas,        # serverless: [{name, handler, code}] -> one faas container
            # VPC/subnet Docker networks (empty -> only the flat `gini` bridge).
            "networks": [{"name": n.name, "cidr": n.cidr, "label": n.label,
                          "region": n.region, "internal": n.internal} for n in self.networks],
            # security groups -> per-member iptables (a sidecar in each member's netns).
            "firewalls": self.firewalls,
            # Real GINI32 boards. One `gbridge` relay container serves all of them:
            # `fabric` is its end of the link to the board's router, and the rest is
            # the identity it hands the board when the board announces itself.
            "gbridge": [
                {"board_id": b.board_id, "name": _svc(b.name), "label": b.name,
                 "ip": b.ip, "mask": b.mask, "gw": b.gw, "mac": b.mac, "mtu": b.mtu,
                 "mode": b.mode, "physical_subnet": b.physical_subnet,
                 "ap_ssid": b.ap_ssid, "ap_pass": b.ap_pass, "dns": b.dns,
                 "fabric": b.ep.wiring(docker)}
                for b in self.gbridge
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


# Traefik dynamic (file-provider) config: route everything to the drawn backends.
_TRAEFIK_DYNAMIC = (
    "http:\n"
    "  routers:\n"
    "    gini:\n"
    "      rule: \"PathPrefix(`/`)\"\n"
    "      entryPoints: [web]\n"
    "      service: gini\n"
    "  services:\n"
    "    gini:\n"
    "      loadBalancer:\n"
    "        servers:\n"
    "{servers}\n"
)

# nginx load-balancer config: an upstream over the drawn backends (honoring the chosen
# algorithm) + a /nginx_status endpoint so the cloud fabric can read its request rate.
_NGINX_LB = (
    "events {{}}\n"
    "http {{\n"
    "  upstream gini_backend {{\n"
    "{algo}"
    "{servers}\n"
    "  }}\n"
    "  server {{\n"
    "    listen 80;\n"
    "    location / {{\n"
    "      proxy_pass http://gini_backend;\n"
    "      proxy_set_header Host $host;\n"
    "    }}\n"
    "    location /nginx_status {{ stub_status; }}\n"
    "  }}\n"
    "}}\n"
)


def _grafana_dashboard_json() -> str:
    """A starter Grafana dashboard. It leads with Prometheus *pipeline-health* panels
    (targets up, samples scraped) that ALWAYS have data — so the board is never blank and
    doubles as a built-in diagnostic — then shows cAdvisor per-container CPU/mem/network
    (best-effort; cAdvisor can be sparse on Docker Desktop). Built via json.dumps so the
    PromQL (with quotes) is always valid JSON."""
    import json

    ds = {"type": "prometheus", "uid": "prometheus"}      # bind panels to the datasource

    def ts(pid, title, expr, legend, x, y, w=12, h=8):
        return {"id": pid, "type": "timeseries", "title": title, "datasource": ds,
                "gridPos": {"h": h, "w": w, "x": x, "y": y},
                "fieldConfig": {"defaults": {}, "overrides": []},
                "targets": [{"expr": expr, "legendFormat": legend, "refId": "A",
                             "datasource": ds}]}

    def stat(pid, title, expr, legend, x, y, w=12, h=6):
        return {"id": pid, "type": "stat", "title": title, "datasource": ds,
                "gridPos": {"h": h, "w": w, "x": x, "y": y},
                "options": {"colorMode": "background", "graphMode": "none",
                            "textMode": "value_and_name", "reduceOptions":
                            {"calcs": ["lastNotNull"]}},
                "fieldConfig": {"defaults": {"mappings": [
                    {"type": "value", "options": {"0": {"text": "DOWN", "color": "red"},
                                                  "1": {"text": "UP", "color": "green"}}}],
                    "thresholds": {"steps": [{"color": "red", "value": None},
                                             {"color": "green", "value": 1}]}},
                    "overrides": []},
                "targets": [{"expr": expr, "legendFormat": legend, "refId": "A",
                             "datasource": ds}]}

    return json.dumps({
        "title": "GINI lab overview", "uid": "gini-containers",
        "schemaVersion": 39, "version": 1, "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"},
        "panels": [
            # --- pipeline health: always populated (Prometheus knows its own targets) ---
            stat(10, "Scrape targets up", "up", "{{job}}", 0, 0, w=8, h=6),
            ts(11, "Samples scraped / target", "scrape_samples_scraped", "{{job}}",
               8, 0, w=16, h=6),
            # --- per-container resources (cAdvisor; best-effort on Docker Desktop) ---
            ts(1, "CPU (cores) by container",
               'rate(container_cpu_usage_seconds_total{name!=""}[1m])', "{{name}}", 0, 6),
            ts(2, "Memory (bytes) by container",
               'container_memory_usage_bytes{name!=""}', "{{name}}", 12, 6),
            ts(3, "Network RX (bytes/s) by container",
               'rate(container_network_receive_bytes_total{name!=""}[1m])',
               "{{name}}", 0, 14, w=24),
        ],
    }, indent=2)


class RuntimeCompiler:
    def compile(self, topo: Topology) -> RuntimeConfig:
        cfg = RuntimeConfig()
        role = {d.id: _role(d.type_key) for d in topo.devices.values()}
        name = {d.id: d.name for d in topo.devices.values()}
        # the drawn "Internet" element (type_key "cloud") is the on-fabric NAT gateway:
        # it sits on the fabric like a host but also has an external uplink + does NAT.
        gw_dids = {d.id for d in topo.devices.values() if d.type_key == "cloud"}

        # 1. keep links not touching grouping devices; pull out SDN control links
        #    (controller↔OVS) — they are a management association, not a data segment.
        kept = []
        control_links = []
        ovs_controller: dict[str, str] = {}        # ovs did -> controller did
        ctrl_switches: dict[str, list[str]] = {}   # controller did -> [ovs did]
        for l in topo.links.values():
            rs, rt = role.get(l.source_id), role.get(l.target_id)
            if getattr(l, "kind", "link") == "attach" or rs == "rider" or rt == "rider":
                # a Source/Sink mount: the rider runs inside the donor, so this is not a cable.
                cfg.notes.append(f"skipped rider attach: "
                                 f"{name.get(l.source_id)}–{name.get(l.target_id)}")
            elif rs == "group" or rt == "group":
                cfg.notes.append(f"skipped link touching grouping: "
                                 f"{name.get(l.source_id)}–{name.get(l.target_id)}")
            elif rs in K8S_ROLES.values() or rt in K8S_ROLES.values():
                # kubernetes associations (cluster↔pod, pod↔autoscaler) are intent for
                # manifest generation, not data-plane segments.
                cfg.notes.append(f"k8s link: {name.get(l.source_id)}–{name.get(l.target_id)}")
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
            #
            # A GINI32 board is the one endpoint that is NOT a container: it is real
            # hardware on the physical LAN. Its location is the shared `gbridge` relay,
            # which owns this end of the link and forwards to the board over the LAN.
            # The gRouter on the far side therefore needs no notion of hardware at all.
            r = role[device_id]
            loc = ("fabric" if r == "switch"
                   else GBRIDGE_SVC if r == "gini32"
                   else _svc(name[device_id]))
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
        # routers (and inline VNFs) first (so .1 is the gateway), then machines. A VNF is a
        # forwarding node in the path, so it's addressed like a router and becomes the
        # gateway on its point-to-point segments — neighbours then route THROUGH it.
        ordered = sorted(kept, key=lambda l: 0)
        # A GINI32 board is addressed exactly like a host on its segment (it presents one
        # fabric address, behind which its real devices are NATed), so it shares the
        # machine numbering pass.
        for did_role in ("router", "vnf", "machine", "gini32"):
            for l in kept:
                seg = seg_ids[seg_of_link[l.id]]
                base = f"10.0.{seg + 1}."
                for end in (l.source_id, l.target_id):
                    if role[end] != did_role:
                        continue
                    key = (l.id, end)
                    if key in iface_ip:
                        continue
                    if did_role in ("router", "vnf"):
                        n = seg_rtr.get(seg, 0) + 1
                        seg_rtr[seg] = n
                        ip = base + str(n)            # .1, .2 ...
                        seg_gateway.setdefault(seg, ip)
                    else:
                        n = seg_hosts.get(seg, 9) + 1
                        seg_hosts[seg] = n
                        ip = base + str(n)            # .10, .11 ...
                    iface_ip[key] = ip

        # 5b. manual addressing: honor each device's typed static IPs, auto-fill the
        #     rest. MACs stay auto. Default gateways then follow the (possibly
        #     overridden) router address on each segment.
        if getattr(topo, "manual_addressing", False):
            for d in topo.devices.values():
                for lid, sip in (getattr(d, "static_ips", None) or {}).items():
                    key = (lid, d.id)
                    bare = (sip or "").strip().split("/")[0]
                    if key in iface_ip and _is_ipv4(bare):
                        iface_ip[key] = bare
            seg_gateway = {}
            for l in kept:
                seg = seg_ids[seg_of_link[l.id]]
                for end in (l.source_id, l.target_id):
                    if role[end] in ("router", "vnf") and (l.id, end) in iface_ip:
                        seg_gateway.setdefault(seg, iface_ip[(l.id, end)])

        # the Internet node's IP on each segment it touches; if a segment has an
        # Internet node but no router, hosts there default straight to the Internet node.
        seg_internet_ip: dict[int, str] = {}
        for l in kept:
            seg = seg_ids[seg_of_link[l.id]]
            for end in (l.source_id, l.target_id):
                if end in gw_dids and (l.id, end) in iface_ip:
                    seg_internet_ip.setdefault(seg, iface_ip[(l.id, end)])
        for seg, ip in seg_internet_ip.items():
            seg_gateway.setdefault(seg, ip)
        # the segment + IP that everything default-routes toward (first Internet node)
        gw_seg, gw_ip = next(iter(seg_internet_ip.items()), (None, None))

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
                                               mac=mac(seg, 2, midx), ep=eps[key],
                                               link_id=l.id))
                gw = seg_gateway.get(seg)        # default route via the first router seen
                if gw and end not in m_gw:
                    m_gw[end] = gw
        have_internet = bool(gw_dids)
        for did in m_order:
            if did in gw_dids:
                # the Internet element: NAT gateway. It defaults OUT its uplink (set up
                # at runtime), and routes the experiment supernet back via its local
                # router so replies reach the hosts behind it.
                cfg.machines.append(MachineSpec(
                    name=name[did], ifaces=m_ifaces[did], gw=None,
                    gateway=True, fabric_gw=m_gw.get(did)))
            else:
                # an ordinary host: when an Internet element is on the canvas, its
                # default route goes INTO the fabric so internet egresses through the
                # drawn routers (traceroute then shows the real path).
                cfg.machines.append(MachineSpec(
                    name=name[did], ifaces=m_ifaces[did], gw=m_gw.get(did),
                    fabric_default=have_internet and bool(m_gw.get(did)),
                    cpus=_cpus_for(topo.devices[did]),    # size tier -> CPU limit
                    toolkit=_toolkit_for(topo.devices[did])))   # lean (default) | full

        # GINI32 boards: real hardware on the fabric. No container is emitted — the shared
        # `gbridge` relay holds this end of the link and carries frames to the board over
        # the physical LAN. We only need to hand it the identity the canvas assigned.
        for l in kept:
            seg = seg_ids[seg_of_link[l.id]]
            for end in (l.source_id, l.target_id):
                if role[end] != "gini32":
                    continue
                key = (l.id, end)
                if key not in iface_ip:
                    continue
                midx += 1
                dev = topo.devices[end]
                props = getattr(dev, "properties", None) or {}
                # Blank BoardID falls back to the element's own name. That id will not
                # match any real board — but it is UNIQUE per element, which is the
                # point: the earlier shared default made a second board vanish from the
                # relay's table silently. Emitting nothing instead would be worse still,
                # because the relay is what collects announcing boards for the Inspector
                # to offer: no entry means no relay means an empty picker and no way to
                # fix the very problem. So the element compiles, validate() flags it on
                # the canvas, and the Inspector names the boards that ARE on the air.
                board_id = str(props.get("BoardID", "")).strip() or _svc(name[end])
                mode = str(props.get("Mode", "routed")).strip().lower()
                if mode not in ("nat", "routed"):
                    mode = "routed"

                # The subnet behind this board's radio. Blank means "allocate me one":
                # every board needs a DISTINCT one, or routers end up with two routes
                # to the same network via different next hops and neither works. We
                # skip any third octet the topology's own segments already use.
                phys = str(props.get("PhysicalSubnet", "")).strip()
                if not phys:
                    used = {int(c.split(".")[2]) for c in cfg.subnets.values()}
                    used |= {int(b.physical_subnet.split(".")[2])
                             for b in cfg.gbridge if b.physical_subnet}
                    oct3 = 9
                    while oct3 in used:
                        oct3 += 1
                    phys = f"10.0.{oct3}.0/24"
                elif not _valid_cidr(phys):
                    cfg.notes.append(
                        f"{name[end]}: PhysicalSubnet {phys!r} is not a valid CIDR — "
                        f"allocating one automatically")
                    phys = ""
                    used = {int(c.split(".")[2]) for c in cfg.subnets.values()}
                    used |= {int(b.physical_subnet.split(".")[2])
                             for b in cfg.gbridge if b.physical_subnet}
                    oct3 = 9
                    while oct3 in used:
                        oct3 += 1
                    phys = f"10.0.{oct3}.0/24"

                # The hotspot real devices join. Named after the element unless the
                # user overrode it, so two boards are distinguishable on a phone.
                ap_ssid = str(props.get("ApSSID", "")).strip()
                if not ap_ssid:
                    ap_ssid = f"GINI32-{re.sub(r'[^A-Za-z0-9-]', '', name[end]) or 'board'}"
                ap_pass = str(props.get("ApPassword", "")).strip()
                # Real devices on the board's radio get a resolver ONLY when the canvas
                # has an Internet element to egress through. Without one, an iPad could
                # still be handed 8.8.8.8, would send queries into a topology with no way
                # out, and would sit there timing out — which looks like broken Wi-Fi
                # rather than a network with deliberately no internet in it. This is also
                # why DNS follows the same route as everything else the board is told:
                # the canvas decides, the board obeys.
                dns = _internet_dns(topo) if have_internet else ""
                cfg.gbridge.append(GBridgeSpec(
                    name=name[end], board_id=board_id,
                    ip=iface_ip[key], mask="255.255.255.0",
                    gw=seg_gateway.get(seg, ""), mac=mac(seg, 4, midx),
                    ep=eps[key], mode=mode,
                    # The board always serves this subnet; `mode` only decides whether
                    # the emulated side gets a ROUTE to it or the devices are hidden.
                    physical_subnet=phys, seg=seg,
                    ap_ssid=ap_ssid, ap_pass=ap_pass, dns=dns))

        # inline VNFs: a forwarding container that applies a network function between its
        # segments (the Internet-element pattern, but fabric<->fabric + an NF instead of NAT).
        # Addressed like a router above, so it's the gateway on its point-to-point segments;
        # `gw` is a next hop toward egress (a router/gateway on its OTHER segment).
        v_ifaces: dict[str, list] = {}
        v_gw: dict[str, str] = {}
        v_order: list[str] = []
        for l in kept:
            seg = seg_ids[seg_of_link[l.id]]
            for end in (l.source_id, l.target_id):
                if role[end] != "vnf":
                    continue
                key = (l.id, end)
                if key not in iface_ip:
                    continue
                midx += 1
                my_ip = iface_ip[key]
                if end not in v_ifaces:
                    v_ifaces[end] = []
                    v_order.append(end)
                v_ifaces[end].append(IfaceSpec(ip=my_ip + "/24", mac=mac(seg, 3, midx),
                                               ep=eps[key], link_id=l.id))
                g = seg_gateway.get(seg)
                if g and g != my_ip and end not in v_gw:   # onward route toward egress
                    v_gw[end] = g
        vprops = {d.id: getattr(d, "properties", {}) or {} for d in topo.devices.values()}
        for did in v_order:
            p = vprops[did]
            cfg.machines.append(MachineSpec(
                name=name[did], ifaces=v_ifaces[did], gw=v_gw.get(did),
                forward=True, nf=(p.get("Kind") or "firewall"),
                nf_rules=(p.get("Rules") or ""), cpus=_cpus_for(topo.devices[did])))

        # switches (a Hub is the same fabric node in flood-all mode — no MAC learning)
        for did, r in role.items():
            if r != "switch":
                continue
            ports = [eps[(l.id, did)] for l in by_device.get(did, []) if l in kept]
            if ports:
                is_hub = topo.devices[did].type_key == "hub"
                cfg.switches.append(SwitchSpec(name=name[did], eps=ports, hub=is_hub))

        # OVS switches — own gRouter --openflow container, programmed by a controller
        props = {d.id: getattr(d, "properties", {}) or {} for d in topo.devices.values()}

        # VPCs -> isolated Docker networks. Each VPC element with members becomes its own
        # bridge (unique subnet); every element inside it (via parent_id) attaches to that
        # network instead of the flat `gini` bridge, so different VPCs can't reach each
        # other. Elements with no VPC ancestor stay on `gini` (unchanged flat behavior).
        vpc_net_of = self._build_networks(cfg, topo, name)
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
        # headful ("gui") machines publish their noVNC console on a unique host port too, so the
        # Desktop element can open the embedded screen (the machine dict carries the port).
        for m in cfg.machines:
            if m.toolkit == "gui":
                m.novnc_port = host_port
                host_port += 1
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
                summary=svc.summary, command=command, env=env, ports=ports,
                cpus=_cpus_for(d),                    # size tier -> CPU limit
                networks=vpc_net_of.get(d.id, ["gini"])))   # VPC/subnet nets (or flat bridge)

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
            elif d.type_key == "kinstance":              # VM-isolated workload via Kata
                image = _norm_image(p.get("Image") or "ubuntu:22.04")
                summary = f"Kata Instance — VM-isolated ({image})."
            else:
                image = _norm_image(p.get("Image") or "ubuntu:22.04")
                summary = f"Compute instance ({image}, {p.get('Type', 'vm')})."
            cmd = p.get("Command") or ""
            command = shlex.split(cmd) if cmd.strip() else ["tail", "-f", "/dev/null"]
            is_kata = d.type_key == "kinstance"
            cfg.services.append(ServiceSpec(
                name=d.name, type_key=d.type_key, image=image, summary=summary,
                command=command, env={}, ports=[], cpus=_cpus_for(d),    # size -> CPU
                # Kata Instances stay flat (no VPC) and run under the kata OCI runtime.
                networks=["gini"] if is_kata else vpc_net_of.get(d.id, ["gini"]),
                runtime="kata" if is_kata else ""))

        # xv6 teaching kernel — a standalone QEMU-RISC-V machine that boots a real kernel and
        # exposes its GDB stub (port 1234) so the Machine Lab's bridge can read and steer it.
        # No fabric wiring (xv6 is standalone); the time-slice tier seeds the kernel quantum.
        for d in topo.devices.values():
            if role.get(d.id) != "xv6":
                continue
            p = props[d.id]
            # the Load loop: bind-mount a host folder over kernel/shadows/ so the student edits
            # gini_sched.c in their own editor and Load rebuilds in-container. The folder can start
            # empty — the agent seeds the shipped stub into it on boot (see gini_agent.py). Mount a
            # DIRECTORY (editors save via rename, which breaks a single-file mount). It lives under
            # the GINI home (~/.gini/xv6-shadows/<name>/) so it's stable + discoverable and the
            # student's edits PERSIST across Stop/Run (unlike the ephemeral compose workdir).
            _sane = "".join(c if (c.isalnum() or c in "_.-") else "-" for c in d.name)
            _shadows_host = _gini_home() / "xv6-shadows" / _sane
            try:
                _shadows_host.mkdir(parents=True, exist_ok=True)   # exists + user-owned before `up`
            except OSError:
                pass
            cfg.services.append(ServiceSpec(
                name=d.name, type_key="xv6", image="gini-xv6:latest",
                summary="xv6 teaching kernel (QEMU-RISC-V); in-container agent serves live state.",
                command=[], env={"XV6_QUANTUM": str(p.get("Timeslice", "1")),
                                 "XV6_CPUS": str(_xv6_harts(d))},   # size stepper -> real harts
                # agent HTTP (the Machine Lab bridge talks here) + serial (the human console).
                ports=[{"container": 5000, "host": host_port, "label": "agent",
                        "web": False, "path": ""},
                       {"container": 4444, "host": host_port + 1, "label": "serial",
                        "web": False, "path": ""}],
                volumes=[f"{_shadows_host}:/opt/xv6-riscv/kernel/shadows"],
                cpus=_cpus_for(d), networks=["gini"]))
            host_port += 2

        # OS Zoo — a real historical OS under emulation. One container per element, image
        # `gini-oszoo:latest`, with ZOO_OS selecting the guest; the container runs the emulator
        # (`-vnc :0`) + websockify/noVNC and publishes the framebuffer as a web page. The Zoo Lab
        # embeds that URL in a QWebEngineView. Standalone (no fabric wiring in v1).
        for d in topo.devices.values():
            if role.get(d.id) != "oszoo":
                continue
            p = props[d.id]
            is_byo = d.type_key in OSZOO_BYO_KEYS
            os_id = "byo" if is_byo else d.type_key
            env = {"ZOO_OS": os_id,
                   "ZOO_PERSIST": "1" if str(p.get("Persist", "false")).lower() == "true" else "0"}
            if is_byo:                                    # BYO / preset: Emulator + Image (+Rom)
                env["ZOO_EMULATOR"] = str(p.get("Emulator", "qemu"))
                env["ZOO_ARCH"] = str(p.get("Arch", "x86"))
                # Image/Rom may be a local path (bind-mounted below) OR an http(s):// URL that the
                # container downloads on first boot. Pass the raw value; boot_zoo.sh decides.
                if str(p.get("Image", "")):
                    env["ZOO_IMAGE"] = str(p.get("Image", ""))
                if str(p.get("Rom", "")):                 # Basilisk II: a Mac ROM
                    env["ZOO_ROM"] = str(p.get("Rom", ""))
                if d.type_key == "win31":                 # ship Digger Remastered on the Win 3.11 C:
                    env["ZOO_ADDONS"] = "digger"          # (run it from the DOS prompt: cd\digger, digger)
            # persist downloaded guest images on the host so each OS is fetched once, not on
            # every Run (an anonymous /zoo/cache volume is discarded when the container recreates).
            # Computed inline (mirrors app.paths.oszoo_cache_dir) to keep the compiler Qt-free.
            import os
            from pathlib import Path
            _home = Path(os.environ.get("GINI_HOME_DIR") or (Path.home() / ".gini")).expanduser()
            cache = _home / "oszoo-cache"; cache.mkdir(parents=True, exist_ok=True)
            volumes = [f"{cache}:/zoo/cache"]
            def _is_url(s: str) -> bool:
                return s.startswith("http://") or s.startswith("https://")
            img = str(p.get("Image", "")) if is_byo else ""
            if img and not _is_url(img):                   # local path -> bind-mount read-only
                volumes.append(f"{img}:/zoo/byo.img:ro")   # (a URL is downloaded in the container)
            rom = str(p.get("Rom", "")) if is_byo else ""
            if rom and not _is_url(rom):                   # Basilisk II Mac ROM, read-only
                volumes.append(f"{rom}:/zoo/rom:ro")
            # Basilisk II creates its 60 Hz timer as a real-time-scheduled thread; Docker's default
            # sandbox (seccomp + no CAP_SYS_NICE) forbids RT scheduling, so that container needs to
            # be privileged. QEMU/DOSBox guests don't, so keep them unprivileged.
            needs_priv = is_byo and str(p.get("Emulator", "")) == "basilisk"
            cfg.services.append(ServiceSpec(
                name=d.name, type_key=d.type_key, image="gini-oszoo:latest",
                summary=f"OS Zoo: {d.type_key} under emulation, screen embedded over noVNC.",
                command=[], env=env, privileged=needs_priv,
                # the noVNC web console (the Zoo Lab embeds this) + the raw VNC port.
                ports=[{"container": 6080, "host": host_port, "label": "screen",
                        "web": True, "path": "/vnc.html?autoconnect=1&resize=remote"},
                       {"container": 5900, "host": host_port + 1, "label": "vnc",
                        "web": False, "path": ""}],
                volumes=volumes, cpus=_cpus_for(d), networks=["gini"]))
            host_port += 2

        # make proxies / load balancers actually route to their drawn backends
        self._wire_proxies(cfg, topo, role, name, props)
        # serverless: gather Functions into the shared faas runtime + route API Gateways to it
        self._build_faas(cfg, topo, role, name, props)
        self._wire_api_gateway(cfg, topo, role, name, props)
        # security groups: per-member iptables (default-deny inbound + the listed rules)
        self._build_security_groups(cfg, topo, role, name, props)
        # auto-wire an observability stack so Prometheus/Grafana actually show data
        host_port = self._wire_observability(cfg, host_port)
        # auto-add the cloud-fabric telemetry agent if there are cloud services to watch
        self._build_fabric(cfg)
        # real Kubernetes: k3s clusters + generated Deployment/Service/HPA manifests
        self._build_k8s(cfg, topo, role, name, props)

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
                                        mac=mac(seg, 1, ridx), ep=eps[key],
                                        link_id=l.id))
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
        # A routed-mode GINI32 board fronts a real subnet behind its radio, which no
        # router knows about. Feed those in as extra destinations reached via the board.
        extra = [(b.physical_subnet, b.seg, b.ip)
                 for b in cfg.gbridge if b.mode == "routed" and b.physical_subnet
                 and b.seg >= 0]
        # dynamic routing mode: routers boot with CONNECTED routes only — a routing
        # protocol (a control-plane program, e.g. RIP in Lua) owns the table. Installing
        # static routes too would silently fight it (same table, last writer wins).
        if getattr(topo, "routing_mode", "static") != "dynamic":
            self._add_static_routes(cfg, spec_of, rtr_seg_ip, rtr_seg_dev, seg_routers,
                                    gw_seg, gw_ip, extra)

        return cfg

    @staticmethod
    def _add_static_routes(cfg, spec_of, rtr_seg_ip, rtr_seg_dev, seg_routers,
                           gw_seg=None, gw_ip=None, extra_nets=None) -> None:
        """extra_nets: [(cidr, seg, via_ip)] — destinations that are not GINI subnets but
        hang off a node ON `seg` (a routed-mode GINI32 board's physical subnet). Routers on
        that segment route to them via `via_ip`; others hop toward a router that is."""
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
                best = min(cand, key=lambda c: (dist[c], str(c)))
                nh = firsthop[best]
                if nh is None:
                    continue
                shared = adj[did][nh]
                net = ipaddress.ip_network(cidr)
                routes.append({"net": str(net.network_address),
                               "mask": str(net.netmask),
                               "gw": rtr_seg_ip[(nh, shared)],
                               "dev": rtr_seg_dev[(did, shared)]})

            # subnets living behind a routed-mode GINI32 board (real devices on its radio)
            for cidr, bseg, via_ip in (extra_nets or []):
                try:
                    net = ipaddress.ip_network(cidr, strict=False)
                except ValueError:
                    continue
                if bseg in my_segs:                            # board is on my segment
                    routes.append({"net": str(net.network_address),
                                   "mask": str(net.netmask), "gw": via_ip,
                                   "dev": rtr_seg_dev[(did, bseg)]})
                else:                                          # hop toward its router
                    cand = [c for c in seg_routers.get(bseg, []) if c in dist and c != did]
                    if not cand:
                        continue
                    nh = firsthop[min(cand, key=lambda c: (dist[c], str(c)))]
                    if nh is None:
                        continue
                    shared = adj[did][nh]
                    routes.append({"net": str(net.network_address),
                                   "mask": str(net.netmask),
                                   "gw": rtr_seg_ip[(nh, shared)],
                                   "dev": rtr_seg_dev[(did, shared)]})

            # default route (0.0.0.0/0) toward the Internet/NAT gateway, so internet-
            # bound traffic leaves the lab through the drawn Internet element.
            if gw_seg is not None and gw_ip:
                if gw_seg in my_segs:                          # gateway is on my segment
                    routes.append({"net": "0.0.0.0", "mask": "0.0.0.0", "gw": gw_ip,
                                   "dev": rtr_seg_dev[(did, gw_seg)]})
                else:                                          # hop toward its router
                    cand = [c for c in seg_routers.get(gw_seg, [])
                            if c in dist and c != did]
                    if cand:
                        best = min(cand, key=lambda c: (dist[c], str(c)))
                        nh = firsthop[best]
                        if nh is not None:
                            shared = adj[did][nh]
                            routes.append({"net": "0.0.0.0", "mask": "0.0.0.0",
                                           "gw": rtr_seg_ip[(nh, shared)],
                                           "dev": rtr_seg_dev[(did, shared)]})
            spec_of[did].routes = routes

    # backend elements a proxy / load balancer can route HTTP traffic to
    _PROXY_BACKENDS = {"web_app", "instance", "container"}

    @classmethod
    def _wire_proxies(cls, cfg, topo, role, name, props) -> None:
        """A Reverse Proxy / Load Balancer only forwards if it has a backend config.
        Build that config from the drawn links: the connected Web Apps / Instances /
        Containers become its upstreams. Traefik gets a file-provider config; nginx gets
        an `upstream` block honoring the chosen Scheme + a /nginx_status endpoint."""
        id_of = {n: i for i, n in name.items()}
        # adjacency from the drawn links
        nbrs: dict[str, list] = {d: [] for d in topo.devices}
        for l in topo.links.values():
            nbrs[l.source_id].append(l.target_id)
            nbrs[l.target_id].append(l.source_id)

        svc_by_name = {s.name: s for s in cfg.services}
        for s in cfg.services:
            if s.type_key not in ("proxy", "load_balancer"):
                continue
            did = id_of.get(s.name)
            if did is None:
                continue
            backends = []
            for nb in nbrs.get(did, []):
                tk = topo.devices[nb].type_key
                if tk in cls._PROXY_BACKENDS:
                    port = 80
                    backends.append((_svc(name[nb]), port))
                elif role.get(nb) == "service" and tk not in ("proxy", "load_balancer"):
                    bs = svc_by_name.get(name[nb])
                    port = bs.ports[0]["container"] if (bs and bs.ports) else 80
                    backends.append((_svc(name[nb]), port))
            if not backends:
                cfg.notes.append(f"{s.name}: no backends wired — connect a Web App to it")
                continue
            sname = _svc(s.name)
            if s.type_key == "proxy":
                servers = "\n".join(f'          - url: "http://{h}:{p}"' for h, p in backends)
                s.files[f"{sname}/dynamic.yml"] = _TRAEFIK_DYNAMIC.format(servers=servers)
                s.volumes.append(f"./{sname}/dynamic.yml:/etc/traefik/dynamic/dynamic.yml:ro")
                s.command = list(s.command) + ["--providers.file.directory=/etc/traefik/dynamic"]
            else:                                          # nginx load balancer
                scheme = (props.get(did, {}).get("Scheme") or "round-robin").lower()
                directive = {"least_conn": "    least_conn;\n", "least-conn": "    least_conn;\n",
                             "ip_hash": "    ip_hash;\n", "ip-hash": "    ip_hash;\n"}.get(scheme, "")
                servers = "\n".join(f"    server {h}:{p};" for h, p in backends)
                s.files[f"{sname}/nginx.conf"] = _NGINX_LB.format(algo=directive, servers=servers)
                s.volumes.append(f"./{sname}/nginx.conf:/etc/nginx/nginx.conf:ro")

    @classmethod
    def _build_networks(cls, cfg, topo, name) -> dict:
        """VPCs + Subnets as real Docker networks (cloud-networking Phase 2).

        A VPC is an **internal** bridge with its CIDR that every member joins — the implicit
        VPC fabric: members reach each other by name, but it has no internet of its own. A
        **public** subnet additionally puts its members on a per-VPC **egress** bridge (real
        internet + host-published consoles); a **private** subnet's members stay on the
        internal VPC net only — no internet, not reachable from the host (what 'private'
        means). Membership + tier come from containment: device → Subnet(Tier) → VPC.
        Returns {device_id -> [docker networks to join]}; non-members default to ["gini"].
        """
        parent = {d.id: d.parent_id for d in topo.devices.values()}
        tkey = {d.id: d.type_key for d in topo.devices.values()}
        props = {d.id: getattr(d, "properties", {}) or {} for d in topo.devices.values()}

        def ancestors(did):
            """(vpc_id, subnet_id) — the nearest VPC and Subnet boxes above `did`."""
            vpc = sub = None
            seen, cur = set(), parent.get(did)
            while cur and cur not in seen:
                seen.add(cur)
                t = tkey.get(cur)
                if t == "cloud_subnet" and sub is None:
                    sub = cur
                if t == "vpc":
                    vpc = cur
                    break
                cur = parent.get(cur)
            return vpc, sub

        from ..domain.grouping import BOX_TYPES        # VPC/Subnet/Region are containers,
        member_vpc, member_sub = {}, {}                #   not workloads — never "members"
        for d in topo.devices.values():
            if d.type_key in BOX_TYPES:
                continue
            v, s = ancestors(d.id)
            if v is not None:
                member_vpc[d.id] = v
                member_sub[d.id] = s
        if not member_vpc:
            return {}

        def is_public(did) -> bool:
            s = member_sub.get(did)
            if s is None:
                return True            # in a VPC but not in a subnet -> default public (egress)
            return (props[s].get("Tier", "private") or "private").strip().lower() == "public"

        used: set[str] = set()

        def unique_cidr(want):
            want = (want or "").strip()
            if _valid_cidr(want) and want not in used:
                used.add(want)
                return want
            for i in range(10, 250):                              # 10.10.0.0/16 … (avoids wan)
                c = f"10.{i}.0.0/16"
                if c not in used:
                    used.add(c)
                    return c
            return "10.249.0.0/16"

        vpc_has_public = {member_vpc[did] for did in member_vpc if is_public(did)}
        net_of_vpc = {}        # vpc_id -> (shared_internal_name, egress_name | None)
        for vdid in dict.fromkeys(member_vpc.values()):           # stable, deduped
            v = topo.devices[vdid]
            slug = _svc(name[vdid])
            cfg.networks.append(NetworkSpec(
                name=slug, cidr=unique_cidr(props[vdid].get("CIDR")),
                label=v.name, region=props[vdid].get("Region", ""), internal=True))
            egress = None
            if vdid in vpc_has_public:
                egress = f"{slug}_egress"
                cfg.networks.append(NetworkSpec(
                    name=egress, cidr="", label=f"{v.name} (public egress)", internal=False))
            net_of_vpc[vdid] = (slug, egress)

        out = {}
        for did, vdid in member_vpc.items():
            shared, egress = net_of_vpc[vdid]
            nets = [shared]
            if egress and is_public(did):
                nets.append(egress)
            out[did] = nets
        return out

    @classmethod
    def _build_security_groups(cls, cfg, topo, role, name, props) -> None:
        """Security Groups → a stateful per-member firewall (the classic web→app→db least
        privilege). An SG wired to a workload/datastore makes it default-deny inbound (only
        stateful replies + the GINI agent allowed) and opens the ports its Ingress lists,
        from a CIDR or from the members of another SG. Realized as an iptables init sidecar
        that shares each member's network namespace (so stock images need no changes)."""
        sgs = [d for d in topo.devices.values() if d.type_key == "security_group"]
        if not sgs:
            return
        nbrs: dict[str, list] = {did: [] for did in topo.devices}
        for l in topo.links.values():
            nbrs[l.source_id].append(l.target_id)
            nbrs[l.target_id].append(l.source_id)

        def members_of(sg):
            return [nb for nb in nbrs[sg.id] if role.get(nb) in ("service", "compute")]

        sg_by_name: dict[str, list] = {}        # "from <sg>" -> that SG's member svc names
        for sg in sgs:
            ms = [_svc(name[m]) for m in members_of(sg)]
            sg_by_name[_svc(sg.name)] = ms
            sg_by_name[(sg.name or "").strip().lower()] = ms

        per_member: dict[str, list] = {}        # member did -> union of its SGs' rules
        for sg in sgs:
            rules = _parse_ingress(props.get(sg.id, {}).get("Ingress", ""), sg_by_name)
            for m in members_of(sg):
                per_member.setdefault(m, []).extend(rules)

        for did, rules in per_member.items():
            cfg.firewalls.append({"member": _svc(name[did]), "script": _sg_script(rules)})

    # event sources that can trigger a Function: element type_key -> client port. The
    # queue/topic/subject the runtime subscribes to is named after the function itself.
    _EVENT_PORTS = {"queue": 5672, "stream": 9092, "messaging": 4222}

    @classmethod
    def _build_faas(cls, cfg, topo, role, name, props) -> None:
        """Gather every Function into the shared faas runtime (one container). A Function
        node = a handler hosted by the platform, reachable at http://faas:8000/<name>.
        A Function wired to an event source (Queue/Stream/Pub-Sub) also gets a trigger so
        the runtime subscribes and invokes the handler on each message (event-driven FaaS)."""
        nbrs: dict[str, list] = {d: [] for d in topo.devices}
        for l in topo.links.values():
            nbrs[l.source_id].append(l.target_id)
            nbrs[l.target_id].append(l.source_id)
        funcs = []
        for did, r in role.items():
            if r != "function":
                continue
            p = props.get(did, {})
            triggers = []
            for nb in nbrs.get(did, []):
                tk = topo.devices[nb].type_key
                port = cls._EVENT_PORTS.get(tk)
                if port:
                    triggers.append({"type": tk, "host": _svc(name[nb]), "port": port})
            funcs.append({"name": _svc(name[did]),
                          "handler": (p.get("Handler") or "echo").strip().lower(),
                          "code": p.get("Code", ""),
                          "triggers": triggers})
        cfg.faas = funcs

    @classmethod
    def _wire_api_gateway(cls, cfg, topo, role, name, props) -> None:
        """An API Gateway (Traefik) routes a URL path to each connected Function: a request
        to /<fn> is forwarded to the faas runtime, which dispatches to that handler."""
        id_of = {n: i for i, n in name.items()}
        nbrs: dict[str, list] = {d: [] for d in topo.devices}
        for l in topo.links.values():
            nbrs[l.source_id].append(l.target_id)
            nbrs[l.target_id].append(l.source_id)
        for s in cfg.services:
            if s.type_key != "api_gateway":
                continue
            did = id_of.get(s.name)
            routes = [_svc(name[nb]) for nb in nbrs.get(did, [])
                      if role.get(nb) == "function"]
            if not routes:
                cfg.notes.append(f"{s.name}: connect a Function to it to route to one")
                continue
            sname = _svc(s.name)
            routers = "\n".join(
                f"    fn-{fn}:\n      rule: \"PathPrefix(`/{fn}`)\"\n"
                f"      service: faas\n      entryPoints: [web]" for fn in routes)
            dyn = ("http:\n  routers:\n" + routers +
                   "\n  services:\n    faas:\n      loadBalancer:\n        servers:\n"
                   "          - url: \"http://faas:8000\"\n")
            s.files[f"{sname}/dynamic.yml"] = dyn
            s.volumes.append(f"./{sname}/dynamic.yml:/etc/traefik/dynamic/dynamic.yml:ro")
            s.command = list(s.command) + ["--providers.file.directory=/etc/traefik/dynamic"]

    # how the cloud-fabric agent probes each service type: (port, creds-kind)
    _FABRIC_PROBE = {"cache": (6379, None), "queue": (15672, "rabbit"),
                     "database": (5432, "postgres"), "messaging": (8222, None),
                     "proxy": (8080, None), "load_balancer": (80, None)}
    # infra services the fabric should not monitor (it watches the *app* services)
    _FABRIC_SKIP = {"_cadvisor", "metrics", "dashboard", "tracing"}

    K3S_IMAGE = "rancher/k3s:v1.30.6-k3s1"

    @classmethod
    def _build_k8s(cls, cfg: RuntimeConfig, topo, role, name, props) -> None:
        """Turn each drawn K8s Cluster + its connected Pods/Autoscalers into a real k3s
        cluster spec with generated Deployment/Service/HPA manifests."""
        id_of = {n: i for i, n in name.items()}
        nbrs: dict[str, list] = {d: [] for d in topo.devices}
        for l in topo.links.values():
            nbrs[l.source_id].append(l.target_id)
            nbrs[l.target_id].append(l.source_id)

        for cdid, r in role.items():
            if r != "k8scluster":
                continue
            deployments = []
            for nb in nbrs.get(cdid, []):
                if role.get(nb) != "k8sworkload":          # a Pod (= a Deployment)
                    continue
                p = props.get(nb, {})
                dep = {"name": _svc(name[nb]),
                       "image": _norm_image(p.get("Image") or "nginxdemos/hello:latest"),
                       "replicas": _int(p.get("Replicas"), 2),
                       "port": _int(p.get("Port"), 80), "hpa": None}
                for nb2 in nbrs.get(nb, []):                # an Autoscaling Group on it -> HPA
                    if role.get(nb2) == "hpa":
                        ap = props.get(nb2, {})
                        dep["hpa"] = {"min": _int(ap.get("Min"), 1),
                                      "max": _int(ap.get("Max"), 5),
                                      "cpu": _int(ap.get("TargetCPU"), 60)}
                        break
                deployments.append(dep)
            manifests = "\n---\n".join(
                m for d in deployments for m in (
                    _k8s_deployment_yaml(d), _k8s_service_yaml(d),
                    *( [_k8s_hpa_yaml(d)] if d["hpa"] else [] )))
            cfg.k8s.append(K8sSpec(name=name[cdid], svc=_svc(name[cdid]),
                                   image=cls.K3S_IMAGE, deployments=deployments,
                                   manifests=manifests))

    @classmethod
    def _build_fabric(cls, cfg: RuntimeConfig) -> None:
        """List the cloud services the GINI Cloud Fabric agent should watch, with the
        per-type probe port + credentials pulled from the catalog config."""
        watched = []
        for s in cfg.services:
            if s.type_key in cls._FABRIC_SKIP:
                continue
            port, credkind = cls._FABRIC_PROBE.get(
                s.type_key, (s.ports[0]["container"] if s.ports else 80, None))
            if credkind == "postgres":
                creds = {"user": s.env.get("POSTGRES_USER", "gini"),
                         "password": s.env.get("POSTGRES_PASSWORD", "gini"),
                         "db": s.env.get("POSTGRES_DB", "postgres")}
            elif credkind == "rabbit":
                creds = {"user": "guest", "password": "guest"}
            else:
                creds = {}
            watched.append({"name": _svc(s.name), "type": s.type_key,
                            "host": _svc(s.name), "port": port, "creds": creds})
        if watched:
            cfg.fabric = FabricSpec(services=watched)

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

        # A Dashboards (Grafana) element with no Prometheus on the canvas: auto-add a
        # hidden Prometheus so Grafana always has a datasource + data to show (mirrors the
        # cAdvisor sidecar). Without this, Grafana loads but has nothing to graph.
        if dashboards and not metrics:
            from .cloud_catalog import service_for
            auto = ServiceSpec(
                name="Prometheus", type_key="metrics", image=service_for("metrics").image,
                summary="Auto-added Prometheus backing the dashboard (scrapes cAdvisor).",
                ports=[{"container": 9090, "host": host_port,
                        "label": "console", "web": True}])
            host_port += 1
            cfg.services.append(auto)
            metrics = [auto]
            cfg.notes.append("auto-added Prometheus + cAdvisor behind Grafana")

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
                # land students on the provisioned dashboard — set ONLY now that the file
                # exists (else Grafana errors "Failed to load home dashboard").
                graf.env["GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"] = \
                    "/var/lib/grafana/dashboards/container.json"
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

    # 0. GINI32 boards name real hardware. A missing or duplicated BoardID does not
    #    fail loudly at run time — the relay keys its table by that id, so a duplicate
    #    makes one board silently disappear. Say so on the canvas instead.
    boards = [d for d in topo.devices.values() if d.type_key == "gini32"]
    seen_ids: dict[str, str] = {}
    for d in boards:
        bid = str((d.properties or {}).get("BoardID", "")).strip()
        if not bid:
            issues.append({"level": "warn", "device": d.name,
                           "message": "No BoardID set — put the id from the board's "
                                      "label here (see `gini32 provision --id`), or no "
                                      "hardware will attach to this element."})
        elif bid in seen_ids:
            issues.append({"level": "warn", "device": d.name,
                           "message": f"BoardID {bid!r} is also used by "
                                      f"{seen_ids[bid]} — two elements cannot share one "
                                      f"physical board; one of them will never connect."})
        else:
            seen_ids[bid] = d.name
    # overlapping physical subnets => routers get two routes to one network
    import ipaddress as _ipa
    nets: list[tuple] = []
    for d in boards:
        raw = str((d.properties or {}).get("PhysicalSubnet", "")).strip()
        if not raw:
            continue                      # blank is fine: allocated automatically
        try:
            net = _ipa.ip_network(raw, strict=False)
        except ValueError:
            continue                      # the compiler already notes and replaces it
        for other, onet in nets:
            if net.overlaps(onet):
                issues.append({"level": "warn", "device": d.name,
                               "message": f"PhysicalSubnet {raw} overlaps {other}'s — "
                                          f"give each board its own, or leave both blank "
                                          f"to have them allocated."})
        nets.append((d.name, net))

    # 1. isolated devices (degree 0) — not part of any network. xv6 runs standalone (it
    #    has no networking), its peripherals are optional, and OS Zoo guests run in isolation
    #    (display-only, no fabric wiring in v1), so none of them are "islands".
    tkey = {d.id: d.type_key for d in topo.devices.values()}
    for did, r in role.items():
        if r == "group" or nbrs[did]:
            continue
        if r == "oszoo" or tkey[did] in ("xv6", "terminal", "storage_volume"):
            continue
        issues.append({"level": "warn", "device": name[did],
                       "message": f"{name[did]} isn't connected to anything."})

    # 1b. two machines wired directly to each other. Not illegal — it is a valid
    #     point-to-point segment and GINI will address it — but stations normally join a
    #     LAN through a switch, so flag it as a teaching hint (advisory, never a block).
    for l in topo.links.values():
        if getattr(l, "kind", "link") == "attach":
            continue                          # a Source/Sink rider, not a network cable
        if role.get(l.source_id) == "machine" and role.get(l.target_id) == "machine":
            issues.append({"level": "warn", "device": name.get(l.source_id),
                           "message": f"{name.get(l.source_id)} and "
                                      f"{name.get(l.target_id)} are wired directly — "
                                      f"machines usually join a LAN through a switch. A "
                                      f"direct link is a point-to-point segment (fine, but "
                                      f"unusual)."})

    # 2. machines with no gateway (no router on any of their subnets) — islands.
    #    A host on a switched/SDN L2 domain is fine without a router (it reaches its
    #    LAN at layer 2), so only warn for hosts NOT on any switch/OVS.
    cfg = RuntimeCompiler().compile(topo)
    id_of = {n: i for i, n in name.items()}
    for m in cfg.machines:
        if m.gw or getattr(m, "gateway", False):    # gateway egresses via its own uplink
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
             "gateway": m.gw if i == 0 else None, "peer": itf.ep.peer.device,
             "link_id": itf.link_id}
            for i, itf in enumerate(m.ifaces)]}
    for r in cfg.routers:
        out[r.name] = {"role": "router", "routes": list(getattr(r, "routes", []) or []),
                       "interfaces": [
            {"name": f"eth{i}", "ip": itf.ip, "mac": itf.mac, "subnet": subnet(itf.ip),
             "gateway": None, "peer": itf.ep.peer.device, "link_id": itf.link_id}
            for i, itf in enumerate(r.ifaces)]}
    for s in cfg.switches:
        out[s.name] = {"role": "switch", "ports": len(s.eps), "interfaces": [],
                       "peers": [e.peer.device for e in s.eps]}
    return out


def overlay_hosts(addressing: dict) -> dict:
    """device name -> its primary overlay (gini0) IP, from `address_map` output. GINI writes these
    into each machine's /etc/hosts so names resolve over the DRAWN network (gini0) instead of the
    Docker bridge — which is what makes DNS/getent/ping/reach ride the overlay."""
    out: dict[str, str] = {}
    for name, info in (addressing or {}).items():
        for itf in info.get("interfaces", []):
            ip = str(itf.get("ip", "")).split("/")[0].strip()
            if ip:
                out[name] = ip
                break
    return out
