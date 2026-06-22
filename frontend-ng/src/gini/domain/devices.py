"""GINI device / element taxonomy.

A single registry describes every kind of element a user can place on the canvas,
spanning classic computer-networking devices and the new cloud-computing primitives.
This is pure data with no Qt dependency so it can be driven by the UI, the compiler,
the persistence layer, and the AI agent layer alike.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    NETWORKING = "Networking"
    SDN = "Software-Defined Networking"
    COMPUTE = "Compute"
    CONTAINERS = "Containers & Kubernetes"
    CLOUD_NETWORK = "Cloud Networking"
    STORAGE = "Storage & Data"
    STREAMING = "Streaming & Messaging"
    OBSERVABILITY = "Observability"
    WORKLOAD = "Workload & Testing"
    SERVERLESS = "Serverless"
    EXTERNAL = "External"


# Color-category keys; the theme maps each to a concrete accent color per theme.
class Accent(str, Enum):
    BLUE = "blue"        # routing / L3
    GREEN = "green"      # switching / L2
    PURPLE = "purple"    # compute / hosts
    TEAL = "teal"        # sdn
    CYAN = "cyan"        # containers
    INDIGO = "indigo"    # cloud networking
    AMBER = "amber"      # storage
    PINK = "pink"        # serverless
    SLATE = "slate"      # external / generic
    ORANGE = "orange"    # observability / monitoring
    RED = "red"          # workload / testing


@dataclass(frozen=True)
class DeviceType:
    key: str
    label: str
    category: Category
    icon: str
    accent: Accent
    description: str
    backend_kind: str | None = None         # compiler element, e.g. "vr", "vs", "vm"
    is_container: bool = False               # can visually/logically contain children
    default_properties: dict[str, str] = field(default_factory=dict)
    # properties that should render as a dropdown in the inspector: name -> choices
    property_choices: dict[str, tuple[str, ...]] = field(default_factory=dict)
    max_links: int | None = None             # None = unlimited

    @property
    def cloud(self) -> bool:
        return self.category in (
            Category.CONTAINERS,
            Category.CLOUD_NETWORK,
            Category.STORAGE,
            Category.SERVERLESS,
        )


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

_DEVICES: list[DeviceType] = [
    # ---- Classic networking -------------------------------------------------
    DeviceType(
        "router", "Router", Category.NETWORKING, "router", Accent.BLUE,
        "Virtual router (Layer-3 forwarding between subnets).",
        backend_kind="vr",
        default_properties={"Name": "", "Forwarding": "true"},
    ),
    DeviceType(
        "switch", "Switch", Category.NETWORKING, "switch", Accent.GREEN,
        "Layer-2 learning switch.",
        backend_kind="vs",
        default_properties={"Name": "", "Priority": "100", "MAC": ""},
    ),
    DeviceType(
        "hub", "Hub", Category.NETWORKING, "hub", Accent.GREEN,
        "Layer-1 repeater hub (broadcasts to all ports).",
        backend_kind="vs",
        default_properties={"Name": ""},
    ),
    DeviceType(
        "host", "Machine", Category.COMPUTE, "host", Accent.PURPLE,
        "Virtual host / end machine (UML or container backed).",
        backend_kind="vm",
        default_properties={"Name": "", "OS": "linux", "Interfaces": "1"},
    ),
    DeviceType(
        "firewall", "Firewall", Category.NETWORKING, "firewall", Accent.BLUE,
        "Packet-filtering firewall node.",
        backend_kind="vr",
        default_properties={"Name": "", "Policy": "default-deny"},
    ),
    DeviceType(
        "wap", "Access Point", Category.NETWORKING, "wifi", Accent.GREEN,
        "Wireless access point / mobile gateway.",
        default_properties={"Name": "", "SSID": "gini"},
    ),
    DeviceType(
        "cloud", "Internet", Category.EXTERNAL, "cloud", Accent.SLATE,
        "External network / the Internet.",
        default_properties={"Name": "Internet"},
    ),

    # ---- Software-defined networking ----------------------------------------
    DeviceType(
        "ovs", "OpenVSwitch", Category.SDN, "ovs", Accent.TEAL,
        "Open vSwitch programmable bridge.",
        backend_kind="vs",
        default_properties={"Name": "", "Datapath": "", "Protocol": "OpenFlow13"},
    ),
    DeviceType(
        "controller", "OpenFlow Controller", Category.SDN, "controller", Accent.TEAL,
        "SDN controller managing OpenFlow switches.",
        default_properties={"Name": "", "Port": "6633",
                            "App": "gini.samples.switch"},
        # the POX app that gives the switch its personality. The gini.samples.*
        # apps are GINI's own (they clear the Flow Switch's match-all -> NORMAL
        # default first, so the controller actually sees packet-ins); the
        # forwarding.* / misc.* apps are stock POX. Runs in the controller container.
        property_choices={"App": ("gini.samples.switch", "gini.samples.packet_loss",
                                  "gini.samples.port_knock", "gini.samples.l4_lb",
                                  "gini.samples.ids", "gini.samples.redirect",
                                  "forwarding.l2_learning", "forwarding.hub",
                                  "misc.of_tutorial")},
    ),

    # ---- Containers & Kubernetes --------------------------------------------
    DeviceType(
        "container", "Container", Category.CONTAINERS, "container", Accent.CYAN,
        "A single Docker/OCI container.",
        backend_kind="vm",
        default_properties={"Name": "", "Image": "alpine:latest", "Command": ""},
    ),
    DeviceType(
        "pod", "Pod", Category.CONTAINERS, "pod", Accent.CYAN,
        "Kubernetes pod (one or more co-located containers).",
        is_container=True,
        default_properties={"Name": "", "Replicas": "1"},
    ),
    DeviceType(
        "k8s_node", "K8s Node", Category.CONTAINERS, "k8s_node", Accent.CYAN,
        "Kubernetes worker node that schedules pods.",
        default_properties={"Name": "", "Role": "worker"},
    ),
    DeviceType(
        "k8s_cluster", "K8s Cluster", Category.CONTAINERS, "k8s_cluster", Accent.CYAN,
        "Kubernetes control plane + node group.",
        is_container=True,
        default_properties={"Name": "", "Version": "1.30", "Nodes": "3"},
    ),
    DeviceType(
        "registry", "Container Registry", Category.CONTAINERS, "registry", Accent.CYAN,
        "Image registry serving container images.",
        default_properties={"Name": "", "Endpoint": ""},
    ),

    # ---- Cloud networking (VPC / SG / LB) -----------------------------------
    DeviceType(
        "vpc", "VPC", Category.CLOUD_NETWORK, "vpc", Accent.INDIGO,
        "Virtual private cloud — an isolated cloud network.",
        is_container=True,
        default_properties={"Name": "", "CIDR": "10.0.0.0/16", "Region": "us-east-1"},
    ),
    DeviceType(
        "cloud_subnet", "Cloud Subnet", Category.CLOUD_NETWORK, "cloud_subnet", Accent.INDIGO,
        "A subnet within a VPC (public or private).",
        is_container=True,
        default_properties={"Name": "", "CIDR": "10.0.1.0/24", "Tier": "private"},
    ),
    DeviceType(
        "security_group", "Security Group", Category.CLOUD_NETWORK, "security_group", Accent.INDIGO,
        "Stateful virtual firewall for cloud instances.",
        default_properties={"Name": "", "Ingress": "", "Egress": "allow-all"},
    ),
    DeviceType(
        "gateway", "Gateway", Category.CLOUD_NETWORK, "gateway", Accent.INDIGO,
        "Internet / NAT gateway connecting a VPC to the outside.",
        default_properties={"Name": "", "Type": "internet"},
    ),
    DeviceType(
        "load_balancer", "Load Balancer", Category.CLOUD_NETWORK, "load_balancer", Accent.INDIGO,
        "Distributes traffic across backend targets.",
        default_properties={"Name": "", "Scheme": "round-robin", "Listener": "80"},
    ),

    # ---- Compute & autoscaling ----------------------------------------------
    DeviceType(
        "instance", "Instance", Category.COMPUTE, "instance", Accent.PURPLE,
        "Cloud compute instance (VM).",
        backend_kind="vm",
        default_properties={"Name": "", "Type": "t3.micro", "Image": "ubuntu-22.04"},
    ),
    DeviceType(
        "instance_group", "Autoscaling Group", Category.COMPUTE, "instance_group", Accent.PURPLE,
        "Managed group of instances with autoscaling policies.",
        is_container=True,
        default_properties={"Name": "", "Min": "1", "Max": "5", "Metric": "cpu>70%"},
    ),
    DeviceType(
        "region", "Region / Zone", Category.COMPUTE, "region", Accent.PURPLE,
        "A cloud region or availability zone boundary.",
        is_container=True,
        default_properties={"Name": "us-east-1", "Zones": "a,b,c"},
    ),

    # ---- Storage & data ------------------------------------------------------
    DeviceType(
        "object_store", "Object Storage", Category.STORAGE, "object_store", Accent.AMBER,
        "Object storage bucket (S3-style).",
        default_properties={"Name": "", "Versioning": "off"},
    ),
    DeviceType(
        "block_volume", "Block Volume", Category.STORAGE, "block_volume", Accent.AMBER,
        "Attachable block storage volume.",
        default_properties={"Name": "", "SizeGB": "20"},
    ),
    DeviceType(
        "database", "Managed Database", Category.STORAGE, "database", Accent.AMBER,
        "Managed relational / NoSQL database.",
        default_properties={"Name": "", "Engine": "postgres", "Replicas": "0"},
    ),

    # ---- Serverless ----------------------------------------------------------
    DeviceType(
        "function", "Function", Category.SERVERLESS, "function", Accent.PINK,
        "Serverless function (FaaS), event-driven.",
        default_properties={"Name": "", "Runtime": "python3.12", "Trigger": "http"},
    ),
    DeviceType(
        "api_gateway", "API Gateway", Category.SERVERLESS, "api_gateway", Accent.PINK,
        "Managed API gateway fronting functions/services.",
        default_properties={"Name": "", "Stage": "prod"},
    ),
    DeviceType(
        "queue", "Message Queue", Category.SERVERLESS, "queue", Accent.PINK,
        "Managed message queue / event bus.",
        default_properties={"Name": "", "Type": "fifo"},
    ),

    # ---- Edge & traffic (proxies / web) -------------------------------------
    DeviceType(
        "proxy", "Reverse Proxy", Category.CLOUD_NETWORK, "proxy", Accent.INDIGO,
        "Traefik reverse proxy / edge router with a live dashboard.",
        default_properties={"Name": "", "Dashboard": "on"},
    ),
    DeviceType(
        "web_app", "Web App", Category.COMPUTE, "web_app", Accent.PURPLE,
        "A small demo web backend that reports which instance served the request.",
        default_properties={"Name": ""},
    ),

    # ---- Streaming & messaging ----------------------------------------------
    DeviceType(
        "stream", "Event Stream", Category.STREAMING, "stream", Accent.CYAN,
        "Kafka-compatible event streaming log (Redpanda).",
        default_properties={"Name": "", "Partitions": "1"},
    ),
    DeviceType(
        "messaging", "Pub/Sub", Category.STREAMING, "messaging", Accent.CYAN,
        "Lightweight NATS publish/subscribe messaging server.",
        default_properties={"Name": ""},
    ),

    # ---- Storage: cache & NoSQL ---------------------------------------------
    DeviceType(
        "cache", "Cache", Category.STORAGE, "cache", Accent.AMBER,
        "Redis in-memory key/value cache and store.",
        default_properties={"Name": ""},
    ),
    DeviceType(
        "nosql", "NoSQL Database", Category.STORAGE, "nosql", Accent.AMBER,
        "MongoDB document database.",
        default_properties={"Name": "", "Database": "app"},
    ),

    # ---- Observability ------------------------------------------------------
    DeviceType(
        "metrics", "Metrics", Category.OBSERVABILITY, "metrics", Accent.ORANGE,
        "Prometheus metrics collection + PromQL query UI.",
        default_properties={"Name": ""},
    ),
    DeviceType(
        "dashboard", "Dashboards", Category.OBSERVABILITY, "dashboard", Accent.ORANGE,
        "Grafana dashboards over metrics and logs.",
        default_properties={"Name": ""},
    ),
    DeviceType(
        "tracing", "Tracing", Category.OBSERVABILITY, "tracing", Accent.ORANGE,
        "Jaeger distributed tracing (request timelines across services).",
        default_properties={"Name": ""},
    ),

    # ---- Workload & testing -------------------------------------------------
    DeviceType(
        "load_generator", "Load Generator", Category.WORKLOAD, "load_generator", Accent.RED,
        "Fortio HTTP/gRPC load generator with a web UI to launch experiments.",
        default_properties={"Name": "", "QPS": "100", "Connections": "8"},
    ),
]

# Lookup table
REGISTRY: dict[str, DeviceType] = {d.key: d for d in _DEVICES}

# Auto-name prefixes per element type (R1, S1, M1, …). Curated to be short and
# collision-free (e.g. Metrics is PROM, not M, so it never clashes with Machine).
# Users can override the popular ones in Settings → Naming.
DEFAULT_PREFIXES: dict[str, str] = {
    # networking
    "router": "R", "switch": "S", "hub": "H", "host": "M", "firewall": "FW",
    "wap": "AP", "cloud": "NET",
    # sdn
    "ovs": "OVS", "controller": "OFC",
    # compute / containers
    "instance": "I", "container": "CT", "web_app": "WA", "pod": "POD",
    "k8s_node": "KN", "k8s_cluster": "K8S", "registry": "REG",
    "instance_group": "ASG", "region": "RGN",
    # cloud networking
    "vpc": "VPC", "cloud_subnet": "CSUB", "security_group": "SG",
    "gateway": "GW", "load_balancer": "LB", "proxy": "PXY",
    # storage & data
    "object_store": "OBJ", "block_volume": "VOL", "database": "DB",
    "cache": "CA", "nosql": "NDB",
    # streaming & messaging
    "stream": "STR", "messaging": "MSG", "queue": "Q",
    # observability
    "metrics": "PROM", "dashboard": "GRAF", "tracing": "JGR",
    # workload / serverless
    "load_generator": "LG", "function": "FN", "api_gateway": "AGW",
}


def default_prefix(type_key: str) -> str:
    dt = REGISTRY.get(type_key)
    if type_key in DEFAULT_PREFIXES:
        return DEFAULT_PREFIXES[type_key]
    if dt is not None:                       # fallback: capitals of the label, e.g. VPC
        return "".join(c for c in dt.label if c.isupper()) or dt.label[:2].upper()
    return "N"


def all_devices() -> list[DeviceType]:
    return list(_DEVICES)


def get(key: str) -> DeviceType:
    return REGISTRY[key]


def by_category() -> dict[Category, list[DeviceType]]:
    out: dict[Category, list[DeviceType]] = {c: [] for c in Category}
    for d in _DEVICES:
        out[d.category].append(d)
    return {c: items for c, items in out.items() if items}
