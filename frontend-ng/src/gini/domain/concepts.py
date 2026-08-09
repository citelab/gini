"""Concept notes — tier-3 GINI knowledge (the *how it actually works* layer).

The element catalog says *what exists* and the connection grammar says *how things
wire*; this module says *how each subsystem behaves in GINI* — the depth that lets the
Ask GINI agent answer probing questions ("why does my private DB stay reachable from the
web tier?") instead of falling back on generic training knowledge.

Pure data, Qt/compiler-free, so every layer (retrieval, tests) can share it. Each note is
compact (a paragraph or two) and student-facing. Notes are keyed to the elements and the
search terms they cover, so retrieval can pull the right ones for a question.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Concept:
    """One subsystem explainer, with the elements and search terms it covers."""
    key: str                                   # stable slug
    title: str                                 # human title
    elements: tuple[str, ...]                  # related device type_keys
    keywords: tuple[str, ...] = field(default_factory=tuple)   # retrieval synonyms
    body: str = ""                             # the teaching text (authoritative)


CONCEPTS: tuple[Concept, ...] = (
    Concept(
        "networking-basics", "LANs, switching & routing",
        ("host", "switch", "hub", "router", "firewall", "wap", "gini32"),
        ("lan", "subnet", "gateway", "layer 2", "layer 3", "l2", "l3", "ethernet",
         "collision", "broadcast", "arp", "mac", "ip", "route", "routing"),
        "GINI's networking plane is a real user-space fabric, not a simulation: links are "
        "Ethernet-in-UDP, each Router runs a real C gRouter, and Switches are multiplexed "
        "into one `fabric` container. A Host is an end machine. A Switch (Layer 2) learns "
        "MAC addresses and forwards within one subnet/broadcast domain; a Hub (Layer 1) is a "
        "dumb repeater that floods every port — useful to *see* collisions and why switches "
        "replaced hubs. A Router (Layer 3) forwards between subnets: each host needs the "
        "router as its gateway to reach other subnets or the Internet. A Firewall filters "
        "between trust zones. Addressing is automatic unless Manual Addressing is on.",
    ),
    Concept(
        "sdn", "Software-defined networking (OpenFlow)",
        ("ovs", "controller"),
        ("sdn", "openflow", "flow", "flow table", "pox", "control plane", "data plane",
         "software defined", "dashboard", "visualize"),
        "SDN splits the control plane (decisions) from the data plane (forwarding). In GINI "
        "the OpenVSwitch element is the C gRouter run in `--openflow` mode, and the Controller "
        "is a POX controller speaking OpenFlow 1.0. An OVS has no built-in logic — it MUST "
        "connect to a Controller, which installs flow rules reactively the first time a flow "
        "appears (so the first packet triggers a rule, then the rest follow it). Wire hosts to "
        "the OVS and the OVS to a Controller; build multi-switch fabrics by linking OVS to OVS. "
        "To SEE what the controller installed, double-click the OVS: its Router Lab opens in "
        "SDN dashboard mode showing the live OpenFlow flow table — each flow's match, action, "
        "and packet/byte counters, refreshed while the lab runs. (There is no separate 'SDN "
        "dashboard' element; that visualization lives on the OVS itself.)",
    ),
    Concept(
        "nfv", "Network Function Virtualization (NFV)",
        ("firewall", "cloud", "router", "vnf"),
        ("nfv", "network function virtualization", "vnf", "virtual network function",
         "middlebox", "network function", "appliance", "service function", "virtualize"),
        "NFV runs network functions (firewall, NAT, IDS/DPI, cache, load balancer, WAN "
        "optimizer) as SOFTWARE on ordinary compute instead of dedicated hardware boxes — so "
        "you can deploy, move, and scale a function like any other workload. GINI realizes NFV "
        "in a few ways: (1) INSIDE a router — the gRouter has an inline data-plane pipeline "
        "(open the Router Lab and add modules: ACL/Firewall, NAT, rate-limit, tap/mirror, a "
        "custom Lua module), each a function processing packets as they pass through; (2) as "
        "STANDALONE in-path elements on the fabric — the Firewall element filters, and the "
        "Internet element is a real NAT gateway; (3) the VNF element — a real container you "
        "wire INLINE (pick its function in Kind: firewall/block/IDS/cache/shaper) that "
        "IP-forwards between its two interfaces and applies the function (firewall and block "
        "are real iptables today; IDS/cache/shaper forward-only for now). Because a VNF "
        "is just a workload, the cost meter prices it, observability traces it, and Kubernetes "
        "can autoscale it — that's the whole point of virtualizing the function.",
    ),
    Concept(
        "sfc", "Service Function Chaining (SFC)",
        ("firewall", "cloud", "router", "ovs", "controller"),
        ("sfc", "service function chain", "service chain", "chaining", "steering",
         "classifier", "traffic steering", "function chain", "chain", "service chaining"),
        "SFC steers traffic through an ORDERED sequence of network functions before it reaches "
        "its destination — e.g. firewall -> IDS -> NAT. A classifier decides WHICH traffic "
        "enters WHICH chain (say, only web traffic through a WAF); the steering mechanism walks "
        "each packet through the functions in order. GINI builds chains two ways: (1) INSIDE "
        "one router — the Router Lab's ordered inline-module pipeline IS a service chain; add, "
        "reorder, and step-trace a packet through firewall -> NAT -> rate-limit and watch each "
        "function's verdict; (2) ACROSS the fabric — wire functions in series (host -> firewall "
        "-> Internet/NAT) so the drawn path is the chain, or, for selective steering, let the "
        "SDN controller install OpenFlow rules that push chosen flows through VNFs in order (the "
        "steering rules then show up in the OVS flow-table dashboard). The router pipeline vs. a "
        "steered container chain is the classic 'function in the box' vs. 'function as a "
        "service' contrast.",
    ),
    Concept(
        "cloud-compute", "Cloud compute: instances & containers",
        ("instance", "container", "web_app", "host"),
        ("vm", "virtual machine", "container", "docker", "compute", "workload", "runtime",
         "image", "service discovery"),
        "The cloud plane is plain Docker containers on a shared `gini` network, reachable by "
        "name (real service discovery — no manual IPs). An Instance models a VM-style workload; "
        "a Container models a single containerised process (both take an image/command). A Web "
        "App is a ready-made HTTP service you can put behind a load balancer or proxy and back "
        "with a datastore. Unlike the networking plane, these don't need routers/switches — they "
        "find each other by service name on the cloud bridge.",
    ),
    Concept(
        "serverless", "Serverless (functions & API gateway)",
        ("function", "api_gateway", "object_store", "queue", "stream", "messaging"),
        ("serverless", "faas", "lambda", "function", "api gateway", "cold start", "invoke",
         "event driven", "stateless", "handler", "trigger"),
        "Serverless = run code without managing servers; you pay per invocation and it scales "
        "to zero. A Function is stateless: it holds no data between calls, so it reads/writes "
        "state from a datastore or object storage. In GINI all functions run in one shared "
        "`faas` runtime container; each handles requests via `handle(event, context)` and "
        "returns `{statusCode, body}`. The API Gateway (a Traefik path-router) is the front "
        "door — it maps a URL path to a function. Functions can also be *triggered by events*: "
        "a Queue message, a Stream record, or a pub/sub message invokes the function "
        "(event-driven). The first hit after idle pays a small cold-start delay; the cost meter "
        "counts invocations, not idle time.",
    ),
    Concept(
        "messaging-queue", "Queues, streams & pub/sub",
        ("queue", "stream", "messaging", "function"),
        ("queue", "message queue", "stream", "streaming", "pub/sub", "pubsub", "publish",
         "subscribe", "async", "asynchronous", "decouple", "kafka", "rabbitmq", "nats",
         "producer", "consumer", "event"),
        "These decouple services so a producer doesn't wait on a consumer. A Queue (work "
        "queue, RabbitMQ-style) delivers each message to one consumer — good for tasks/jobs. "
        "A Stream (event log, Kafka/Redpanda-style) keeps an ordered, replayable log many "
        "consumers can read independently — good for event sourcing/analytics. Messaging "
        "(pub/sub, NATS-style) fans a message out to all subscribers. In GINI any of the three "
        "can *trigger a Function* (event-driven serverless): drop the queue/stream/messaging "
        "element next to a function and it subscribes. A producing workload (web app, "
        "instance) connects to the queue to publish.",
    ),
    Concept(
        "datastores", "Datastores: SQL, NoSQL, cache, object, block",
        ("database", "nosql", "cache", "object_store", "block_volume"),
        ("database", "sql", "postgres", "nosql", "mongo", "cache", "redis", "object storage",
         "s3", "bucket", "block volume", "disk", "persistence", "state", "store"),
        "Pick the store to fit the data. A Database is relational (Postgres) for structured, "
        "queryable state. NoSQL (Mongo) is document/flexible-schema for denormalised data. A "
        "Cache (Redis) is in-memory, fast, and ephemeral — put it in front of a database to "
        "cut load. Object Storage (MinIO/S3-style) holds files/blobs and function code, "
        "durable and cheap. A Block Volume is a persistent disk attached to one Instance (like "
        "an EBS volume). Apps, functions, and pods all connect to these for their state; "
        "functions are stateless so they lean on them heavily.",
    ),
    Concept(
        "load-balancing", "Load balancing, proxies & load testing",
        ("load_balancer", "proxy", "load_generator", "web_app"),
        ("load balancer", "load balancing", "reverse proxy", "proxy", "nginx", "traefik",
         "round robin", "least conn", "tls", "load test", "fortio", "throughput", "traffic"),
        "A Load Balancer (nginx) spreads incoming traffic across several backend replicas — "
        "its Scheme (round-robin / least-conn / ip-hash) is a property, and it builds its "
        "backend list from the links you draw. A Reverse Proxy (Traefik) fronts a service for "
        "path routing and TLS termination; you can chain a load balancer in front of a proxy. "
        "A Load Generator (Fortio) fires HTTP load at a backend, a gateway, or a function so "
        "you can watch throughput, latency, cost, and autoscaling react — drive it from "
        "gBuilder with a live rate throttle.",
    ),
    Concept(
        "kubernetes", "Kubernetes: clusters, pods & autoscaling",
        ("k8s_cluster", "pod", "instance_group", "registry"),
        ("kubernetes", "k8s", "k3s", "cluster", "pod", "deployment", "hpa", "autoscale",
         "autoscaling", "replicas", "registry", "orchestration"),
        "GINI runs REAL Kubernetes (a k3s container), not a mock. A K8s Cluster is the k3s "
        "node; a Pod compiles to a Deployment (its replicas run your image); a Pod Autoscaler "
        "(HPA — the `instance_group` element) scales a Pod's replicas on CPU. A Pod MUST live "
        "in a Cluster. A private Registry serves images to the cluster. GINI generates the "
        "Deployment/Service/HPA manifests and applies them with kubectl, and you can watch "
        "replicas scale live. (The single-node `k8s_node` element is hidden — use Cluster + "
        "Pod + Autoscaler.)",
    ),
    Concept(
        "vpc-networking", "VPCs, subnets & public/private",
        ("vpc", "cloud_subnet", "region", "gateway"),
        ("vpc", "subnet", "public", "private", "isolation", "cidr", "network", "egress",
         "region", "availability zone", "az", "cloud network"),
        "A VPC is a real isolated Docker network with its own CIDR — services inside reach each "
        "other by name, but nothing outside reaches in. Drop workloads inside the VPC box (or a "
        "Subnet box) on the canvas and containment sets membership. A Subnet's Tier is the "
        "teaching knob: a PUBLIC subnet's members also join a per-VPC egress bridge, so they "
        "get real Internet and their consoles open from the host; a PRIVATE subnet's members "
        "stay on the internal VPC fabric ONLY — no Internet, not reachable from the host, but "
        "STILL reachable by other members of the same VPC (which is why a public web tier can "
        "talk to a private database while the outside world can't). A Region/AZ is a label only "
        "— there's no real geography on one host. A Gateway gives a VPC outbound Internet.",
    ),
    Concept(
        "security-groups", "Security groups (stateful firewall)",
        ("security_group",),
        ("security group", "firewall", "default deny", "least privilege", "ingress", "iptables",
         "allow", "port", "stateful", "acl"),
        "A Security Group is a stateful, DEFAULT-DENY firewall you attach to the workloads or "
        "datastores it protects. List inbound rules in its Ingress field, one per line: "
        "`<port> from <source>`, where source is a CIDR, `anywhere`, or ANOTHER security "
        "group's name. Only the listed ports open (outbound is allowed, and replies to allowed "
        "traffic flow back because it's stateful). Referencing another SG by name is what makes "
        "least privilege work — the classic web->app->db chain: web open to the world on 80, "
        "app reachable only from web, db only from app. Under the hood each protected member "
        "gets a per-member iptables sidecar sharing its network namespace, and the telemetry "
        "agent is always allowed so the dashboard keeps working.",
    ),
    Concept(
        "observability", "Observability: metrics, dashboards & tracing",
        ("metrics", "dashboard", "tracing"),
        ("observability", "metrics", "prometheus", "dashboard", "grafana", "tracing", "jaeger",
         "monitoring", "scrape", "telemetry", "kpi", "latency"),
        "Metrics (Prometheus) scrapes numeric time-series from your targets — apps, proxies, "
        "load balancers, functions, the API gateway. A Dashboard (Grafana) visualises them and "
        "MUST connect to a metrics source. Tracing (Jaeger) collects distributed traces across "
        "services to show a request's path and where time goes. GINI also runs a `cloudfabric` "
        "agent that polls each service's native metrics and feeds the gBuilder cost/Live "
        "panels, so you get per-element CPU/throughput/latency without wiring everything by "
        "hand.",
    ),
    Concept(
        "cost-model", "The GINI $ cost meter",
        ("region",),
        ("cost", "price", "pricing", "bill", "gini dollars", "money", "budget", "meter",
         "cheap", "expensive", "spend"),
        "GINI shows a live 'cloud bill' so students feel the price of their design. The meter "
        "sums a per-element rate x its instance size (S/M/L/XL multiply cost 1/2/4/8, matching "
        "the CPU cap) x time it runs, plus usage where it applies (e.g. serverless counts "
        "invocations, not idle). Rates live in `pricing.py` and are editable in Settings, so "
        "you can model different providers. The dashboard breaks the bill down by category so "
        "students can see what's driving cost and compare designs (e.g. always-on VMs vs. "
        "scale-to-zero functions).",
    ),
    Concept(
        "kata-isolation", "VM vs container isolation (Kata)",
        ("kinstance",),
        ("kata", "microvm", "vm", "isolation", "secure", "hypervisor", "sandbox",
         "vm vs container"),
        "The Kata Instance is a deliberately RESTRICTED element for one experiment: comparing "
        "a VM-isolated workload against a plain container. It runs as a Kata microVM (real "
        "hardware-virtualisation isolation, stronger than a container's shared-kernel "
        "isolation) via a brokered GINI server, and it pays a longer startup than a container "
        "— which is the whole teaching point (isolation vs. startup/overhead trade-off). It "
        "wires only to a load source, a backend datastore/object store, and metrics — never to "
        "Kubernetes, VPCs, or the networking plane, because it's meant to stay flat for the "
        "comparison.",
    ),
    Concept(
        "internet-nat", "The Internet / NAT gateway",
        ("cloud",),
        ("internet", "nat", "egress", "public", "outbound", "gateway", "wan", "masquerade"),
        "The Internet element is a real on-fabric NAT gateway, not just a cloud icon. In "
        "faithful mode it forces egress to flow through the topology you actually drew: a "
        "host's default route goes out via the router(s) and firewall you placed, then "
        "MASQUERADEs to the outside — so if you forgot a route or a firewall blocks it, traffic "
        "really fails, which is the lesson. Wire a router or firewall to the Internet element "
        "to give a LAN outbound connectivity.",
    ),
    Concept(
        "os-scheduling", "Processes & CPU scheduling (xv6)",
        ("xv6",),
        ("xv6", "kernel", "operating system", "os", "process", "scheduler", "scheduling",
         "context switch", "swtch", "time slice", "timeslice", "quantum", "preemption",
         "preemptive", "round robin", "trap", "timer interrupt", "proc", "pcb", "runnable",
         "machine lab"),
        "The xv6 Machine runs a real teaching kernel (MIT 6.1810's xv6) on QEMU-RISC-V — an "
        "actual OS, not a container. The Machine Lab reads its live state through QEMU's GDB "
        "stub (no kernel patch needed to observe): the process table (xv6's `proc[]`: each "
        "entry has a pid, a state — UNUSED/USED/SLEEPING/RUNNABLE/RUNNING/ZOMBIE — a name and a "
        "parent), the CPU registers (pc, sp, ra, satp, …), and the kernel stack (unwound with "
        "`bt`). A context switch happens in `swtch()`: xv6's per-CPU scheduler loop picks a "
        "RUNNABLE process, switches to it, and a timer interrupt later traps back so the "
        "scheduler can pick again — that is preemptive round-robin. GINI makes this visible: "
        "the four panels (machine/process/memory/stack) update on each switch, a Gantt strip "
        "shows which pid held the CPU over time, and you can slow the time-slice (the timer "
        "reload) to ~1 s or Step one switch at a time to actually watch control move between "
        "processes. The mirror of the Router Lab: there you step a packet through a pipeline; "
        "here you step the CPU through context switches.",
    ),
    Concept(
        "os-processes", "Processes, fork/exec & system calls (xv6)",
        ("xv6",),
        ("xv6", "process", "fork", "exec", "wait", "exit", "zombie", "pid", "system call",
         "syscall", "trap", "user mode", "kernel mode", "proc", "parent", "child"),
        "On the xv6 Machine a process is one entry in the kernel's `proc[]` table: a pid, a "
        "state, a name, a parent, its own page table and a kernel stack. `fork()` makes a child "
        "by copying the parent; `exec()` replaces a process's memory image with a program; "
        "`wait()` lets a parent collect a finished child; `exit()` ends a process, which then "
        "sits as a ZOMBIE until its parent wait()s for it — a zombie that lingers means the "
        "parent never did, and you can see exactly that in the process table. A system call is "
        "how a user program asks the kernel to act: it traps from user mode into the kernel, "
        "runs the handler, and returns — visible in the CPU registers and kernel stack in the "
        "Machine Lab. GINI runs a REAL xv6, so these are the actual kernel mechanics, not a "
        "simulation.",
    ),
    Concept(
        "os-memory", "Virtual memory & paging (xv6)",
        ("xv6",),
        ("xv6", "memory", "virtual memory", "paging", "page table", "pte", "satp", "sv39",
         "page fault", "cow", "copy on write", "lazy allocation", "demand paging", "trampoline",
         "trapframe", "allocator", "kalloc"),
        "xv6 on RISC-V uses Sv39 three-level page tables: `satp` points at the root page table, "
        "and a virtual address is split into three 9-bit indices plus a 12-bit offset. Each leaf "
        "PTE maps a virtual page to a physical page with permission bits V/R/W/X/U. A process's "
        "address space runs low-to-high — text, data, heap (grown by `sbrk`), a guard page, the "
        "user stack — with the trapframe and the shared trampoline mapped at the very top. The "
        "Memory face in the Machine Lab shows these as the address-space map, the leaf mappings "
        "(VA→PA with perms), and the physical page allocator (free vs used). A page fault traps "
        "into the kernel; demand/lazy allocation handles it by allocating a physical page and "
        "adding a mapping — you can watch the stack grow that way. Copy-on-write fork and "
        "lazy allocation are labs that add exactly this behaviour.",
    ),
    Concept(
        "os-filesystem", "File system, buffer cache & the log (xv6)",
        ("xv6",),
        ("xv6", "file system", "filesystem", "inode", "dinode", "directory", "dirent", "block",
         "superblock", "bitmap", "buffer cache", "bcache", "log", "journal", "journaling",
         "crash", "transaction", "write ahead log", "commit"),
        "xv6's disk is a fixed sequence of regions: boot | super | log | inodes | bitmap | data. "
        "A file is an inode (`dinode`) holding its type, size, link count and up to 12 direct + "
        "1 indirect block pointers; a directory is just a file whose data is an array of "
        "`dirent {inum, name}`, which is how paths resolve. Recently-used blocks live in the "
        "buffer cache (bcache) so repeated reads hit memory instead of disk. Crucially, writes "
        "go through a write-ahead LOG: a system call's block writes are first recorded in the "
        "log, the log is committed, and only then installed at their home locations — so a crash "
        "either loses the whole transaction or applies all of it, never a half-write. The "
        "Storage face shows the layout, inodes, directory tree, buffer cache, and the log "
        "transaction as it fills and commits.",
    ),
    Concept(
        "os-zoo", "The OS Zoo (historical OSes under emulation)",
        ("freedos", "kolibri", "menuet", "msdos", "mac7", "win31", "oszoo_byo"),
        ("os zoo", "freedos", "dos", "ms-dos", "msdos", "kolibri", "kolibrios", "menuet",
         "menuetos", "assembly",
         "windows", "win95",
         "windows 95", "mac", "system 7", "classic", "historical", "emulator", "emulation",
         "qemu", "vnc", "novnc", "basilisk", "dosbox", "vintage", "retro", "boot"),
        "The OS Zoo is a play-with-real-OSes section (separate from the xv6 workbench, which is "
        "about OS internals). Each Zoo element is a genuine historical operating system running "
        "under emulation inside a Docker container — QEMU for x86 guests (FreeDOS, KolibriOS, "
        "MenuetOS), Basilisk II for a 68k classic Mac — with its screen embedded in gBuilder over "
        "noVNC (a VNC framebuffer served as a web page, shown in a QWebEngineView). Double-click an "
        "element to open the Zoo Lab and use the OS live: mouse, keyboard, boot and all. GINI ships "
        "only freely-redistributable OSes (FreeDOS, KolibriOS and MenuetOS boot out of the box; "
        "KolibriOS and MenuetOS — tiny assembly OSes on a single floppy — are the fast ones). "
        "Proprietary OSes (Windows 95, Mac "
        "System 7) use the 'Classic OS (your image)' "
        "element: GINI provides the emulator and points to where the image legally lives, and you "
        "supply a disk image (and a Mac ROM for 68k) you own — GINI hosts nothing copyrighted. "
        "Boots are ephemeral by default (a clean image each Run); turn on Persist to keep changes "
        "in a qcow2 overlay. v1 is display-only; fabric networking (wiring a Zoo OS into the GINI "
        "network like any machine) is v2.",
    ),
)


# -- lookups ---------------------------------------------------------------- #
_BY_KEY: dict[str, Concept] = {c.key: c for c in CONCEPTS}
_BY_ELEMENT: dict[str, list[Concept]] = {}
for _c in CONCEPTS:
    for _e in _c.elements:
        _BY_ELEMENT.setdefault(_e, []).append(_c)


def by_key(key: str) -> Concept | None:
    return _BY_KEY.get(key)


def for_element(type_key: str) -> list[Concept]:
    """Concept notes that discuss this element type."""
    return list(_BY_ELEMENT.get(type_key, []))


def search(terms) -> list[Concept]:
    """Concepts whose title/keywords/elements match any of the given lowercase terms,
    ranked by number of matches. `terms` is any iterable of strings."""
    want = [t.strip().lower() for t in terms if t and t.strip()]
    scored: list[tuple[int, Concept]] = []
    for c in CONCEPTS:
        hay = " ".join((c.key, c.title, " ".join(c.keywords), " ".join(c.elements))).lower()
        score = sum(1 for t in want if t in hay)
        if score:
            scored.append((score, c))
    scored.sort(key=lambda s: -s[0])
    return [c for _, c in scored]
