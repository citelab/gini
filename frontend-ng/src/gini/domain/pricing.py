"""GINI $ — the toy cloud-economics model behind the dashboard.

Every element you place is a "rented" cloud resource billed by the hour in GINI $ (a
pretend currency — purely pedagogical, so students feel pay-as-you-go without spending
real money). The dashboard accrues `rate_per_hr × elapsed` while a lab runs.

Rates are deliberately round numbers that *teach the relative cost of cloud building
blocks*: a managed database costs more than a plain VM, which costs more than a switch.
Override any rate in Settings → Pricing (persisted to ~/.gini).
"""
from __future__ import annotations

# Default rental rates in GINI $ per hour, keyed by element type_key.
DEFAULT_RATES: dict[str, float] = {
    # --- compute (you pay most for managed/large compute) ------------------- #
    "instance": 10.0,        # VM
    "kinstance": 14.0,       # Kata Instance — a real microVM, so it costs more than a VM image
    "container": 5.0,
    "host": 2.0,             # a plain Machine / end host
    # --- serverless (cheap, scale-to-zero; pay per use, not per idle hour) --- #
    "function": 1.0,         # a handler in the shared FaaS runtime
    "api_gateway": 5.0,      # a managed API front door (Traefik)
    # --- containers & kubernetes -------------------------------------------- #
    "pod": 5.0,              # a Deployment — billed per replica (scaling costs money)
    "k8s_cluster": 8.0,      # a managed control plane (k3s)
    "k8s_node": 8.0,         # a worker node (a VM)
    "instance_group": 2.0,   # Pod Autoscaler (HPA) — the autoscaling feature itself
    "registry": 3.0,         # an image registry
    # --- networking --------------------------------------------------------- #
    "router": 3.0,
    "ovs": 3.0,              # OpenFlow switch (a gRouter in OF mode)
    "controller": 4.0,       # SDN controller
    "firewall": 2.0,
    "switch": 1.0,
    "wap": 1.5,              # access point
    "hub": 0.5,
    "cloud": 1.0,            # the Internet element = a NAT gateway (small fee)
    # --- managed services (the expensive, "someone runs it for you" tier) --- #
    "database": 12.0,
    "nosql": 10.0,
    "object_store": 8.0,
    "stream": 9.0,
    "queue": 6.0,
    "messaging": 6.0,
    "cache": 4.0,
    "load_balancer": 5.0,
    "proxy": 3.0,
    "web_app": 4.0,
    # --- observability / workload ------------------------------------------ #
    "metrics": 5.0,          # Prometheus
    "dashboard": 5.0,        # Grafana
    "tracing": 5.0,          # Jaeger
    "load_generator": 2.0,
}

# Instance "size" tiers (like cloud instance types). level -> (label, vCPUs, mem_MB,
# cost_multiplier). Cost roughly doubles each step up, mirroring real cloud pricing.
SIZE_TIERS: dict[int, tuple[str, float, int, int]] = {
    1: ("S",  0.5,  256, 1),
    2: ("M",  1.0,  512, 2),
    3: ("L",  2.0, 1024, 4),
    4: ("XL", 4.0, 2048, 8),
}
SIZE_MIN, SIZE_MAX = 1, 4


def size_level(level) -> int:
    try:
        return max(SIZE_MIN, min(SIZE_MAX, int(level or 1)))
    except (TypeError, ValueError):
        return 1


def size_tier(level) -> tuple[str, float, int, int]:
    return SIZE_TIERS[size_level(level)]


def size_label(level) -> str:
    return size_tier(level)[0]


def size_cost_mult(level) -> int:
    return size_tier(level)[3]


def resizable(type_key: str) -> bool:
    """Which elements expose a size knob — things that run a real workload container
    and meaningfully have a capacity (compute + managed services). Not switches/hubs."""
    from ..services.cloud_catalog import is_service   # lazy: keep domain below services
    return is_service(type_key) or type_key in ("instance", "kinstance", "container", "host")


# Dashboard breakdown groups. Order is the display order.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "Compute": ("instance", "kinstance", "container", "host"),
    "Serverless": ("function", "api_gateway"),
    "Kubernetes": ("k8s_cluster", "pod", "k8s_node", "instance_group", "registry"),
    "Networking": ("router", "ovs", "controller", "firewall", "switch",
                   "wap", "hub", "cloud"),
    "Services": ("database", "nosql", "object_store", "stream", "queue",
                 "messaging", "cache", "load_balancer", "proxy", "web_app"),
    "Observability": ("metrics", "dashboard", "tracing", "load_generator"),
}
CATEGORY_ORDER = tuple(CATEGORIES.keys())
_CAT_OF = {tk: cat for cat, tks in CATEGORIES.items() for tk in tks}

# the type_keys that are actually billable (everything in a category).
BILLABLE = frozenset(_CAT_OF)

# Intentionally-free elements, listed so the coverage test can tell "deliberately not
# billed" from "someone added an element and forgot to price it" (the latter would make
# the meter under-count — the bug this guards against). Two kinds:
#   * grouping boundaries — visual regions, not rented resources;
#   * placeholders — elements with no real runtime yet (priced once they become real).
FREE = frozenset({
    "vpc", "cloud_subnet", "region",              # grouping boundaries
    "security_group", "gateway", "block_volume",  # not-yet-real placeholders
})


def category_of(type_key: str) -> str | None:
    return _CAT_OF.get(type_key)


def rate_of(type_key: str, overrides: dict | None = None) -> float:
    """GINI $/hr for a type, honoring user overrides from Settings → Pricing."""
    if overrides and type_key in overrides:
        try:
            return max(0.0, float(overrides[type_key]))
        except (TypeError, ValueError):
            pass
    return float(DEFAULT_RATES.get(type_key, 0.0))


def _int(v, default: int = 1) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def units(type_key: str, properties: dict | None) -> int:
    """How many billable units one element represents. A Pod is a Deployment of N
    replicas, so it bills per replica — scaling a workload up costs more, which is the
    whole point of Kubernetes/HPA. Everything else is a single unit."""
    if type_key == "pod":
        return max(1, _int((properties or {}).get("Replicas"), 1))
    return 1


def bill(topology, overrides: dict | None = None) -> dict:
    """Cost estimate for the whole canvas.

    Returns::

        {"rate_per_hr": float,           # GINI $/hr for every billable element
         "count": int,                   # number of billable elements
         "by_category": {cat: {"rate": float, "count": int}}}   # display order
    """
    by_cat: dict[str, dict] = {c: {"rate": 0.0, "count": 0} for c in CATEGORY_ORDER}
    total = 0.0
    n = 0
    for d in getattr(topology, "devices", {}).values():
        tk = getattr(d, "type_key", None)
        if tk not in BILLABLE:
            continue
        cat = _CAT_OF[tk]
        # bigger instance size = proportionally more GINI $/hr (x1/x2/x4/x8)
        mult = size_cost_mult(getattr(d, "size", 1)) if resizable(tk) else 1
        # a Pod bills per replica (scaling a workload costs more)
        qty = units(tk, getattr(d, "properties", None))
        r = rate_of(tk, overrides) * mult * qty
        by_cat[cat]["rate"] += r
        by_cat[cat]["count"] += 1
        total += r
        n += 1
    # drop empty categories so the strip stays compact
    by_cat = {c: v for c, v in by_cat.items() if v["count"]}
    return {"rate_per_hr": round(total, 2), "count": n, "by_category": by_cat}
