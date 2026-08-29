"""The capability vocabulary — the typed interface that lets mission fragments COMPOSE.

Every fragment declares what it `provides` and `requires` in these role names; assembly then joins
fragments by matching one's provides to another's requires. Roles form a small **is-a hierarchy**
(a role may have several super-roles), so a fragment can require a *broad* role (loose — many
providers satisfy it) or a *specific* leaf (tight). `satisfies(provided, required)` is true when the
required role is the provided role or any of its ancestors — i.e. providing a `web-endpoint`
satisfies a requirement for a `traffic-sink`.

Pure data + two small helpers; no Qt, no LLM. This is the vocabulary drafted in
GINI_MISSIONS_COMPOSABLE_DESIGN.md §4 — prune here as we learn.
"""
from __future__ import annotations

# role -> its super-roles (is-a). Roots map to (). Multiple parents are allowed (e.g. a web tier is
# both a compute node and a traffic sink).
PARENTS: dict[str, tuple[str, ...]] = {
    # F1 · network fabric
    # `network` is the recursion hinge: a slot that requires a `network` is satisfied by a LAN
    # (l2-fabric) OR by another routed network (routed-network) — so routers nest to any depth.
    "network": (),
    "l2-fabric": ("network",),
    "switched-segment": ("l2-fabric",),
    "sdn-fabric": ("l2-fabric",),
    "routed-network": ("network",),
    "l3-gateway": (),
    "router-gateway": ("l3-gateway", "routed-network"),   # a router exposes the routed network it fronts
    "nat-gateway": ("l3-gateway",),
    "network-boundary": (),
    "vpc-boundary": ("network-boundary",),
    "subnet-boundary": ("network-boundary",),
    # F2 · edge / egress
    "internet-egress": (),
    "public-entrypoint": (),
    # F3 · compute / workload
    "compute-node": (),
    "host-node": ("compute-node",),
    "pod-workload": ("compute-node",),
    "serverless-fn": ("compute-node",),
    "orchestrated-compute": (),
    "web-tier": ("compute-node", "traffic-sink"),
    # F4 · traffic
    "traffic-source": (),
    "load-generator": ("traffic-source",),
    "request-client": ("traffic-source",),
    "traffic-sink": (),
    "web-endpoint": ("traffic-sink",),
    "api-endpoint": ("traffic-sink",),
    "service-endpoint": ("traffic-sink",),
    # F5 · steering / distribution
    "load-distributor": (),
    "inline-nf": (),
    "flow-control": (),
    # F6 · security / policy
    "access-policy": (),
    "perimeter-filter": ("access-policy",),
    "micro-segmentation": ("access-policy",),
    # F7 · data / state
    "datastore": (),
    "relational-store": ("datastore",),
    "cache-tier": ("datastore",),
    "object-store": ("datastore",),
    "message-broker": (),
    # F8 · observability
    "metrics-source": (),
    "metrics-collector": (),
    "visualizer": (),
    "dashboard-view": ("visualizer",),
    "flow-inspector": ("visualizer",),
    "packet-visualizer": ("visualizer",),
    # F9 · fault / perturbation
    "fault-injector": (),
    "link-break": ("fault-injector",),
    "misconfig": ("fault-injector",),
    "overload": ("fault-injector",),
    # F10 · operating systems (xv6 track) — standalone machine, no fabric
    "kernel-host": (),
    "workload-source": ("traffic-source",),      # a process/syscall stimulus (a "source" for the OS)
    "kernel-observer": ("visualizer",),
}

# Which domain each role belongs to (for presentation + so the composer knows the track). ONE
# namespace, tagged three ways — a role like compute-node is shared, which is what lets fragments
# compose ACROSS domains (a cloud app on a network, an OS reached over a link).
NETWORKING, CLOUD, OS, SHARED = "networking", "cloud", "os", "shared"
DOMAIN: dict[str, str] = {
    **{r: NETWORKING for r in (
        "network", "routed-network",
        "l2-fabric", "switched-segment", "sdn-fabric", "l3-gateway", "router-gateway", "nat-gateway",
        "network-boundary", "vpc-boundary", "subnet-boundary", "internet-egress", "public-entrypoint",
        "traffic-source", "load-generator", "request-client", "traffic-sink", "load-distributor",
        "inline-nf", "flow-control", "access-policy", "perimeter-filter", "micro-segmentation",
        "packet-visualizer", "flow-inspector", "fault-injector", "link-break", "misconfig", "overload")},
    **{r: CLOUD for r in (
        "compute-node", "host-node", "pod-workload", "serverless-fn", "orchestrated-compute",
        "web-tier", "web-endpoint", "api-endpoint", "service-endpoint", "datastore",
        "relational-store", "cache-tier", "object-store", "message-broker")},
    **{r: SHARED for r in ("metrics-source", "metrics-collector", "visualizer", "dashboard-view")},
    **{r: OS for r in ("kernel-host", "workload-source", "kernel-observer")},
}

