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
    "vnf": (
        "A VNF (Virtualized Network Function) is a CONTAINER running a network function — a "
        "firewall, IDS, cache, or shaper — inserted INLINE in the forwarding path. Wire it "
        "between two elements and traffic flows through it; pick the function in 'Kind' and "
        "give its config in 'Rules'. Chain several in series (firewall → IDS → NAT) to build "
        "a Service Function Chain (SFC). Use it to teach NFV — functions as software in the "
        "path — versus a fixed hardware appliance."),
    "wap": (
        "An Access Point bridges WIRELESS clients onto the wired LAN — it's essentially a "
        "switch with a radio. Use it when the topology needs Wi-Fi devices joining an "
        "existing wired network."),
    "cloud": (
        "The Internet element represents the outside world / upstream network. Connect it "
        "to a router or gateway to model traffic leaving your topology toward the public "
        "Internet."),
    "gini32": (
        "A GINI32 Board is a REAL ESP32 on your desk running the gBridge firmware — the "
        "only element that is not emulated. It raises its own Wi-Fi network; phones, "
        "Raspberry Pis and sensors that join it become hosts inside the drawn topology, "
        "their traffic carried as Ethernet-in-UDP to the emulated core. Wire it to a "
        "router or switch and set BoardID to the id on the board's LABEL (written by "
        "`gini32 provision --id`); everything else — address, hotspot name, subnet — is "
        "handed to the board from the canvas when it checks in. Mode 'routed' (the "
        "default) gives the physical subnet its own route so traffic flows BOTH ways; "
        "'nat' hides the real devices behind the board's single address, which is the "
        "asymmetry the book asks you to discover. Channel is REPORTED by the board, not "
        "set — one radio serves both faces, so the hotspot follows the uplink's channel. "
        "Devices that join the hotspot appear on the canvas by themselves."),

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
    "xv6": (
        "An xv6 Machine runs the real xv6 teaching kernel (MIT 6.1810) on QEMU-RISC-V — not a "
        "container, an actual operating system booting from a tiny kernel. It is the OS-course "
        "workbench: double-click it to open the Machine Lab and watch the kernel run — the "
        "scheduler moving the CPU between processes, the process table, CPU registers, memory "
        "and the kernel stack — then slow the time-slice to watch context switches one at a "
        "time. It is standalone by default (no fabric wiring); you drive it from its own serial "
        "console. Use it to teach processes, scheduling, virtual memory, traps and file "
        "systems on a kernel small enough to read end to end."),
    "terminal": (
        "A Terminal is an xv6 Machine's console — a screen and keyboard in one, like a real tty "
        "(xv6's console is a single bidirectional UART, so output and input share one stream). "
        "Connect it to an xv6 Machine and double-click to open it: type xv6 commands (ls, cat, "
        "echo, spin 10 &, …) and their output appears inline. Up-arrow recalls history and `help` "
        "lists what you can run; this is the authentic way to launch programs with arguments."),
    "storage_volume": (
        "A Storage Volume is the xv6 Machine's disk. Connect it and double-click to open the "
        "Storage view — the on-disk layout (boot/super/log/inodes/bitmap/data), the inodes and "
        "directory tree, the buffer cache, and the write-ahead log. xv6 has a single custom file "
        "system with no VFS; supporting alternate file systems is an advanced student project."),
    "kinstance": (
        "A Kata Instance runs your workload inside a lightweight microVM with its OWN guest "
        "kernel (Kata Containers), instead of sharing the host kernel like a normal container. "
        "Use it to compare VM-vs-container trade-offs: stronger isolation, but slower boot and "
        "more memory/IO overhead. It needs a Kata-enabled Linux backend and is kept to flat "
        "experiment topologies (no VPCs, no Kubernetes)."),
    "instance_group": (
        "A Pod Autoscaler is a Kubernetes Horizontal Pod Autoscaler (HPA): it watches one "
        "Deployment's CPU and adds or removes pod replicas between Min and Max to hold a "
        "target CPU%. Connect it to a Pod — the HPA scales that one workload, not the whole "
        "cluster, because each workload scales on its own policy. Note the contrast with two "
        "other K8s autoscalers: the Cluster Autoscaler adds/removes Nodes (machines) when "
        "pods don't fit, and the Vertical Pod Autoscaler (VPA) resizes each pod's CPU/memory. "
        "The HPA is also the container-world cousin of a cloud Auto Scaling Group, which "
        "instead scales VM instances behind a load balancer."),
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
    # --- Sources / Sinks (riders: run inside a donor, no container of their own) --------- #
    "ping_probe": (
        "A Ping Probe is a Source: attach it to a Machine/Router and it runs `ping` INSIDE that "
        "donor, streaming live RTT and loss. Double-click to start/stop. Count 0 pings until you "
        "stop it; Count N sends N. It has no container — it rides its donor over a dotted edge."),
    "http_probe": (
        "An HTTP Probe is a Source: it runs `curl` inside its donor at a Target/Path and reports "
        "2xx success rate and latency. Continuous (Count 0) or a fixed number (Count N). Lighter "
        "than the Load Generator — use it to prove a service answers, not to stress it."),
    "packet_view": (
        "A Packet View is a Sink: it runs `tcpdump` inside its donor and streams the packets it "
        "sees. Attach it to the RECEIVER and run a Source on the sender to watch traffic arrive. "
        "Count 0 captures until stopped; Count N stops after N packets."),
    "dns_probe": (
        "A DNS Probe is a Source: it resolves the hostname in Target from inside its donor and "
        "reports whether (and to what) it resolved. It uses the system resolver over the drawn "
        "gini0 network (GINI writes peer names into /etc/hosts), so it returns the topology address "
        "— not Docker's. (Target is the name to look up; Name is just this element's label.)"),
    "traceroute_probe": (
        "A Traceroute is a Source: it runs `traceroute` inside its donor to a Target and reports "
        "the hop path. Pair it with a Packet View to watch each hop, or use it to see routing."),
    "iperf_client": (
        "An iPerf Client is a Source: it drives `iperf3 -c` at a Target running an iPerf Server "
        "and reports measured throughput (Mbit/s). Use it to teach bandwidth and congestion."),
    "iperf_server": (
        "An iPerf Server is a Sink: it runs `iperf3 -s` inside its donor and reports the "
        "throughput it receives from an iPerf Client. Pair the two across a link to measure it."),
    "iface_stats": (
        "Interface Stats is a Sink: it reads /proc/net/dev inside its donor and streams rx/tx "
        "packet and byte counts, so you can watch traffic volume rise and fall on an interface."),
    "xv6_shell": (
        "A Shell Probe is a Source for the xv6 Machine: type a Command (e.g. `ls`, `cat README`) "
        "and it types it into the kernel's console and streams the output back — the OS-course "
        "counterpart of the HTTP Probe."),
    "xv6_workload": (
        "A Workload is a Source for the xv6 Machine: it spawns a Program (`spin`, `forktest`, "
        "`usertests`) to drive the scheduler. Run it in the background so several compete, and "
        "watch the effect in the Machine Lab — the OS-course load generator."),
    "freedos": (
        "FreeDOS is an OS Zoo element: a real, still-maintained MS-DOS-compatible operating "
        "system running under QEMU in a container, its screen embedded in gBuilder over noVNC. "
        "Double-click it to open the Zoo Lab and use it live — the single-tasking, real-mode "
        "command-line PC of the DOS era. It boots out of the box; boots are ephemeral unless you "
        "turn on Persist."),
    "kolibri": (
        "KolibriOS is an OS Zoo element: a tiny GUI operating system written entirely in assembly, "
        "running under QEMU and embedded over noVNC. The whole system boots from a single 1.44 MB "
        "floppy to a graphical desktop in seconds — even under software emulation — so it's the "
        "fast OS Zoo guest to reach for. Double-click to open the Zoo Lab; ephemeral unless "
        "Persist is on."),
    "menuet": (
        "MenuetOS is an OS Zoo element: the assembly GUI OS that KolibriOS forked from, running "
        "under QEMU and embedded over noVNC. Like KolibriOS, the whole graphical desktop lives on "
        "a single 1.44 MB floppy and boots in seconds under emulation (GINI ships the open-source "
        "32-bit build). Double-click to open the Zoo Lab; ephemeral unless Persist is on."),
    "msdos": (
        "MS-DOS 6.22 is an OS Zoo preset: the real Microsoft MS-DOS, booted from a disk image under "
        "QEMU and embedded over noVNC — so it's the genuine article (`VER` reports MS-DOS, not a "
        "clone). Drag it on and Run; GINI downloads a public pre-installed MS-DOS 6.22 disk on first "
        "boot (it ships nothing proprietary) and boots to the C:\\> prompt. Pair it with FreeDOS to "
        "compare the original MS-DOS with the open re-implementation. Ephemeral unless Persist."),
    "mac7": (
        "Mac System 7 is an OS Zoo preset: classic Macintosh System 7.5.3 on an emulated 68k Mac "
        "(Basilisk II), embedded over noVNC. Drag it on and Run — GINI downloads a Quadra ROM and a "
        "bootable System 7 disk from a public archive on first boot (it ships nothing proprietary), "
        "caches them, and boots to the Mac desktop. It's the 'Classic OS (your image)' element with "
        "the Image/Rom URLs pre-filled; edit them to use your own files. Ephemeral unless Persist."),
    "win31": (
        "Windows 3.11 is an OS Zoo preset: Windows for Workgroups 3.11 under DOSBox (the fast "
        "vintage-Windows path), embedded over noVNC. Drag it on and Run — GINI downloads a public "
        "pre-installed Windows 3.11 on first boot (it ships nothing proprietary), mounts it as C:, "
        "and starts Windows. It's the 'Classic OS (your image)' element with the Image URL "
        "pre-filled; edit it to use your own folder or zip. Ephemeral unless Persist."),
    "oszoo_byo": (
        "Classic OS (your image) is the bring-your-own OS Zoo element for proprietary systems "
        "GINI can't ship (Windows 95, Mac System 7, …). GINI provides the emulator and points to "
        "where the image legally lives; you set Image to a disk image you own (and, for a 68k "
        "Mac, choose the Basilisk emulator and supply a Mac ROM). GINI hosts nothing "
        "copyrighted — you source the image, GINI runs it and embeds the screen over noVNC."),
}


def guide_for(key: str) -> str | None:
    """Return the teaching guide for an element type key, or None if not catalogued."""
    return GUIDE.get(key)
