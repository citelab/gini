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
    "l2-fabric": (),
    "switched-segment": ("l2-fabric",),
    "sdn-fabric": ("l2-fabric",),
    "l3-gateway": (),
    "router-gateway": ("l3-gateway",),
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
}


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
