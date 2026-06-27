"""Teaching guide for every palette element — what it is and WHEN to use it.

This is the knowledge the tutor grounds on when a student asks about an element from
the palette (rather than a placed instance). Each entry is plain, student-facing, and
focuses on the decision: when would I reach for this vs. a similar element?

The deterministic core returns this text; an LLM (when connected) rephrases/expands it
into a fuller explanation, but stays anchored to these facts.
"""
from __future__ import annotations

GUIDE: dict[str, str] = {
    # --- core networking -------------------------------------------------- #
    "router": (
        "A Router forwards packets between DIFFERENT IP networks (subnets), choosing the "
        "next hop from its routing table and decrementing TTL. Use one whenever two "
        "subnets must talk — it's the gateway out of a LAN. In gBuilder the router is a "
        "real, programmable C data plane you can open in the Router Lab. Reach for a "
        "router (not a switch) the moment hosts are on different 10.0.x.0/24 networks."),
    "switch": (
        "A Switch is a Layer-2 device: it forwards Ethernet FRAMES within a single network "
        "by learning which MAC address lives on which port. Use it to connect several hosts "
        "on the SAME subnet into one LAN. It does not route between subnets and assigns no "
        "IPs. Choose a switch over a hub for any realistic LAN — it sends traffic only to "
        "the right port instead of flooding everyone."),
    "hub": (
        "A Hub is a Layer-1 repeater: every frame that arrives is blindly copied to ALL "
        "other ports, so all hosts share one collision domain. It's mostly a teaching tool "
        "— use it to demonstrate collisions, flooding, and why switches replaced hubs. "
        "Avoid it in real designs; a switch does the same job without the noise."),
    "firewall": (
        "A Firewall filters traffic against rules (allow/deny by address, port, protocol). "
        "Place one between zones of different trust — e.g. between your LAN and the Internet "
        "— to control exactly what is permitted. In gBuilder it behaves like a router with "
        "an ACL stage, so use it when the lesson is about policy, not just connectivity."),
    "wap": (
        "An Access Point bridges WIRELESS clients onto the wired LAN — it's essentially a "
        "switch with a radio. Use it when the topology needs Wi-Fi devices joining an "
        "existing wired network."),
    "cloud": (
        "The Internet element represents the outside world / upstream network. Connect it "
        "to a router or gateway to model traffic leaving your topology toward the public "
        "Internet."),

    # --- software-defined networking -------------------------------------- #
    "ovs": (
        "Open vSwitch is a programmable software switch that speaks OpenFlow. Use it instead "
        "of a plain switch when the lesson is SDN — you want an external controller to install "
        "the forwarding rules rather than relying on MAC learning."),
    "controller": (
        "An OpenFlow Controller is the SDN 'brain': it connects to OVS/switches and programs "
        "their flow tables, deciding centrally how packets are handled. Use it with Open "
        "vSwitch to show centralized control separated from the data plane."),

    # --- compute ---------------------------------------------------------- #
    "host": (
        "A Machine is an end host — a PC or server that runs programs and originates and "
        "receives traffic. It's the source and sink of every experiment. In gBuilder it "
        "runs a Debian container with the GINI networking toolkit preinstalled — ping, "
        "traceroute, mtr, tcpdump, tshark, nmap, nc, dig, iperf3, curl, hping3, iptables, "
        "and more — so the book's experiments work out of the box (apt is there for "
        "anything else). Give it one link to a switch or router; attach it to two for two "
        "networks at once (multi-homed)."),
    "instance": (
        "A cloud Instance is a virtual machine in a cloud provider. Use it as a host inside "
        "cloud scenarios (VPCs, security groups) rather than a plain LAN."),
    "instance_group": (
        "An Autoscaling Group is a managed set of identical instances that grows or shrinks "
        "with load, usually behind a load balancer. Use it to model elastic, horizontally "
        "scaled compute."),
    "region": (
        "A Region / Zone groups cloud resources by physical location. Use it to discuss "
        "latency, availability zones, and multi-region designs."),

    # --- containers & kubernetes ------------------------------------------ #
    "container": (
        "A Container packages one application with its dependencies, sharing the host kernel. "
        "Use it for lightweight, app-per-box scenarios; many containers run on one node."),
    "pod": (
        "A Pod is Kubernetes' smallest unit — one or more containers that share a network "
        "namespace and IP. Use it when teaching Kubernetes networking specifically."),
    "k8s_node": (
        "A K8s Node is a worker machine that runs pods. Use it to show how a cluster places "
        "workloads across several nodes."),
    "k8s_cluster": (
        "A K8s Cluster groups nodes under one control plane. Use it as the boundary for a "
        "Kubernetes deployment."),
    "registry": (
        "A Container Registry stores and serves container images. Use it to model where "
        "nodes pull images from."),

    # --- cloud networking -------------------------------------------------- #
    "vpc": (
        "A VPC is your private, isolated network in a cloud — the cloud analog of a site LAN, "
        "carved into subnets. Use it as the top-level container for cloud instances and "
        "subnets."),
    "cloud_subnet": (
        "A Cloud Subnet is one IP range inside a VPC, often split public vs. private. Use it "
        "to separate internet-facing tiers from internal ones."),
    "security_group": (
        "A Security Group is a per-instance stateful firewall (allow rules by port/source). "
        "Use it to control instance traffic in a cloud — conceptually an ACL attached to the "
        "instance rather than a separate box."),
    "gateway": (
        "A Gateway connects a VPC/subnet to something outside it (the Internet, or another "
        "VPC). Use it as the controlled exit/entry point for cloud traffic."),
    "load_balancer": (
        "A Load Balancer spreads incoming connections across several backends (instances or "
        "an autoscaling group). Use it to model high availability and horizontal scaling — "
        "it's a forwarder that picks a healthy backend per request."),

    # --- storage & data ---------------------------------------------------- #
    "object_store": (
        "Object Storage holds files/blobs addressed by key over HTTP (think S3). Use it for "
        "static assets, backups, and data lakes — not for low-latency block access."),
    "block_volume": (
        "A Block Volume is a virtual disk attached to one instance. Use it when an instance "
        "needs persistent, filesystem-style storage."),
    "database": (
        "A Managed Database is a provider-run SQL/NoSQL store. Use it so apps have a "
        "queryable, durable data tier without you running the DB host yourself."),

    # --- serverless -------------------------------------------------------- #
    "function": (
        "A Function is serverless code that runs on demand and scales to zero. Use it for "
        "event-driven glue logic where you don't manage a server."),
    "api_gateway": (
        "An API Gateway is the managed front door for APIs — routing, auth, rate limiting — "
        "usually in front of functions or services. Use it to expose backends to clients."),
    "queue": (
        "A Message Queue buffers messages between producers and consumers so they can work "
        "asynchronously and absorb bursts. Use it to decouple components."),

    # --- edge & traffic ---------------------------------------------------- #
    "proxy": (
        "A Reverse Proxy sits in front of your services and forwards client requests to "
        "them, adding TLS, routing by host/path, and a single entry point. Use it (vs a "
        "raw Load Balancer) when you want application-aware routing and a dashboard — in "
        "gBuilder it runs real Traefik."),
    "web_app": (
        "A Web App is a small demo backend that returns its own hostname. Use several of "
        "them behind a Load Balancer or Reverse Proxy to SEE requests spread across "
        "backends — the simplest way to teach horizontal scaling and load balancing."),

    # --- streaming & messaging --------------------------------------------- #
    "stream": (
        "An Event Stream is an append-only log of events that many consumers read at their "
        "own pace (Kafka-style). Use it for event-driven pipelines, metrics, and replay — "
        "choose it over a Message Queue when you need durable, replayable history rather "
        "than once-delivered messages."),
    "messaging": (
        "Pub/Sub (NATS) delivers messages to whoever is subscribed to a subject, right now. "
        "Use it for fast, lightweight fan-out between services when you don't need the "
        "durable, replayable log that an Event Stream gives you."),

    # --- cache & NoSQL ----------------------------------------------------- #
    "cache": (
        "A Cache (Redis) keeps hot data in memory for microsecond reads. Put one in front "
        "of a database to cut load and latency — use it for sessions, counters, and "
        "results you can afford to recompute if lost."),
    "nosql": (
        "A NoSQL Database (MongoDB) stores schema-flexible documents instead of SQL tables. "
        "Reach for it when your data is hierarchical/varied and you value flexible schemas "
        "over relational joins and constraints."),

    # --- observability ----------------------------------------------------- #
    "metrics": (
        "Metrics (Prometheus) scrapes numeric time-series from your services and lets you "
        "query them with PromQL. Use it to measure rate, errors, and latency — the data "
        "behind every dashboard and alert."),
    "dashboard": (
        "Dashboards (Grafana) visualise metrics and logs as graphs. Point it at a Metrics "
        "(Prometheus) source so students can watch a system's behaviour while they load it."),
    "tracing": (
        "Tracing (Jaeger) records the path of a single request as it hops across services, "
        "with timing at each step. Use it to teach where latency comes from in a "
        "distributed system — the 'why is this slow?' tool."),

    # --- workload & testing ------------------------------------------------ #
    "load_generator": (
        "A Load Generator (Fortio) fires controlled traffic at a target and reports QPS and "
        "latency histograms. Use it to run experiments — push a service until it slows or "
        "an autoscaler reacts, and watch the metrics/dashboards respond."),
}


def guide_for(key: str) -> str | None:
    """Return the teaching guide for an element type key, or None if not catalogued."""
    return GUIDE.get(key)
