"""Missions — the Wizard's objective layer.

The Wizard is "X-ray with a goal." A student states an objective in plain words ("a
multi-LAN IP network"); that becomes a **Mission**: the set of element types relevant to
the goal, plus a suggested first element. The canvas then filters X-ray to on-goal
neighbours, dims off-goal palette items, and flags off-goal drops.

This module is the deterministic, offline base (keyword → relevant types). When a local
LLM is connected the assistant refines the type set and restates the goal, but everything
works without a model — just more coarsely.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Mission:
    goal: str
    types: frozenset[str]            # element type_keys that belong to this objective
    first: str | None = None         # a good first element to drop

    def allows(self, type_key: str) -> bool:
        return type_key in self.types


# category -> (relevant element type_keys, anchor/first element, trigger keywords)
_CATS: tuple[tuple[str, set[str], str, tuple[str, ...]], ...] = (
    ("net", {"router", "switch", "hub", "host", "firewall", "wap", "cloud"}, "router",
     ("lan", "subnet", "router", "switch", "ethernet", "ip network", "ping", "route",
      "vlan", "gateway", "multi-lan", "broadcast", "network topology", "campus")),
    ("sdn", {"ovs", "controller", "host", "switch", "cloud"}, "controller",
     ("sdn", "openflow", "flow table", "ovs", "openvswitch", "software-defined",
      "software defined", "controller")),
    ("k8s", {"k8s_cluster", "pod", "instance_group", "registry", "load_balancer",
             "metrics", "dashboard", "load_generator"}, "k8s_cluster",
     ("kubernetes", "k8s", "pod", "orchestrat", "deployment", "autoscal", "hpa",
      "replica", "k3s")),
    ("cloudnet", {"vpc", "cloud_subnet", "security_group", "gateway", "region",
                  "instance", "web_app", "container"}, "vpc",
     ("vpc", "security group", "isolat", "private network", "availability zone",
      "subnet within", "tenancy")),
    ("web", {"web_app", "instance", "container", "load_balancer", "proxy", "gateway",
             "database", "nosql", "cache", "metrics", "dashboard", "load_generator",
             "registry"}, "web_app",
     ("web", "http", "microservice", "api", "backend", "frontend", "website", "web app",
      "web service", "three-tier", "3-tier", "scal", "load balanc")),
    ("serverless", {"function", "api_gateway", "queue", "object_store", "database"},
     "function",
     ("serverless", "function", "faas", "lambda", "event-driven", "pub/sub", "messaging",
      "queue")),
    ("data", {"database", "nosql", "cache", "object_store", "block_volume"}, "database",
     ("database", "sql", "postgres", "mongo", "cache", "redis", "object storage",
      "bucket", "datastore", "persist")),
    ("obs", {"metrics", "dashboard", "tracing"}, "metrics",
     ("monitor", "observab", "metric", "dashboard", "prometheus", "grafana", "trace",
      "telemetry")),
)


def _palette_keys() -> set[str]:
    from .devices import by_category
    return {d.key for items in by_category().values() for d in items}


# first-element preference when several categories match (most "anchoring" element wins)
_ANCHOR_PRIORITY = ("k8s_cluster", "controller", "router", "vpc", "web_app", "function",
                    "database", "metrics", "instance")


def _hit(keyword: str, goal: str) -> bool:
    """Match a keyword on a word boundary (a leading \\b, prefixes allowed) so 'wan' no
    longer matches 'want' and 'route' still matches 'router'."""
    return re.search(r"\b" + re.escape(keyword), goal) is not None


def keyword_mission(goal: str) -> Mission:
    """Deterministic objective → relevant element types (the offline base the LLM refines).
    Unrecognized goals impose no constraint (every palette element is on-goal)."""
    g = (goal or "").lower()
    types: set[str] = set()
    first: str | None = None
    for _key, tset, anchor, kws in _CATS:
        if any(_hit(kw, g) for kw in kws):
            types |= tset
            if first is None:
                first = anchor
    if not types:                                # nothing recognized -> don't constrain
        return Mission(goal.strip(), frozenset(_palette_keys()), None)
    return Mission(goal.strip(), frozenset(types), anchor_for(types) or first)


def anchor_for(types) -> str | None:
    """The best 'first element to drop' for a set of relevant types."""
    for k in _ANCHOR_PRIORITY:
        if k in types:
            return k
    return None


def refine_types(raw: str) -> set[str]:
    """Pull element type_keys from free LLM text (a lenient membership scan over the
    registry's keys and labels) — used to refine a mission when a model is connected."""
    from .devices import REGISTRY
    low = (raw or "").lower()
    hits: set[str] = set()
    for key, dt in REGISTRY.items():
        if key in low or f" {dt.label.lower()}" in f" {low}":
            hits.add(key)
    return hits