# Device type → the capability roles it can PLAY. Multi-role is deliberate: it gives the composer
# more ways to satisfy a requirement (a host is both a compute-node and, when it pings, a source).
DEVICE_ROLES: dict[str, tuple[str, ...]] = {
    # networking fabric / edge
    "router": ("router-gateway",), "switch": ("switched-segment",), "hub": ("switched-segment",),
    "firewall": ("perimeter-filter",), "wap": ("switched-segment",), "vnf": ("inline-nf",),
    "cloud": ("internet-egress",), "ovs": ("sdn-fabric",), "controller": ("flow-control",),
    # compute
    "host": ("host-node",), "container": ("compute-node",), "instance": ("compute-node",),
    "kinstance": ("compute-node",), "web_app": ("web-endpoint", "compute-node"),
    "pod": ("pod-workload",), "k8s_cluster": ("orchestrated-compute",), "k8s_node": ("compute-node",),
    "instance_group": ("orchestrated-compute",),
    # cloud networking
    "vpc": ("vpc-boundary",), "cloud_subnet": ("subnet-boundary",),
    "security_group": ("micro-segmentation",), "gateway": ("nat-gateway",),
    "load_balancer": ("load-distributor",), "proxy": ("load-distributor",),
    # data / serverless / streaming
    "database": ("relational-store",), "nosql": ("datastore",), "cache": ("cache-tier",),
    "object_store": ("object-store",), "function": ("serverless-fn",),
    "api_gateway": ("api-endpoint", "public-entrypoint"),
    "queue": ("message-broker",), "stream": ("message-broker",), "messaging": ("message-broker",),
    # observability (sinks)
    "metrics": ("metrics-collector",), "dashboard": ("dashboard-view",), "tracing": ("visualizer",),
    # sources / sinks (riders)
    "ping_probe": ("request-client",), "http_probe": ("request-client",),
    "dns_probe": ("request-client",), "traceroute_probe": ("request-client",),
    "iperf_client": ("load-generator",), "load_generator": ("load-generator",),
    "iperf_server": ("traffic-sink",), "iface_stats": ("metrics-source",),
    "packet_view": ("packet-visualizer",),
    # operating systems (xv6)
    "xv6": ("kernel-host",), "terminal": ("kernel-observer",), "storage_volume": ("kernel-observer",),
    "xv6_shell": ("workload-source",), "xv6_workload": ("workload-source",),
}


def roles_for(device_key: str) -> tuple[str, ...]:
    """The capability roles a device type can play (may be several)."""
    return DEVICE_ROLES.get(device_key, ())


def domain_of(role: str) -> str:
    return DOMAIN.get(role, SHARED)


def is_role(name: str) -> bool:
    return name in PARENTS


def ancestors(role: str) -> set[str]:
    """All super-roles of `role`, inclusive of itself (walks the is-a DAG)."""
    seen: set[str] = set()
    stack = [role]
    while stack:
        r = stack.pop()
        if r in seen:
            continue
        seen.add(r)
        stack.extend(PARENTS.get(r, ()))
    return seen


def satisfies(provided: str, required: str) -> bool:
    """True when a fragment providing `provided` meets a requirement for `required` — i.e. the
    required role is the provided role or one of its ancestors (a specific provider satisfies a
    broad requirement)."""
    return required in ancestors(provided)


def any_satisfies(provided_roles, required: str) -> bool:
    """True when any of `provided_roles` satisfies the single `required` role."""
    return any(satisfies(p, required) for p in provided_roles)


def unknown_roles(roles) -> list[str]:
    """Roles not in the vocabulary (authoring safety net)."""
    return sorted({r for r in roles if r not in PARENTS})
