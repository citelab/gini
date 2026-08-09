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
    """Palette sections, in the order they appear.

    MACHINES exists because the single most valuable comparison GINI can make was invisible: the
    things that RUN YOUR CODE were scattered across three sections (Machine and Instance under
    "Compute", Container under "Containers & Kubernetes", xv6 under "xv6"), so a student hunting
    for "something to run this on" had to look in three places — and never saw that they were
    looking at one family. They are: a container, a container with your image, a cloud VM, a real
    microVM with its own kernel (Kata), and a real kernel on QEMU (xv6). Listed lightest-to-heaviest
    (see PALETTE_RANK), the section itself teaches the isolation/weight tradeoff before the student
    reads a word — and the startup-time stamp and cost meter then make it measurable.
    """
    NETWORKING = "Networking"
    SDN = "Software-Defined Networking"
    MACHINES = "Machines"
    XV6 = "xv6 Peripherals"           # attach only to an xv6 Machine (no networking)
    CONTAINERS = "Containers & Kubernetes"
    CLOUD_NETWORK = "Cloud Networking"
    STORAGE = "Storage & Data"
    STREAMING = "Streaming & Messaging"
    OBSERVABILITY = "Observability"
    WORKLOAD = "Workload & Testing"
    SOURCE = "Sources"                # stimulus riders — inject inputs into a donor element
    SINK = "Sinks"                    # observer riders — read outputs off a donor element
    SERVERLESS = "Serverless"
    EXTERNAL = "External"
    OS_ZOO = "OS Zoo"                 # play with real historical OSes (emulated, embedded noVNC)


