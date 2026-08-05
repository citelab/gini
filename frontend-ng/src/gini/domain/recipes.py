"""Recipes — curated, guaranteed-to-work blueprints the agent lays out on the canvas.

The safety model: the LLM only *selects and explains* recipes (matching the student's
intent against `intent` tags + the summary); the actual building is this deterministic
data + `GiniAPI.apply_recipe`. So even a small local model can't produce a broken topology
— it just picks from known-good blueprints. With no model at all the recipes are still
browsable by tag.

Each recipe is a set of typed elements (a local `ref` for linking, a grid position for
layout, an optional one-line `why`, and an optional `parent` ref for box containment —
VPC / Subnet / Region) plus grammar-valid links. The set covers every palette element
except the hidden `k8s_node` (see `covered_elements()` and the test).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecipeElement:
    ref: str                       # local key, used by `links` / `parent`
    type_key: str                  # palette element type
    props: dict = field(default_factory=dict)
    col: int = 0                   # layout grid column
    row: int = 0                   # layout grid row
    why: str = ""                  # one-line teaching reason (narration)
    parent: str = ""               # ref of the box (vpc/cloud_subnet/region) this sits in


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    summary: str                   # one line the LLM/UX shows
    intent: tuple[str, ...]        # keywords the LLM/offline matcher scores against
    teaches: str
    elements: tuple[RecipeElement, ...]
    links: tuple[tuple[str, str], ...] = ()
    concept: str = ""              # related concepts.Concept.key


def _e(ref, type_key, why="", *, col=0, row=0, props=None, parent=""):
    return RecipeElement(ref, type_key, props=props or {}, col=col, row=row,
                         why=why, parent=parent)


RECIPES: tuple[Recipe, ...] = (
    # ---- networking plane ------------------------------------------------- #
    Recipe(
        id="lan", name="A basic switched LAN",
        summary="Two machines on one subnet through a switch, with a router as their gateway.",
        intent=("lan", "switch", "router", "subnet", "network", "basic", "ethernet"),
        teaches="Layer-2 switching within a subnet and a Layer-3 router as the gateway",
        concept="networking-basics",
        elements=(
            _e("m1", "host", "An end machine on the LAN.", col=0, row=0),
            _e("m2", "host", "A second machine on the same subnet.", col=0, row=1),
            _e("s1", "switch", "Switch linking the machines in one subnet.", col=1, row=0),
            _e("r1", "router", "Gateway off the LAN to other subnets.", col=2, row=0),
        ),
        links=(("m1", "s1"), ("m2", "s1"), ("s1", "r1")),
    ),
    Recipe(
        id="wifi_lan", name="A Wi-Fi LAN",
        summary="A wireless client joining the wired LAN through an access point.",
        intent=("wifi", "wireless", "access point", "wap", "mobile", "802.11"),
        teaches="bridging a wireless segment into a wired LAN",
        concept="networking-basics",
        elements=(
            _e("m1", "host", "A wireless client.", col=0, row=0),
            _e("ap", "wap", "Access point bridging Wi-Fi into the wired LAN.", col=1, row=0),
            _e("s1", "switch", "Wired switch behind the AP.", col=2, row=0),
            _e("r1", "router", "Gateway off the LAN.", col=3, row=0),
        ),
        links=(("m1", "ap"), ("ap", "s1"), ("s1", "r1")),
    ),
    Recipe(
        id="hub_demo", name="Hub vs switch (collision domain)",
        summary="A Layer-1 hub shared segment (watch flooding) bridged into a switched LAN.",
        intent=("hub", "collision", "broadcast", "flooding", "repeater", "layer 1", "shared"),
        teaches="why a hub floods and a switch doesn't",
        concept="networking-basics",
        elements=(
            _e("m1", "host", "One machine sharing the medium.", col=0, row=0),
            _e("m2", "host", "Another machine in the same collision domain.", col=0, row=1),
            _e("h1", "hub", "Layer-1 hub — floods every frame to all ports.", col=1, row=0),
            _e("s1", "switch", "Switch the shared segment bridges into.", col=2, row=0),
        ),
        links=(("m1", "h1"), ("m2", "h1"), ("h1", "s1")),
    ),
    Recipe(
        id="internet_access", name="LAN to the Internet (NAT + firewall)",
        summary="A LAN reaching the Internet through a router, a firewall, and NAT.",
        intent=("internet", "nat", "egress", "firewall", "outbound", "gateway", "wan"),
        teaches="how egress flows through the drawn path and a firewall guards the edge",
        concept="internet-nat",
        elements=(
            _e("m1", "host", "A machine that needs the Internet.", col=0, row=0),
            _e("s1", "switch", "LAN switch.", col=1, row=0),
            _e("r1", "router", "Routes the LAN toward the edge.", col=2, row=0),
            _e("fw", "firewall", "Filters traffic at the trust boundary.", col=3, row=0),
            _e("net", "cloud", "The Internet / NAT gateway (faithful egress).", col=4, row=0),
        ),
        links=(("m1", "s1"), ("s1", "r1"), ("r1", "fw"), ("fw", "net")),
    ),
    Recipe(
        id="sdn", name="OpenFlow SDN",
        summary="Two hosts on an OpenFlow switch programmed by a controller.",
        intent=("sdn", "openflow", "controller", "flow", "software defined", "pox", "ovs"),
        teaches="the control/data-plane split and reactive flow installation",
        concept="sdn",
        elements=(
            _e("m1", "host", "End host on the SDN fabric.", col=0, row=0),
            _e("m2", "host", "Second host across the fabric.", col=0, row=1),
            _e("ovs", "ovs", "OpenFlow switch — no logic of its own.", col=1, row=0),
            _e("c1", "controller", "Programs the switch's flow rules reactively.", col=2, row=0),
        ),
        links=(("m1", "ovs"), ("m2", "ovs"), ("ovs", "c1")),
    ),
    Recipe(
        id="nfv_chain", name="Service function chain (firewall → NAT)",
        summary="A host whose traffic is steered through a firewall function and then a NAT "
                "gateway on the way out — network functions chained in the forwarding path.",
        intent=("nfv", "sfc", "service chain", "service function chain", "chaining", "chain",
                "firewall", "nat", "middlebox", "network function", "vnf", "steering"),
        teaches="inserting network functions (firewall, NAT) in series in the path (NFV/SFC)",
        concept="sfc",
        elements=(
            _e("m1", "host", "The client whose traffic is steered through the chain.",
               col=0, row=0),
            _e("r1", "router", "Routes the client toward the chain and the edge.",
               col=1, row=0),
            _e("fw", "firewall", "Function #1 — filters traffic in the path.", col=2, row=0),
            _e("net", "cloud", "Function #2 — NAT gateway to the Internet (the chain's egress).",
               col=3, row=0),
        ),
        links=(("m1", "r1"), ("r1", "fw"), ("fw", "net")),
    ),
    Recipe(
        id="sfc_container", name="Container VNF service chain",
        summary="A host whose traffic passes through a firewall VNF then an IDS VNF "
                "(containers inserted in the path) before routing out to the Internet.",
        intent=("vnf", "sfc", "service function chain", "service chain", "nfv", "chain",
                "firewall", "ids", "middlebox", "container vnf", "network function"),
        teaches="chaining container VNFs (firewall -> IDS) inline in the forwarding path",
        concept="sfc",
        elements=(
            _e("m1", "host", "The client whose traffic is steered through the chain.",
               col=0, row=0),
            _e("fw", "vnf", "VNF #1 — a firewall container filtering the traffic.",
               props={"Kind": "firewall", "Rules": "deny 10.0.9.0/24"}, col=1, row=0),
            _e("ids", "vnf", "VNF #2 — an IDS container inspecting the traffic.",
               props={"Kind": "ids"}, col=2, row=0),
            _e("r1", "router", "Routes the chain's egress toward the Internet.", col=3, row=0),
            _e("net", "cloud", "The Internet (the chain's egress).", col=4, row=0),
        ),
        links=(("m1", "fw"), ("fw", "ids"), ("ids", "r1"), ("r1", "net")),
    ),
    # ---- serverless & event-driven --------------------------------------- #
    Recipe(
        id="serverless", name="Serverless API (gateway + function + storage)",
        summary="An API gateway invoking a function that stores data and can fire on queue events.",
        intent=("serverless", "faas", "lambda", "function", "api gateway", "cloud function",
                "gateway"),
        teaches="stateless functions behind an API gateway, with storage and event triggers",
        concept="serverless",
        elements=(
            _e("gw", "api_gateway", "The front door — routes a URL path to the function.",
               col=0, row=0),
            _e("f1", "function", "Stateless code that runs per request and scales to zero.",
               col=1, row=0),
            _e("obj", "object_store", "Durable storage for the function's data/objects.",
               col=2, row=0),
            _e("q1", "queue", "Also triggers the function from messages (event-driven).",
               col=1, row=1),
        ),
        links=(("gw", "f1"), ("f1", "obj"), ("q1", "f1")),
    ),
    Recipe(
        id="message_queue", name="Message queue (producer -> consumer)",
        summary="A producer enqueuing work that a function consumes asynchronously.",
        intent=("message queue", "queue", "rabbitmq", "async", "producer", "consumer", "job",
                "decouple"),
        teaches="asynchronous decoupling — a work queue between a producer and a consumer",
        concept="messaging-queue",
        elements=(
            _e("w1", "web_app", "Producer — enqueues work without waiting.", col=0, row=0),
            _e("q1", "queue", "Buffers messages; each goes to one consumer.", col=1, row=0),
            _e("f1", "function", "Consumer — triggered to drain the queue.", col=2, row=0),
        ),
        links=(("w1", "q1"), ("q1", "f1")),
    ),
    Recipe(
        id="streaming", name="Event stream",
        summary="An app producing to an event stream that a function consumes.",
        intent=("stream", "streaming", "kafka", "event log", "event sourcing", "redpanda"),
        teaches="an ordered, replayable event log with independent consumers",
        concept="messaging-queue",
        elements=(
            _e("w1", "web_app", "Produces events onto the log.", col=0, row=0),
            _e("st", "stream", "Ordered, replayable event log.", col=1, row=0),
            _e("f1", "function", "Consumes the stream independently.", col=2, row=0),
        ),
        links=(("w1", "st"), ("st", "f1")),
    ),
    Recipe(
        id="pubsub", name="Publish / subscribe",
        summary="A publisher fanning messages out to a subscribing function.",
        intent=("pubsub", "pub/sub", "publish", "subscribe", "nats", "fan out", "messaging"),
        teaches="pub/sub fan-out to many subscribers",
        concept="messaging-queue",
        elements=(
            _e("w1", "web_app", "Publisher.", col=0, row=0),
            _e("msg", "messaging", "Fans each message out to all subscribers.", col=1, row=0),
            _e("f1", "function", "A subscriber, triggered on publish.", col=2, row=0),
        ),
        links=(("w1", "msg"), ("msg", "f1")),
    ),
    # ---- web apps, data, LB, proxy --------------------------------------- #
    Recipe(
        id="web_3tier", name="Load-balanced web app + data",
        summary="A load balancer fronting a web app backed by a database and a cache.",
        intent=("web app", "load balancer", "three tier", "3-tier", "database", "cache",
                "scale"),
        teaches="load balancing across an app tier backed by a database and a cache",
        concept="load-balancing",
        elements=(
            _e("lb", "load_balancer", "Spreads traffic across the app.", col=0, row=0),
            _e("w1", "web_app", "The application tier.", col=1, row=0),
            _e("db", "database", "Relational store for app state.", col=2, row=0),
            _e("ca", "cache", "In-memory cache in front of the database.", col=2, row=1),
        ),
        links=(("lb", "w1"), ("w1", "db"), ("w1", "ca")),
    ),
    Recipe(
        id="reverse_proxy", name="Reverse proxy (routing / TLS)",
        summary="A reverse proxy fronting a web service.",
        intent=("reverse proxy", "proxy", "traefik", "tls", "ingress", "routing"),
        teaches="path routing and TLS termination in front of a service",
        concept="load-balancing",
        elements=(
            _e("px", "proxy", "Reverse proxy for path routing and TLS.", col=0, row=0),
            _e("w1", "web_app", "The service behind the proxy.", col=1, row=0),
        ),
        links=(("px", "w1"),),
    ),
    Recipe(
        id="load_test", name="Load-test rig",
        summary="A load generator firing HTTP traffic at a web app while metrics are scraped.",
        intent=("load", "stress", "benchmark", "performance", "throughput", "latency",
                "test", "experiment", "traffic", "qps"),
        teaches="generating controlled load and reading QPS / latency results",
        concept="load-balancing",
        elements=(
            _e("gen", "load_generator", "Fires HTTP load at the target.", col=0, row=0),
            _e("app", "web_app", "The service under test.", col=1, row=0),
            _e("met", "metrics", "Scrapes throughput/latency while it runs.", col=1, row=1),
        ),
        links=(("gen", "app"), ("met", "app")),
    ),
    Recipe(
        id="observability", name="Live observability stack",
        summary="Metrics + dashboard + tracing watching a web app.",
        intent=("observe", "monitor", "visualize", "visualization", "metrics", "dashboard",
                "grafana", "prometheus", "tracing", "jaeger", "telemetry"),
        teaches="scrape metrics -> dashboard, distributed tracing, driven under load",
        concept="observability",
        elements=(
            _e("gen", "load_generator", "Drives traffic so there's something to observe.",
               col=0, row=0),
            _e("app", "web_app", "The service being observed.", col=0, row=1),
            _e("prom", "metrics", "Scrapes numeric metrics from the service.", col=1, row=1),
            _e("dash", "dashboard", "Visualises the metrics.", col=2, row=0),
            _e("tr", "tracing", "Collects distributed traces.", col=1, row=2),
        ),
        links=(("gen", "app"), ("prom", "app"), ("dash", "prom"), ("tr", "app")),
    ),
    Recipe(
        id="nosql_app", name="NoSQL-backed app",
        summary="A web app backed by a NoSQL document store.",
        intent=("nosql", "mongo", "document", "flexible schema", "no-sql"),
        teaches="when a document store fits better than a relational one",
        concept="datastores",
        elements=(
            _e("w1", "web_app", "App with a document data model.", col=0, row=0),
            _e("ndb", "nosql", "Document store (Mongo-style).", col=1, row=0),
        ),
        links=(("w1", "ndb"),),
    ),
    Recipe(
        id="container_app", name="Containerised service + DB",
        summary="A container backed by a database.",
        intent=("container", "docker", "microservice", "image"),
        teaches="running a single containerised process with its own store",
        concept="cloud-compute",
        elements=(
            _e("ct", "container", "A single containerised process.", col=0, row=0),
            _e("db", "database", "Its relational store.", col=1, row=0),
        ),
        links=(("ct", "db"),),
    ),
    Recipe(
        id="instance_disk", name="VM instance with a persistent disk",
        summary="An instance with a persistent block volume attached.",
        intent=("instance", "vm", "block volume", "disk", "ebs", "persistent", "virtual machine"),
        teaches="attaching durable block storage to a VM-style instance",
        concept="cloud-compute",
        elements=(
            _e("i1", "instance", "A VM-style workload.", col=0, row=0),
            _e("bv", "block_volume", "A persistent disk attached to it.", col=1, row=0),
        ),
        links=(("i1", "bv"),),
    ),
    # ---- Kubernetes ------------------------------------------------------- #
    Recipe(
        id="kubernetes", name="Kubernetes with autoscaling",
        summary="A cluster running an autoscaled Pod that pulls from a private registry.",
        intent=("kubernetes", "k8s", "pod", "cluster", "hpa", "autoscale", "registry",
                "replicas", "orchestration"),
        teaches="Pods in a cluster, a Pod Autoscaler (HPA), and an image registry",
        concept="kubernetes",
        elements=(
            _e("k1", "k8s_cluster", "The Kubernetes cluster (k3s).", col=0, row=0),
            _e("p1", "pod", "A Pod (Deployment) running your image.", col=1, row=0),
            _e("hpa", "instance_group", "Pod Autoscaler — scales replicas on CPU.", col=2, row=0),
            _e("reg", "registry", "Private image registry for the cluster.", col=0, row=1),
        ),
        links=(("p1", "k1"), ("p1", "hpa"), ("k1", "reg")),
    ),
    # ---- cloud networking ------------------------------------------------- #
    Recipe(
        id="vpc_public_private", name="VPC with public & private subnets",
        summary="A VPC where a public web tier reaches a private database the Internet can't.",
        intent=("vpc", "subnet", "public", "private", "isolation", "region", "cloud network",
                "segmentation"),
        teaches="VPC isolation and public vs private subnets",
        concept="vpc-networking",
        elements=(
            _e("us", "region", "A region label wrapping the VPC.", col=0, row=0),
            _e("vpc", "vpc", "An isolated network with its own CIDR.", col=0, row=0, parent="us"),
            _e("pub", "cloud_subnet", "Public subnet — its members get Internet.",
               props={"Tier": "public"}, col=0, row=1, parent="vpc"),
            _e("priv", "cloud_subnet", "Private subnet — no Internet, VPC-internal only.",
               props={"Tier": "private"}, col=1, row=1, parent="vpc"),
            _e("w1", "web_app", "Public web tier.", col=0, row=2, parent="pub"),
            _e("db", "database", "Private database — reachable by the web tier, not outside.",
               col=1, row=2, parent="priv"),
        ),
        links=(("w1", "db"),),
    ),
    Recipe(
        id="vpc_gateway", name="VPC Internet gateway",
        summary="A gateway giving a VPC outbound access to the Internet.",
        intent=("gateway", "internet gateway", "igw", "vpc egress", "outbound"),
        teaches="giving a private VPC controlled outbound Internet",
        concept="vpc-networking",
        elements=(
            _e("gw", "gateway", "Gives the VPC outbound Internet.", col=1, row=0),
            _e("vpc", "vpc", "The private network being connected.", col=0, row=0),
            _e("net", "cloud", "The public Internet.", col=2, row=0),
        ),
        links=(("gw", "vpc"), ("gw", "net")),
    ),
    Recipe(
        id="security_groups", name="Least-privilege security groups",
        summary="A web app open to the world and a database reachable only from that web app.",
        intent=("security group", "firewall", "least privilege", "ingress", "default deny",
                "port", "lock down", "segmentation"),
        teaches="default-deny security groups and referencing one group from another",
        concept="security-groups",
        elements=(
            _e("w1", "web_app", "Public web tier.", col=0, row=0),
            _e("db", "database", "Private database.", col=2, row=0),
            _e("wsg", "security_group", "Opens the web tier to the world on 80.",
               props={"Ingress": "80 from anywhere"}, col=0, row=1),
            _e("dsg", "security_group", "Opens the DB port ONLY to the web tier.",
               props={"Ingress": "5432 from web-sg"}, col=2, row=1),
        ),
        links=(("wsg", "w1"), ("dsg", "db"), ("w1", "db")),
    ),
    # ---- VM-vs-container experiment -------------------------------------- #
    Recipe(
        id="kata", name="VM isolation experiment (Kata)",
        summary="A Kata VM workload under load, backed by a DB, measured against container overhead.",
        intent=("kata", "microvm", "vm vs container", "isolation", "secure workload",
                "hypervisor"),
        teaches="the isolation-vs-startup trade-off of a VM-isolated workload",
        concept="kata-isolation",
        elements=(
            _e("kv", "kinstance", "A VM-isolated workload (Kata microVM).", col=1, row=0),
            _e("lg", "load_generator", "Drives load to measure it.", col=0, row=0),
            _e("db", "database", "Backs the workload.", col=2, row=0),
            _e("met", "metrics", "Scrapes its startup/throughput to compare.", col=1, row=1),
        ),
        links=(("lg", "kv"), ("kv", "db"), ("met", "kv")),
    ),
    # ---- OS course: a real kernel to watch ------------------------------- #
    Recipe(
        id="xv6_scheduler", name="Watch the CPU scheduler (xv6)",
        summary="A standalone xv6 kernel you open in the Machine Lab to watch context switches.",
        intent=("xv6", "os", "operating system", "kernel", "scheduler", "scheduling",
                "context switch", "process", "time slice", "preemption", "machine lab"),
        teaches="how a real kernel schedules processes — the process table, CPU registers and "
                "kernel stack changing on each context switch",
        concept="os-scheduling",
        elements=(
            _e("k", "xv6", "A real teaching kernel (xv6 on QEMU-RISC-V). Double-click it to "
               "open the Machine Lab, slow the time-slice, and watch the scheduler run.",
               col=0, row=0, props={"Timeslice": "1"}),
        ),
        links=(),
    ),
    Recipe(
        id="xv6_starvation", name="Provoke starvation (xv6)",
        summary="An xv6 kernel with a wide time-slice — spawn CPU-bound procs and watch one "
                "hog the CPU while others wait.",
        intent=("xv6", "starvation", "fairness", "unfair", "hog", "monopoly", "time slice",
                "quantum", "priority", "scheduling"),
        teaches="how a large time-slice (or an unfair policy) lets one process monopolize the "
                "CPU while others starve",
        concept="os-scheduling",
        elements=(
            _e("k", "xv6", "Open the Machine Lab, keep the wide time-slice, run a few `spin` "
               "processes, and watch the Gantt strip and the RUNNABLE queue.",
               col=0, row=0, props={"Timeslice": "100"}),
        ),
        links=(),
    ),
    Recipe(
        id="xv6_terminal", name="An xv6 machine with a terminal + disk",
        summary="An xv6 Machine wired to a Terminal (its shell console) and a Storage Volume — "
                "its software peripherals (xv6 has no networking).",
        intent=("xv6", "terminal", "shell", "console", "peripheral", "device", "tty",
                "storage volume", "disk", "io"),
        teaches="how an OS talks to devices — the console (a Terminal) and the disk are "
                "peripherals, reached through drivers",
        concept="os-processes",
        elements=(
            _e("k", "xv6", "The teaching kernel.", col=1, row=1),
            _e("term", "terminal", "Type commands and watch xv6's console.", col=2, row=0),
            _e("vol", "storage_volume", "The xv6 disk — open its file system.", col=1, row=2),
        ),
        links=(("k", "term"), ("k", "vol")),
    ),
    Recipe(
        id="xv6_syscall", name="Add your own system call (xv6)",
        summary="An xv6 kernel to extend — use the Syscall Builder to add a real system call.",
        intent=("xv6", "system call", "syscall", "add a syscall", "new syscall", "kernel",
                "user program", "trap", "sysproc"),
        teaches="how a system call is wired through xv6 — the number, the dispatch table, the "
                "kernel handler, and the user stub",
        concept="os-processes",
        elements=(
            _e("k", "xv6", "Open the Machine Lab, click Syscall Builder, declare a call and "
               "drop in a C body; GINI generates the five real xv6 edits.",
               col=0, row=0, props={"Timeslice": "1"}),
        ),
        links=(),
    ),
    Recipe(
        id="xv6_paging", name="Watch demand paging (xv6)",
        summary="An xv6 kernel to open in the Memory face — watch page tables and a fault grow "
                "the stack.",
        intent=("xv6", "memory", "virtual memory", "paging", "page table", "page fault",
                "demand paging", "lazy allocation", "satp", "address space"),
        teaches="how virtual memory maps VA→PA and how a page fault triggers demand allocation",
        concept="os-memory",
        elements=(
            _e("k", "xv6", "Open the Machine Lab → Memory: read the page table and the allocator, "
               "then Simulate a fault to watch the stack grow.", col=0, row=0),
        ),
        links=(),
    ),
    Recipe(
        id="xv6_journal", name="Watch a journal transaction (xv6)",
        summary="An xv6 kernel to open in the Storage face — watch the write-ahead log fill, "
                "commit, and install.",
        intent=("xv6", "file system", "filesystem", "journal", "journaling", "log", "crash",
                "transaction", "inode", "buffer cache", "commit"),
        teaches="how the write-ahead log makes file-system writes crash-safe (all-or-nothing)",
        concept="os-filesystem",
        elements=(
            _e("k", "xv6", "Open the Machine Lab → Storage: inspect the layout and inodes, then "
               "Simulate a write to watch a transaction commit.", col=0, row=0),
        ),
        links=(),
    ),
    Recipe(
        id="xv6_step_switch", name="Step one context switch (xv6)",
        summary="An xv6 kernel to single-step through a context switch and watch the registers, "
                "process table and kernel stack change.",
        intent=("xv6", "context switch", "swtch", "step", "single step", "trap", "registers",
                "kernel stack", "how does a context switch work"),
        teaches="what actually changes across a single context switch — the saved registers, "
                "the running process, and the kernel stack",
        concept="os-scheduling",
        elements=(
            _e("k", "xv6", "Open the Machine Lab and use Step-switch to advance one context "
               "switch at a time; read the four panels between steps.",
               col=0, row=0, props={"Timeslice": "1"}),
        ),
        links=(),
    ),
    # ---- Sources / Sinks (riders: instruments that run inside a donor) ---- #
    Recipe(
        id="ping_capture", name="Ping and capture it",
        summary="Ping one machine from another and watch the ICMP packets arrive on the receiver.",
        intent=("ping", "icmp", "capture", "tcpdump", "packet", "rtt", "latency", "reachability",
                "sniff", "packet view", "loss"),
        teaches="how to inject ICMP traffic from a Source and observe it arrive with a Sink capture",
        elements=(
            _e("m1", "host", "The sender.", col=0, row=0),
            _e("s1", "switch", "LAN switch.", col=1, row=0),
            _e("m2", "host", "The receiver.", col=2, row=0),
            _e("ping", "ping_probe", "Rides the sender — pings the receiver.", col=0, row=1),
            _e("pcap", "packet_view", "Rides the receiver — watch the pings arrive.", col=2, row=1),
        ),
        links=(("m1", "s1"), ("m2", "s1"), ("m1", "ping"), ("m2", "pcap")),
    ),
    Recipe(
        id="http_check", name="Probe a web service",
        summary="Fire HTTP requests at a web app from a machine and read the success rate + latency.",
        intent=("http", "curl", "web", "request", "probe", "2xx", "latency", "service"),
        teaches="how an HTTP Source proves a service answers, reporting success rate and latency",
        elements=(
            _e("m1", "host", "The client machine.", col=0, row=0),
            _e("web", "web_app", "The web service, reached by name (set it as the probe's Target).",
               col=1, row=0),
            _e("http", "http_probe", "Rides the client — requests the web app by name.",
               col=0, row=1),
        ),
        links=(("m1", "http"),),
    ),
    Recipe(
        id="throughput_test", name="Measure throughput (iPerf)",
        summary="Drive iPerf traffic between two machines across a switch and read the bandwidth.",
        intent=("iperf", "throughput", "bandwidth", "mbps", "congestion", "speed", "capacity"),
        teaches="how to measure link throughput with an iPerf Client Source and Server Sink",
        elements=(
            _e("m1", "host", "The client.", col=0, row=0),
            _e("s1", "switch", "Links the two machines.", col=1, row=0),
            _e("m2", "host", "The server.", col=2, row=0),
            _e("cli", "iperf_client", "Rides the client — drives traffic at the server.", col=0, row=1),
            _e("srv", "iperf_server", "Rides the server — reports received throughput.", col=2, row=1),
        ),
        links=(("m1", "s1"), ("m2", "s1"), ("m1", "cli"), ("m2", "srv")),
    ),
    Recipe(
        id="net_diagnostics", name="Diagnose a path (DNS · traceroute · counters)",
        summary="Resolve a name, trace the path to the edge, and watch interface counters — the "
                "classic diagnostic Sources and Sinks on one machine.",
        intent=("dns", "dig", "resolve", "traceroute", "path", "hops", "interface", "counters",
                "diagnostics", "iface"),
        teaches="how DNS, traceroute and interface counters reveal what a machine sees on the network",
        elements=(
            _e("m1", "host", "The machine you diagnose from.", col=0, row=0),
            _e("r1", "router", "The gateway to the edge.", col=1, row=0),
            _e("net", "cloud", "The Internet.", col=2, row=0),
            _e("dns", "dns_probe", "Rides the machine — resolves a name.", col=0, row=1),
            _e("trace", "traceroute_probe", "Rides the machine — traces the path.", col=0, row=2),
            _e("ifs", "iface_stats", "Rides the machine — streams rx/tx counters.", col=0, row=3),
        ),
        links=(("m1", "r1"), ("r1", "net"), ("m1", "dns"), ("m1", "trace"), ("m1", "ifs")),
    ),
    Recipe(
        id="xv6_drive", name="Drive an xv6 kernel (shell + workload)",
        summary="Run a custom command and spawn a scheduler workload on an xv6 Machine, over its "
                "console.",
        intent=("xv6", "shell", "command", "workload", "spin", "forktest", "scheduler", "process",
                "os", "syscall"),
        teaches="how to drive an xv6 kernel with a Shell Probe (a command) and a Workload (a process)",
        concept="os-scheduling",
        elements=(
            _e("k", "xv6", "The xv6 Machine — open the Machine Lab to watch it react.", col=0, row=0),
            _e("sh", "xv6_shell", "Rides the kernel — types a command into the console.", col=0, row=1),
            _e("wl", "xv6_workload", "Rides the kernel — spawns a process to drive the scheduler.",
               col=0, row=2),
        ),
        links=(("k", "sh"), ("k", "wl")),
    ),
    # ---- real hardware in the loop ---------------------------------------- #
    Recipe(
        id="gini32_phone", name="A real phone inside the emulated network",
        summary="A GINI32 board carries a real phone (or Pi) into the topology, so it can "
                "ping an emulated machine.",
        intent=("gini32", "esp32", "board", "hardware", "physical", "phone", "real device",
                "cyber-physical", "hardware in the loop", "wireless", "gbridge"),
        teaches="hardware-in-the-loop: a real radio bridging physical devices into an "
                "emulated topology over Ethernet-in-UDP",
        concept="networking-basics",
        elements=(
            _e("gb", "gini32",
               "The real board — set BoardID to the id on its label (`gini32 provision`).",
               col=0, row=0, props={"Mode": "routed"}),
            _e("r1", "router", "The board's devices arrive on this router's subnet.",
               col=1, row=0),
            _e("s1", "switch", "The LAN the emulated machine sits on.", col=2, row=0),
            _e("m1", "host", "An emulated machine for the real phone to ping.",
               col=3, row=0),
        ),
        links=(("gb", "r1"), ("r1", "s1"), ("s1", "m1")),
    ),
)

_BY_ID = {r.id: r for r in RECIPES}


def get_recipe(recipe_id: str) -> Recipe | None:
    return _BY_ID.get(recipe_id)


def suggest_recipes(query: str) -> list[Recipe]:
    """Deterministic intent match (the offline / fallback ranker the LLM mirrors):
    score recipes by how many intent tags or name words appear in the query."""
    q = (query or "").lower()
    scored: list[tuple[int, Recipe]] = []
    for r in RECIPES:
        score = sum(1 for tag in r.intent if tag in q)
        score += sum(1 for w in r.name.lower().split() if len(w) > 3 and w in q)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored]


def covered_elements() -> set[str]:
    """Every element type that appears in at least one recipe."""
    return {el.type_key for r in RECIPES for el in r.elements}
