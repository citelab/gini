"""Connection grammar — what can wire to what, and *why*.

A single declarative table of the meaningful connections between element types, each
with a one-line, student-facing reason and whether the link is *required* for one side
to do its job (e.g. a Pod needs a Cluster to run in). This is pure data with no Qt or
compiler dependency so every layer can share one source of truth:

  • **X-ray** (long-press a node) highlights its compatible partners + shows the *why*.
  • **Explain** can answer "what can I connect this to, and what's missing?".
  • **Wizard / lint** can validate a topology and suggest the next element.

The grammar is intentionally *advisory*, not a hard constraint: gBuilder still lets a
student wire anything (mistakes are teachable). It just makes the good paths visible.

Edges are undirected for matching purposes; the `why` is phrased to read sensibly from
either end. `requires` names the element type(s) that genuinely *need* such an edge.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import devices as _dev

# Reusable groups (expanded into concrete edges at import). Keep these aligned with the
# device registry keys.
GROUPS: dict[str, tuple[str, ...]] = {
    "WORKLOAD": ("web_app", "instance", "container"),       # VM-style cloud app runtimes
    "BACKEND": ("web_app", "instance", "container", "pod"),  # things traffic targets
    "DATASTORE": ("database", "nosql", "cache"),
    # anything that runs app code — a Pod is a real workload too, so it can use data
    # stores, emit metrics/traces, and talk to streams/queues just like a Web App.
    "APPISH": ("web_app", "instance", "container", "function", "pod"),
}

# Each row: (a, b, why, requires). `a`/`b` may be a GROUP name (uppercase). `requires` is
# a tuple of type_keys (or group names) for which this edge is *required* to function.
_SPEC: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # ---- classic networking ---------------------------------------------- #
    ("host", "switch", "Join a LAN — a switch links machines on the same subnet.", ()),
    ("host", "hub", "Share one collision domain (teaching: watch flooding & collisions).", ()),
    ("host", "router", "Attach a machine straight to a router as its gateway off the LAN.", ()),
    ("host", "wap", "Join the network over Wi-Fi.", ()),
    ("switch", "router", "Uplink the LAN to a router so it can reach other subnets.", ()),
    ("switch", "switch", "Extend the LAN onto a second switch.", ()),
    ("hub", "switch", "Bridge a shared-media segment into a switched LAN.", ()),
    ("wap", "switch", "Uplink the access point into the wired LAN.", ()),
    ("router", "router", "Link routers for multi-hop routing between subnets.", ()),
    ("router", "cloud", "Reach the public Internet through this router.", ()),
    ("router", "firewall", "Filter traffic between trust zones with a firewall.", ()),
    ("firewall", "cloud", "Guard the boundary between the LAN and the Internet.", ()),

    # ---- NFV / service function chaining --------------------------------- #
    # A VNF is an inline network function: it sits BETWEEN two elements in the path, so it
    # wires to hosts, switches, routers, the OVS, the Internet, and other VNFs (a chain).
    ("vnf", "host", "Put this network function inline in front of a machine.", ()),
    ("vnf", "switch", "Insert this network function on the path through a switch.", ()),
    ("vnf", "router", "Chain this network function to a router in the path.", ()),
    ("vnf", "ovs", "Attach this VNF to the SDN switch (steer flows through it).", ()),
    ("vnf", "firewall", "Chain this VNF next to a firewall in the path.", ()),
    ("vnf", "cloud", "Put this network function at the edge, before the Internet.", ()),
    ("vnf", "vnf", "Chain VNFs in series — a Service Function Chain (e.g. firewall → IDS).", ()),

    # ---- software-defined networking ------------------------------------- #
    ("ovs", "controller", "An OpenFlow switch needs a controller to program its flow rules.",
     ("ovs",)),
    ("ovs", "host", "Attach end machines to the SDN switch.", ()),
    ("ovs", "ovs", "Build a multi-switch SDN fabric.", ()),

    # ---- containers & Kubernetes ----------------------------------------- #
    ("pod", "k8s_cluster", "A Pod needs a Cluster to run in — this is where it deploys.",
     ("pod",)),
    ("pod", "instance_group", "Add a Pod Autoscaler (HPA) to scale this Pod's replicas on CPU.",
     ("instance_group",)),
    ("pod", "registry", "Pull the Pod's image from a private registry.", ()),
    ("k8s_cluster", "registry", "Serve images to the cluster's workloads from a registry.", ()),

    # ---- traffic into backends (LB / proxy / load-gen) ------------------- #
    ("load_balancer", "BACKEND", "Distribute incoming traffic across this backend.", ()),
    ("proxy", "BACKEND", "Front this service with a reverse proxy (routing / TLS).", ()),
    ("load_generator", "BACKEND", "Fire HTTP load at this app to test it under traffic.", ()),
    ("load_balancer", "proxy", "Chain a load balancer in front of a reverse proxy.", ()),

    # ---- apps + their data (a Pod is an app too, so it's a BACKEND here) -- #
    ("BACKEND", "DATASTORE", "Back this app with a database / cache for its state.", ()),
    ("BACKEND", "object_store", "Store & serve files / objects for this app.", ()),
    ("instance", "block_volume", "Attach a persistent disk to this instance.",
     ("block_volume",)),

    # ---- cloud networking ------------------------------------------------ #
    ("vpc", "cloud_subnet", "A VPC is divided into subnets — place this subnet inside it.",
     ("cloud_subnet",)),
    ("cloud_subnet", "WORKLOAD", "Place this workload inside the subnet.", ()),
    ("security_group", "WORKLOAD", "Attach a stateful firewall to this workload.", ()),
    ("security_group", "DATASTORE", "Lock down this datastore — allow only the tiers you list.", ()),
    ("gateway", "vpc", "Give the VPC outbound Internet through this gateway.", ()),
    ("gateway", "cloud", "The gateway faces the public Internet.", ()),

    # ---- serverless ------------------------------------------------------ #
    ("api_gateway", "function", "Route a URL path to this function (the front door).", ()),
    ("function", "DATASTORE", "Read & write state from the function (it's stateless itself).", ()),
    ("function", "object_store", "Read & write objects from the function.", ()),
    ("function", "queue", "Trigger the function from queue messages (event-driven).", ()),
    ("function", "stream", "Trigger the function from an event stream.", ()),
    ("function", "messaging", "Trigger the function from pub/sub messages.", ()),
    ("api_gateway", "web_app", "Front a web service with a managed API.", ()),
    ("load_generator", "api_gateway", "Fire HTTP load at the gateway to watch functions scale.", ()),
    ("load_generator", "function", "Invoke the function under load to test it.", ()),
    ("metrics", "function", "Scrape invocation metrics from the function runtime.", ()),
    ("metrics", "api_gateway", "Scrape request metrics from the API gateway.", ()),
    ("queue", "WORKLOAD", "Pass messages asynchronously between services.", ()),

    # ---- VM-vs-container experiment (Kata Instance) ---------------------- #
    # A deliberately RESTRICTED set: a Kata Instance is a VM-isolated workload used only
    # for the isolation experiment, so it wires to a load source, a backend, and metrics —
    # never k8s, the networking plane, or VPCs (it can't run those / stays flat).
    ("load_generator", "kinstance", "Fire HTTP load at the VM workload to measure it.", ()),
    ("load_balancer", "kinstance", "Fan traffic across several VM workloads.", ()),
    ("kinstance", "DATASTORE", "Back the VM workload with a database / cache.", ()),
    ("kinstance", "object_store", "Read & write objects from the VM workload.", ()),
    ("metrics", "kinstance", "Scrape metrics from the VM workload.", ()),
    ("kinstance", "kinstance", "Build a multi-VM topology to compare against containers.", ()),

    # ---- streaming & messaging ------------------------------------------- #
    ("stream", "APPISH", "Produce / consume an event log with this service.", ()),
    ("messaging", "APPISH", "Publish / subscribe messages with this service.", ()),

    # ---- observability --------------------------------------------------- #
    ("metrics", "BACKEND", "Scrape metrics from this target.", ()),
    ("metrics", "proxy", "Scrape request metrics from the proxy.", ()),
    ("metrics", "load_balancer", "Scrape metrics from the load balancer.", ()),
    ("dashboard", "metrics", "A dashboard needs a metrics source to visualize.", ("dashboard",)),
    ("tracing", "APPISH", "Collect distributed traces from this service.", ()),

    # ---- xv6 peripherals (xv6 has NO networking; you attach software devices) --- #
    ("xv6", "terminal", "Attach a Terminal to type commands and watch xv6's console.", ()),
    ("xv6", "storage_volume", "Attach the xv6 disk to inspect its file system.", ()),
)


# xv6 has no network stack, so unlike the advisory grammar above these ARE hard constraints:
# an xv6 Machine wires ONLY to its peripherals, and a peripheral wires ONLY to an xv6 Machine.
_XV6_PERIPHERALS = frozenset({"terminal", "storage_volume"})


# --------------------------------------------------------------------------- #
# Attach grammar: riders (Sources / Sinks) mount onto a donor, they don't wire.
# --------------------------------------------------------------------------- #
def is_rider(type_key: str) -> bool:
    dt = _dev.REGISTRY.get(type_key)
    return bool(dt and getattr(dt, "rider", False))


def attach_targets(rider_type: str) -> tuple[str, ...]:
    """The donor type_keys a rider may ride (empty if it isn't a rider)."""
    dt = _dev.REGISTRY.get(rider_type)
    return tuple(getattr(dt, "attaches_to", ()) or ()) if dt else ()


def riders_for(donor_type: str) -> list[str]:
    """Every rider type that can mount on this donor (drives X-ray from the donor's side)."""
    return sorted(k for k, dt in _dev.REGISTRY.items()
                  if getattr(dt, "rider", False)
                  and donor_type in (getattr(dt, "attaches_to", ()) or ()))


def attach_blocked(rider_type: str, donor_type: str) -> str | None:
    """A reason to REJECT mounting `rider_type` on `donor_type`, or None to allow it."""
    if not is_rider(rider_type):
        return "Only a Source or Sink can be attached — wire the rest as network links."
    if is_rider(donor_type):
        return "A donor can't be another Source/Sink — attach the rider to a Machine, Router, …"
    if donor_type not in attach_targets(rider_type):
        tgts = ", ".join(attach_targets(rider_type)) or "a compatible element"
        return f"A {rider_type} attaches to {tgts} — not {donor_type}."
    return None


def link_blocked(a: str, b: str) -> str | None:
    """A reason to REJECT an a–b link (a HARD constraint), or None to allow it."""
    # A Source/Sink rides its donor with a dotted ATTACH edge, never a network cable.
    for x, other in ((a, b), (b, a)):
        if is_rider(x):
            return (f"A {x} is a Source/Sink — attach it to its donor (dotted), don't wire it as "
                    f"a network link.")
    if a == "xv6" or b == "xv6":
        other = b if a == "xv6" else a
        if other != "xv6" and other not in _XV6_PERIPHERALS:
            return ("xv6 has no networking — attach a Screen, Keyboard, or Storage Volume "
                    "instead of a network connection.")
    if a in _XV6_PERIPHERALS or b in _XV6_PERIPHERALS:
        other = b if a in _XV6_PERIPHERALS else a
        if other != "xv6":
            return "An xv6 peripheral attaches only to an xv6 Machine."
    return None


@dataclass(frozen=True)
class Partner:
    """A valid connection partner for some element, with its teaching reason."""
    type_key: str
    why: str
    required: bool        # True if the *queried* element needs at least one such link


def _expand(name: str) -> tuple[str, ...]:
    return GROUPS.get(name, (name,))


# adjacency: type_key -> {partner_type: (why, requires_set)}; built once at import.
_ADJ: dict[str, dict[str, tuple[str, frozenset]]] = {}


def _add(a: str, b: str, why: str, requires: frozenset) -> None:
    cur = _ADJ.setdefault(a, {}).get(b)
    # keep the first reason but union requirements (a pair may appear via several rows)
    if cur is None:
        _ADJ[a][b] = (why, requires)
    else:
        _ADJ[a][b] = (cur[0], cur[1] | requires)


for _a, _b, _why, _req in _SPEC:
    _reqset = frozenset(t for r in _req for t in _expand(r))
    for ea in _expand(_a):
        for eb in _expand(_b):
            _add(ea, eb, _why, _reqset)
            if ea != eb:
                _add(eb, ea, _why, _reqset)


def partners_for(type_key: str) -> list[Partner]:
    """Every element type `type_key` can meaningfully connect to, required ones first."""
    out = [Partner(p, why, type_key in req)
           for p, (why, req) in _ADJ.get(type_key, {}).items()]
    out.sort(key=lambda p: (not p.required, p.type_key))
    return out


def partner_types(type_key: str) -> set[str]:
    """Just the set of compatible partner type keys (for fast highlight tests)."""
    return set(_ADJ.get(type_key, {}).keys())


def can_connect(a: str, b: str) -> str | None:
    """The teaching reason for an a–b link, or None if it isn't a recommended connection."""
    edge = _ADJ.get(a, {}).get(b)
    return edge[0] if edge else None


def required_partners(type_key: str) -> list[Partner]:
    """Partner types this element *needs* at least one of to function."""
    return [p for p in partners_for(type_key) if p.required]


def missing_required(type_key: str, neighbor_types) -> list[Partner]:
    """Required partners that are absent from `neighbor_types` (drives lint / hints)."""
    have = set(neighbor_types)
    return [p for p in required_partners(type_key) if p.type_key not in have]