# Within the Machines section, order is the ISOLATION LADDER — lightest first. This is the lesson:
# they all run your code, and they differ in how much of a machine they actually are.
PALETTE_RANK: dict[str, int] = {
    "host": 1,          # a Linux container on the fabric
    "container": 2,     # …the same, but you supply the image
    "instance": 3,      # a cloud VM (as the cloud presents it)
    "kinstance": 4,     # a REAL microVM — its own kernel (Kata)
    "xv6": 5,           # a real teaching kernel on QEMU-RISC-V
}


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
    # Properties the element REPORTS rather than accepts: shown in the inspector but
    # not editable, because the value is observed from the real world (e.g. a GINI32
    # board's radio channel, which APSTA forces to match the uplink).
    readonly_properties: tuple[str, ...] = ()
    max_links: int | None = None             # None = unlimited
    hidden: bool = False                     # kept in the registry but off the palette
    # Is this a CLOUD element (a managed service you rent) rather than a networking primitive you
    # build? Stated explicitly, not inferred from the palette category — otherwise reorganising a UI
    # section silently changes what the AI is told about an element, which is how a Message Queue
    # ends up "not a cloud thing" because someone moved it next to Pub/Sub.
    is_cloud: bool | None = None             # None = fall back to the category default
    # --- rider elements (Sources / Sinks) ------------------------------------ #
    # A rider has NO container of its own: it runs as a process INSIDE a donor element (a Machine,
    # Router, OVS…) via the donor's runtime. On the canvas it hangs off its donor by a dotted
    # *attach* edge, not a network cable. `role` is stated, not inferred from the palette section.
    rider: bool = False                      # True = runs on a donor, spawns no container
    role: str = ""                           # "source" (injects input) | "sink" (reads output)
    attaches_to: tuple[str, ...] = ()        # donor type_keys this rider may ride
    driver: str = ""                         # how it runs: "docker-exec" | "grouter-cli" | "qemu-serial"

    @property
    def cloud(self) -> bool:
        if self.is_cloud is not None:
            return self.is_cloud
        return self.category in (
            Category.CONTAINERS,
            Category.CLOUD_NETWORK,
            Category.STORAGE,
            Category.STREAMING,
            Category.OBSERVABILITY,
            Category.WORKLOAD,
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
        "host", "Machine", Category.MACHINES, "host", Accent.PURPLE,
        "Virtual host / end machine — a Linux container on the fabric.",
        backend_kind="vm",
        # Toolkit = which image this host is built from. LEAN (Alpine) is the default and the one
        # to prefer: it has everything a student actually types (ip, ping, traceroute, tcpdump,
        # dig, curl, nc, socat, iperf3, nmap) and is ~10x smaller, so topologies build and boot
        # far faster on a modest laptop. Switch to FULL only for the experiments that need the
        # heavy servers — bind9 (DNS), postfix (mail), ettercap/dsniff (spoofing), haproxy.
        # NOTE: unrelated to the element's SIZE tier, which sets CPU and cost, not contents.
        default_properties={"Name": "", "OS": "linux", "Interfaces": "1", "Toolkit": "lean"},
        property_choices={"Toolkit": ("lean", "full", "security")},
        is_cloud=False,   # stated, not inferred from the palette section
    ),
    DeviceType(
        "xv6", "xv6 Machine", Category.MACHINES, "host", Accent.RED,
        "A real teaching kernel: xv6 (MIT 6.1810) running on QEMU-RISC-V. Not a container — a "
        "genuine OS you can watch and steer. Double-click it to open the Machine Lab: observe the "
        "scheduler, process table, CPU registers, memory and kernel stack live, and slow the "
        "time-slice to watch context switches. Runs standalone; xv6 has no networking, so instead "
        "of network links you attach peripherals — a Terminal and a Storage Volume.",
        backend_kind="xv6",
        default_properties={"Name": "", "Timeslice": "1", "CPUs": "1"},
        property_choices={"Timeslice": ("1", "5", "10", "100")},
        is_cloud=False,   # stated, not inferred from the palette section
    ),
    # --- xv6 peripherals (software devices attached to the xv6 Machine) -------
    DeviceType(
        "terminal", "Terminal", Category.XV6, "dashboard", Accent.RED,
        "A console for an xv6 Machine — one shell terminal (a screen and keyboard in one, like a "
        "real tty). Connect it to an xv6 Machine and double-click to open it: type xv6 commands "
        "(ls, cat, echo, spin 10 &, …) and watch their output inline. Up-arrow recalls history; "
        "`help` lists what you can run.",
        default_properties={"Name": ""},
        max_links=1,
    ),
    DeviceType(
        "storage_volume", "Storage Volume", Category.XV6, "database", Accent.RED,
        "The xv6 disk. Connect it to an xv6 Machine and double-click to open the Storage view — "
        "the on-disk layout, inodes, buffer cache and write-ahead log. (xv6 has a single custom "
        "file system; alternate file systems are an advanced student project.)",
        default_properties={"Name": "", "File system": "xv6fs"},
        property_choices={"File system": ("xv6fs",)},
        max_links=1,
    ),
    DeviceType(
        "firewall", "Firewall", Category.NETWORKING, "firewall", Accent.BLUE,
        "Packet-filtering firewall node.",
        backend_kind="vr",
        default_properties={"Name": "", "Policy": "default-deny"},
    ),
    DeviceType(
        "vnf", "VNF (Service Function)", Category.NETWORKING, "controller", Accent.TEAL,
        "A Virtualized Network Function: a container that runs a network function (firewall, "
        "IDS, cache, shaper) and is inserted INLINE in the forwarding path — wire it between "
        "two elements and traffic flows through it. Pick the function in 'Kind'; give its "
        "config in 'Rules' (e.g. firewall: 'deny 10.0.3.0/24'; block: '10.0.3.5'). Chain "
        "several in series (host → firewall → IDS → NAT) for a Service Function Chain (SFC).",
        default_properties={"Name": "", "Kind": "firewall", "Rules": "deny 10.0.3.0/24"},
        property_choices={"Kind": ("firewall", "block", "ids", "cache", "shaper")},
    ),
    DeviceType(
        "wap", "Access Point", Category.NETWORKING, "wifi", Accent.GREEN,
        "Wireless access point / mobile gateway.",
        default_properties={"Name": "", "SSID": "gini"},
    ),
    DeviceType(
        "cloud", "Internet", Category.EXTERNAL, "cloud", Accent.SLATE,
        "External network / the Internet.",
        # DNS lives HERE rather than on the things that use it, because name resolution
        # is a property of "the outside world is reachable" — which is precisely what
        # this element represents. Remove the element and DNS goes with it, which is the
        # honest behaviour: there is nothing left to resolve names against.
        # Handed to real devices on a GINI32 board's hotspot via DHCP; editable because
        # some campus networks block public resolvers.
        default_properties={"Name": "Internet", "DNS": "8.8.8.8"},
    ),
    # A real ESP32 board (GINI32) running the gBridge firmware: the one element on
    # the palette that is not emulated at all. It stands for a physical radio on
    # your desk that carries real devices — a phone, a Raspberry Pi, a sensor —
    # into the drawn topology. The board finds the lab through the `gbridge` relay
    # and is handed its fabric address from here, so the canvas stays the source of
    # truth. BoardID must match the id flashed into the board (serial: `set id`).
    DeviceType(
        "gini32", "GINI32 Board", Category.EXTERNAL, "gini32", Accent.GREEN,
        "A real ESP32 gateway board: carries physical devices into the emulated "
        "topology over Wi-Fi (Ethernet-in-UDP via the gbridge relay).",
        # BoardID names a PHYSICAL object, so it is never auto-generated: it is the
        # sticker on the board (`gini32 provision --id gini-5`). Empty by default so
        # two boards cannot silently collide on a shared default — an unset id is a
        # visible error rather than one board vanishing from the relay's table.
        # Everything else is per-RUN and assigned by the canvas: blank PhysicalSubnet
        # and ApSSID mean "allocate me one". Channel is REPORTED, not set — in APSTA
        # the hotspot is forced onto the uplink's channel.
        default_properties={"Name": "", "BoardID": "", "Mode": "routed",
                            "ApSSID": "", "ApPassword": "gini12345",
                            "PhysicalSubnet": "", "Channel": ""},
        property_choices={"Mode": ("routed", "nat")},
        readonly_properties=("Channel",),
        max_links=1,
        is_cloud=False,
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
        "container", "Container", Category.MACHINES, "container", Accent.CYAN,
        "A single Docker/OCI container.",
        backend_kind="vm",
        default_properties={"Name": "", "Image": "alpine:latest", "Command": ""},
        is_cloud=True,   # stated, not inferred from the palette section
    ),
    DeviceType(
        "pod", "Pod", Category.CONTAINERS, "pod", Accent.CYAN,
        "A Kubernetes workload — a Deployment of an image, run as N pod replicas. "
        "Connect it to a K8s Cluster to deploy it there.",
        is_container=True,
        default_properties={"Name": "", "Image": "nginxdemos/hello:latest",
                            "Replicas": "2", "Port": "80"},
    ),
    DeviceType(
        "k8s_node", "K8s Node", Category.CONTAINERS, "k8s_node", Accent.CYAN,
        "A Kubernetes worker node (a k3s agent). v1 clusters are single-node; nodes are "
        "shown for the model — multi-node scheduling is a follow-on.",
        default_properties={"Name": "", "Role": "worker"},
        # hidden from the palette for v1: a single-node cluster makes a separate Node
        # element confusing next to 'K8s Cluster'. The type is retained so older saved
        # projects still load and the compiler role keeps working. Re-expose with
        # multi-node scheduling.
        hidden=True,
    ),
    DeviceType(
        "k8s_cluster", "K8s Cluster", Category.CONTAINERS, "k8s_cluster", Accent.CYAN,
        "A real Kubernetes cluster (k3s in a container). Connect Pods to deploy them; add "
        "a Pod Autoscaler (HPA) on a Pod to scale its replicas.",
        is_container=True,
        default_properties={"Name": "", "Version": "1.30"},
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
        "A subnet inside a VPC. Drop elements in it. A *public* subnet's members reach the "
        "internet (and their consoles are reachable); a *private* subnet's members stay "
        "inside the VPC only — reachable by other VPC members, but with no internet.",
        is_container=True,
        default_properties={"Name": "", "CIDR": "10.0.1.0/24", "Tier": "private"},
        property_choices={"Tier": ("private", "public")},
    ),
    DeviceType(
        "security_group", "Security Group", Category.CLOUD_NETWORK, "security_group", Accent.INDIGO,
        "A stateful, default-deny firewall. Connect it to the workloads/datastores it "
        "protects, then list inbound rules in Ingress (one per line): '<port> from <source>', "
        "where source is a CIDR, 'anywhere', or another Security Group's name — e.g. "
        "'80 from anywhere' or '5432 from app-sg'. Only listed ports open; outbound is allowed.",
        default_properties={"Name": "", "Ingress": "80 from anywhere", "Egress": "allow-all"},
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
        property_choices={"Scheme": ("round-robin", "least_conn", "ip_hash")},
    ),

    # ---- Compute & autoscaling ----------------------------------------------
    DeviceType(
        "instance", "Instance", Category.MACHINES, "instance", Accent.PURPLE,
        "Cloud compute instance (VM).",
        backend_kind="vm",
        default_properties={"Name": "", "Type": "t3.micro", "Image": "ubuntu-22.04"},
        is_cloud=True,   # stated, not inferred from the palette section
    ),
    DeviceType(
        "kinstance", "Kata Instance (VM)", Category.MACHINES, "instance", Accent.PURPLE,
        "A VM-isolated workload (Kata Containers): your container runs inside a lightweight "
        "microVM with its own guest kernel — stronger isolation than a normal container, at "
        "the cost of boot time, memory and I/O overhead. Use it to compare VM-vs-container "
        "trade-offs. Needs a Kata-enabled GINI server backend (Settings - Backend).",
        backend_kind="vm",
        default_properties={"Name": "", "Image": "ubuntu:22.04", "Command": ""},
        is_cloud=True,   # stated, not inferred from the palette section
    ),
    DeviceType(
        "instance_group", "Pod Autoscaler (HPA)", Category.CONTAINERS, "instance_group", Accent.CYAN,
        "A Kubernetes Horizontal Pod Autoscaler. Connect it to a Pod to scale that "
        "Deployment's replicas between Min and Max to hold a target CPU%. (This is the "
        "HPA — different from the Cluster Autoscaler, which adds Nodes.)",
        is_container=True,
        default_properties={"Name": "", "Min": "1", "Max": "5", "TargetCPU": "60"},
    ),
    DeviceType(
        "region", "Region / Zone", Category.CLOUD_NETWORK, "region", Accent.PURPLE,
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
        "A serverless function (FaaS). Runs your handler on demand in a shared runtime — "
        "no server to manage, scales per request, billed per invocation. Reachable over "
        "HTTP at /<name>; front it with an API Gateway and drive it with a Load Generator.",
        default_properties={"Name": "", "Runtime": "python3.12", "Handler": "echo",
                            "Code": ""},
        property_choices={"Handler": ("echo", "transform", "slow", "fail", "counter",
                                      "custom")},
    ),
    DeviceType(
        "api_gateway", "API Gateway", Category.SERVERLESS, "api_gateway", Accent.PINK,
        "The front door for your functions — a real Traefik edge router that maps a URL "
        "path to each connected Function (/<name>). Connect it to Functions and it routes "
        "automatically; open its dashboard to watch requests.",
        default_properties={"Name": "", "Stage": "prod"},
    ),
    DeviceType(
        "queue", "Message Queue", Category.STREAMING, "queue", Accent.PINK,
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
        "web_app", "Web App", Category.CONTAINERS, "web_app", Accent.PURPLE,
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
    # Observability elements are SINKS (they observe outputs) — they live in the Sinks section now,
    # but unlike the rider sinks they are real services with their own container (rider=False).
    DeviceType(
        "metrics", "Metrics", Category.SINK, "metrics", Accent.ORANGE,
        "Prometheus metrics collection + PromQL query UI. A Sink: it scrapes and stores the numbers "
        "your services emit. Its own container (not a rider) — wire it to the targets it scrapes.",
        role="sink", is_cloud=True,
        default_properties={"Name": ""},
    ),
    DeviceType(
        "dashboard", "Dashboards", Category.SINK, "dashboard", Accent.ORANGE,
        "Grafana dashboards over metrics and logs. A Sink that visualizes what Metrics collected — "
        "wire it to a Metrics source. Its own container (not a rider).",
        role="sink", is_cloud=True,
        default_properties={"Name": ""},
    ),
    DeviceType(
        "tracing", "Tracing", Category.SINK, "tracing", Accent.ORANGE,
        "Jaeger distributed tracing (request timelines across services). A Sink: it observes request "
        "flow across services. Its own container (not a rider).",
        role="sink", is_cloud=True,
        default_properties={"Name": ""},
    ),

    # ---- Load generation (a heavyweight Source: its own container) ----------
    DeviceType(
        "load_generator", "Load Generator", Category.SOURCE, "load_generator", Accent.RED,
        "Fortio HTTP/gRPC load generator with a web UI to launch experiments. A Source that injects "
        "sustained traffic — heavier than the HTTP Probe rider, with its own container. Wire it to "
        "the backend/gateway you want to load.",
        role="source", is_cloud=True,
        default_properties={"Name": "", "QPS": "100", "Connections": "8"},
    ),

    # ---- Sources (stimulus riders — run ON a donor, no container of their own) ----
    DeviceType(
        "ping_probe", "Ping Probe", Category.SOURCE, "load_generator", Accent.RED,
        "A ping (ICMP) stimulus. Attach it to a Machine or Router — its donor — and give it a "
        "Target; double-click to start pinging INSIDE the donor (live RTT / loss in its Live tab), "
        "double-click again to stop. It rides the donor (dotted attach edge, not a cable). "
        "Count 0 = ping continuously until stopped; Count N = send N and stop.",
        rider=True, role="source", driver="docker-exec",
        attaches_to=("host", "router", "instance", "container"),
        default_properties={"Name": "", "Target": "", "Count": "0"},
    ),
    DeviceType(
        "http_probe", "HTTP Probe", Category.SOURCE, "load_generator", Accent.RED,
        "An HTTP request stimulus (curl). Attach it to a Machine / Router donor and give it a "
        "Target and Path; double-click to start requesting from inside the donor (live 2xx + "
        "latency), double-click again to stop. Count 0 = request continuously; Count N = do N. "
        "Rides the donor — no container of its own. For heavy sustained load use the Load Generator.",
        rider=True, role="source", driver="docker-exec",
        attaches_to=("host", "router", "instance", "container"),
        default_properties={"Name": "", "Target": "", "Path": "/", "Count": "0"},
    ),

    # ---- Sinks (observer riders — run ON a donor, render its output) ----
    DeviceType(
        "packet_view", "Packet View", Category.SINK, "tracing", Accent.ORANGE,
        "A live packet capture (tcpdump). Attach it to a Machine or Router — its donor; double-click "
        "to start sniffing the donor's interface (live packet stream in its Live tab), double-click "
        "again to stop. Count 0 = capture until stopped; Count N = stop after N packets. Captures "
        "the GINI overlay (gini0) by default — set Interface to eth0/any for the Docker bridge. "
        "Rides the donor — no container of its own.",
        rider=True, role="sink", driver="docker-exec",
        attaches_to=("host", "router", "ovs", "instance", "container"),
        default_properties={"Name": "", "Interface": "gini0", "Filter": "", "Count": "0"},
    ),
    DeviceType(
        "dns_probe", "DNS Probe", Category.SOURCE, "load_generator", Accent.RED,
        "A name-resolution stimulus. Attach it to a Machine/Router donor, put the hostname to "
        "resolve in Target (e.g. 'M2' or 'web'), and double-click to start; it resolves the name "
        "from inside the donor — over the DRAWN network (gini0), because GINI writes peer names into "
        "/etc/hosts — and reports the answer + resolve rate. Count 0 = query continuously; Count N = "
        "do N. Rides the donor.",
        rider=True, role="source", driver="docker-exec",
        attaches_to=("host", "router", "instance", "container"),
        default_properties={"Name": "", "Target": "", "Count": "0"},
    ),
    DeviceType(
        "traceroute_probe", "Traceroute", Category.SOURCE, "load_generator", Accent.RED,
        "A path-discovery stimulus (traceroute). Attach it to a Machine/Router donor and give it a "
        "Target; it traces the hops to the target from inside the donor and reports the hop count. "
        "Rides the donor — a good pair with a Packet View to watch each hop.",
        rider=True, role="source", driver="docker-exec",
        attaches_to=("host", "router", "instance", "container"),
        default_properties={"Name": "", "Target": ""},
    ),
    DeviceType(
        "iperf_client", "iPerf Client", Category.SOURCE, "load_generator", Accent.RED,
        "A throughput generator (iperf3 -c). Attach it to a Machine donor and point Target at a "
        "donor running an iPerf Server; it drives traffic and reports the measured bandwidth. "
        "Seconds sets the test length; Bitrate caps the rate (e.g. 100M) so it doesn't saturate the "
        "software fabric — set '0' for unlimited. Rides the donor.",
        rider=True, role="source", driver="docker-exec",
        attaches_to=("host", "instance", "container"),
        default_properties={"Name": "", "Target": "", "Seconds": "10", "Bitrate": "100M"},
    ),
    DeviceType(
        "iperf_server", "iPerf Server", Category.SINK, "tracing", Accent.ORANGE,
        "A throughput endpoint (iperf3 -s). Attach it to a Machine donor; it listens for an iPerf "
        "Client and reports the bandwidth it receives. Runs until stopped. Rides the donor.",
        rider=True, role="sink", driver="docker-exec",
        attaches_to=("host", "instance", "container"),
        default_properties={"Name": ""},
    ),
    DeviceType(
        "iface_stats", "Interface Stats", Category.SINK, "metrics", Accent.ORANGE,
        "A live interface counter (reads /proc/net/dev). Attach it to a Machine/Router donor; it "
        "streams rx/tx packet and byte counts so you can watch traffic volume. Rides the donor.",
        rider=True, role="sink", driver="docker-exec",
        attaches_to=("host", "router", "instance", "container"),
        default_properties={"Name": ""},
    ),

    # ---- xv6 Sources (OS course) — ride the xv6 Machine over its console -----
    DeviceType(
        "xv6_shell", "Shell Probe", Category.SOURCE, "load_generator", Accent.RED,
        "Launch a custom command into an xv6 Machine. Attach it to an xv6 Machine, put the command "
        "in Command (e.g. 'ls', 'echo hi', 'cat README'); it types the command into the kernel's "
        "console and streams the output back. The OS-course counterpart of the HTTP Probe.",
        rider=True, role="source", driver="qemu-serial",
        attaches_to=("xv6",),
        default_properties={"Name": "", "Command": ""},
    ),
    DeviceType(
        "xv6_workload", "Workload", Category.SOURCE, "load_generator", Accent.RED,
        "Spawn a program on an xv6 Machine to drive the scheduler (the OS-course load generator). "
        "Attach it to an xv6 Machine; set Program (e.g. 'spin', 'forktest', 'usertests') and Args. "
        "Background = run with '&' so several can compete. Watch the effect in the Machine Lab.",
        rider=True, role="source", driver="qemu-serial",
        attaches_to=("xv6",),
        default_properties={"Name": "", "Program": "spin", "Args": "", "Background": "true"},
        property_choices={"Background": ("true", "false")},
    ),

    # ---- OS Zoo — real historical OSes under emulation, embedded via noVNC ------
    DeviceType(
        "freedos", "FreeDOS", Category.OS_ZOO, "host", Accent.ORANGE,
        "The open, still-maintained MS-DOS-compatible OS — the command-line PC of the DOS era. "
        "Boots out of the box; double-click to use it in an embedded screen.",
        default_properties={"Name": "", "Persist": "false"},
        property_choices={"Persist": ("false", "true")},
    ),
    DeviceType(
        "kolibri", "KolibriOS", Category.OS_ZOO, "host", Accent.ORANGE,
        "A tiny GUI operating system written entirely in assembly — the whole thing boots from a "
        "single 1.44 MB floppy to a full graphical desktop in seconds, even under emulation. The "
        "fast one to reach for.",
        default_properties={"Name": "", "Persist": "false"},
        property_choices={"Persist": ("false", "true")},
    ),
    DeviceType(
        "menuet", "MenuetOS", Category.OS_ZOO, "host", Accent.ORANGE,
        "The assembly GUI OS that KolibriOS forked from — a complete graphical desktop with apps "
        "on a single 1.44 MB floppy (the 32-bit build is open source). Boots in seconds under "
        "emulation, just like KolibriOS.",
        default_properties={"Name": "", "Persist": "false"},
        property_choices={"Persist": ("false", "true")},
    ),
    DeviceType(
        "msdos", "MS-DOS 6.22", Category.OS_ZOO, "host", Accent.PURPLE,
        "The real Microsoft MS-DOS 6.22, booted from a disk image under QEMU (so it's the genuine "
        "article — `VER` reports MS-DOS, not a clone). Drag it on and Run — GINI downloads a public "
        "pre-installed MS-DOS 6.22 disk on first boot (GINI ships nothing proprietary) and boots to "
        "the C:\\> prompt. Put it next to FreeDOS to compare the original with the open re-creation. "
        "The Image URL is pre-filled and editable. Ephemeral unless Persist is on.",
        default_properties={
            "Name": "", "Emulator": "qemu", "Arch": "x86",
            "Image": "https://archive.org/download/pre-installed-ms-dos-622-disk-image/"
                     "Installed%20MS-DOS%206.22.img",
            "Persist": "false"},
        property_choices={"Persist": ("false", "true")},
    ),
    DeviceType(
        "mac7", "Mac System 7", Category.OS_ZOO, "host", Accent.PURPLE,
        "Classic Macintosh System 7 on an emulated 68k Mac (Basilisk II). Just drag it on and Run — "
        "GINI downloads a Quadra ROM and a bootable System 7.5.3 disk from a public archive on first "
        "boot (GINI ships nothing proprietary) and boots to the Mac desktop. The Image/Rom URLs are "
        "pre-filled; change them to point at files you prefer. Ephemeral unless Persist is on.",
        default_properties={
            "Name": "", "Emulator": "basilisk", "Arch": "68k",
            "Image": "https://archive.org/download/system-753/System753.dsk",
            "Rom": "https://archive.org/download/mac_rom_archive_-_as_of_8-19-2011/"
                   "mac_rom_archive_-_as_of_8-19-2011.zip/"
                   "F1ACAD13%20-%20Quadra%20610%2C650%2Cmaybe%20800.ROM",
            "Persist": "false"},
        property_choices={"Persist": ("false", "true")},
    ),
    DeviceType(
        "win31", "Windows 3.11", Category.OS_ZOO, "host", Accent.PURPLE,
        "Windows for Workgroups 3.11 under DOSBox — the fast vintage-Windows path. Just drag it on "
        "and Run — GINI downloads a public, pre-installed Windows 3.11, mounts it as C:, and starts "
        "Windows (GINI ships nothing proprietary). The Image URL is pre-filled; change it to point "
        "at a folder or zip you prefer. Ephemeral unless Persist is on.",
        default_properties={
            "Name": "", "Emulator": "dosbox", "Arch": "x86",
            "Image": "https://archive.org/download/win3_stock/win311-stock.zip",
            "Persist": "false"},
        property_choices={"Persist": ("false", "true")},
    ),
    DeviceType(
        "oszoo_byo", "Classic OS (your image)", Category.OS_ZOO, "host", Accent.SLATE,
        "Bring your own OS: run a proprietary classic OS that GINI can't legally ship. Pick the "
        "emulator — QEMU (Windows 95/98, or any bootable x86 disk), DOSBox (DOS and Windows 3.x, "
        "the fast path), or Basilisk II (classic 68k Mac — System 7 / Mac OS 8). Set 'Image' to a "
        "disk image (or folder for DOSBox) you own, and 'Rom' to a Macintosh ROM for Basilisk. "
        "GINI provides the emulator and hosts nothing — you supply the image.",
        default_properties={"Name": "", "Emulator": "qemu", "Image": "", "Rom": "", "Arch": "x86"},
        property_choices={"Emulator": ("qemu", "dosbox", "basilisk"),
                          "Arch": ("x86", "x86_64", "68k")},
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
    "wap": "AP", "cloud": "NET", "vnf": "VNF", "gini32": "GB",
    # sdn
    "ovs": "OVS", "controller": "OFC",
    # compute / containers
    "instance": "I", "container": "CT", "web_app": "WA", "pod": "POD",
    "k8s_node": "KN", "k8s_cluster": "K8S", "registry": "REG",
    "instance_group": "HPA", "region": "RGN",
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
    # sources / sinks (riders)
    "ping_probe": "PING", "http_probe": "HTTP", "packet_view": "PCAP",
    "dns_probe": "DNS", "traceroute_probe": "TRACE", "iperf_client": "IPERFC",
    "iperf_server": "IPERFS", "iface_stats": "IFSTAT",
    "xv6_shell": "SH", "xv6_workload": "WL",
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
    """The palette, section by section. Sections come out in Category order; within a section,
    PALETTE_RANK wins (that's what puts Machines in the isolation ladder — container first, real
    kernel last), and anything unranked keeps its registry order."""
    out: dict[Category, list[DeviceType]] = {c: [] for c in Category}
    for d in _DEVICES:
        if d.hidden:                         # retained in REGISTRY but kept off the palette
            continue
        out[d.category].append(d)
    for items in out.values():
        items.sort(key=lambda d: PALETTE_RANK.get(d.key, 99))    # stable: unranked keep their order
    return {c: items for c, items in out.items() if items}
