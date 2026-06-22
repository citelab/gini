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
    "container": 5.0,
    "host": 2.0,             # a plain Machine / end host
    "function": 1.0,         # serverless — cheap, scale-to-zero
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
    "registry": 3.0,
    # --- observability / workload ------------------------------------------ #
    "metrics": 5.0,          # Prometheus
    "dashboard": 5.0,        # Grafana
    "tracing": 5.0,          # Jaeger
    "load_generator": 2.0,
}

# Dashboard breakdown groups. Order is the display order.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "Compute": ("instance", "container", "host", "function"),
    "Networking": ("router", "ovs", "controller", "firewall", "switch",
                   "wap", "hub", "cloud"),
    "Services": ("database", "nosql", "object_store", "stream", "queue",
                 "messaging", "cache", "load_balancer", "proxy", "web_app",
                 "registry"),
    "Observability": ("metrics", "dashboard", "tracing", "load_generator"),
}
CATEGORY_ORDER = tuple(CATEGORIES.keys())
_CAT_OF = {tk: cat for cat, tks in CATEGORIES.items() for tk in tks}

# the type_keys that are actually billable (everything in a category). Grouping
# elements (VPC/region boundaries) and unknown types are free and skipped.
BILLABLE = frozenset(_CAT_OF)


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
        r = rate_of(tk, overrides)
        by_cat[cat]["rate"] += r
        by_cat[cat]["count"] += 1
        total += r
        n += 1
    # drop empty categories so the strip stays compact
    by_cat = {c: v for c, v in by_cat.items() if v["count"]}
    return {"rate_per_hr": round(total, 2), "count": n, "by_category": by_cat}
