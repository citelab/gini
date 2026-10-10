"""Link properties: a routing cost and a failure model (docs/design/link-properties.md).

A link carries two things, and nothing else:

  cost          an ABSTRACT routing cost, integer 1-15, the same in both directions. Think price or
                policy preference. It has no relation to delay or bandwidth -- those stay on the
                router's ingress/egress delay lines -- and nothing in GINI derives one from the
                other. 1-15 is RIP's range: 16 is RIP's infinity, so a PATH costing 16 or more is
                unreachable to RIP. That is RIP's real rule and part of the lesson.
  fail_after    mean time to failure, seconds of running time, exponentially distributed; 0 = never
  repair_after  mean time to repair, seconds; 0 = a failed link stays down until restored by hand

Only values that differ from the defaults are stored, so a link nobody configured has empty
`props` and saves exactly as it did before link properties existed -- plain topologies keep their
proof-of-activity hashes, and their files still open in GINI from before Phase 0.

Pure: no Qt, no Docker. The inspector, the AI tools and the compiler all read and write through
here, so the rules live in one place.
"""
from __future__ import annotations

COST = "cost"
FAIL_AFTER = "fail_after"
REPAIR_AFTER = "repair_after"

DEFAULTS = {COST: 1, FAIL_AFTER: 0.0, REPAIR_AFTER: 0.0}
KEYS = tuple(DEFAULTS)

COST_MIN, COST_MAX = 1, 15
#: no failure clock longer than a day: a lab runs for minutes, and a typo of 120000 for 120 should
#: be caught rather than silently mean "never" in practice
TIME_MAX = 86400.0

#: The device types whose links have a routing cost: the ones the gRouter runs. Mirrors the
#: compiler's ROUTERS set (services/compiler.py); a test keeps the two in step.
ROUTER_TYPES = frozenset({"router", "firewall"})

LABELS = {
    COST: "Cost",
    FAIL_AFTER: "Fail after",
    REPAIR_AFTER: "Repair after",
}

HELP = {
    COST: ("Abstract routing cost, 1-15, the same both ways. Not delay and not bandwidth -- those "
           "are set on the routers. A path costing 16 or more is unreachable to RIP."),
    FAIL_AFTER: ("Mean time to failure in seconds of running time, drawn from an exponential "
                 "distribution. 0 = the link never fails."),
    REPAIR_AFTER: ("Mean time to repair in seconds, exponential. 0 = a failed link stays down "
                   "until you restore it."),
}


def normalize(key: str, value):
    """`value` as the property's type and range, or ValueError in words a student can act on."""
    if key not in DEFAULTS:
        raise ValueError(f"a link has no property {key!r} (it has: {', '.join(KEYS)})")
    if key == COST:
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"cost must be a whole number from {COST_MIN} to {COST_MAX}") from None
        if f != int(f) or not COST_MIN <= f <= COST_MAX:
            raise ValueError(f"cost must be a whole number from {COST_MIN} to {COST_MAX}, "
                             f"not {value!r}")
        return int(f)
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{LABELS[key].lower()} must be a number of seconds") from None
    if not 0.0 <= f <= TIME_MAX:
        raise ValueError(f"{LABELS[key].lower()} must be between 0 and {TIME_MAX:g} seconds, "
                         f"not {value!r}")
    return f


def get(link, key: str):
    """The property's value on `link`, or its default."""
    if key not in DEFAULTS:
        raise ValueError(f"a link has no property {key!r}")
    props = getattr(link, "props", None) or {}
    return props.get(key, DEFAULTS[key])


def values(link) -> dict:
    """Every property, defaults filled in."""
    return {k: get(link, k) for k in KEYS}


def set_prop(link, key: str, value):
    """Set one property, validated. A value equal to the default is removed rather than stored,
    so `props` only ever holds what someone chose. Returns the normalized value."""
    v = normalize(key, value)
    if v == DEFAULTS[key]:
        link.props.pop(key, None)
    else:
        link.props[key] = v
    return v


def clean(props) -> dict:
    """A props dict as read from a file or a staged spec: known keys normalized, defaults dropped.
    Unknown or invalid entries are dropped too -- a file from a newer GINI keeps those in the
    link's `extra` instead (see topology)."""
    out = {}
    for k, v in (props or {}).items():
        try:
            nv = normalize(k, v)
        except ValueError:
            continue
        if nv != DEFAULTS[k]:
            out[k] = nv
    return out


def is_costed(topology, link) -> bool:
    """Does this link's cost mean anything? Only on a network cable with a router on at least one
    end: a router's cost onto a LAN, or between two routers."""
    if getattr(link, "kind", "link") != "link":
        return False
    for end in (link.source_id, link.target_id):
        d = topology.devices.get(end)
        if d is not None and d.type_key in ROUTER_TYPES:
            return True
    return False


def can_fail(link) -> bool:
    """Any network cable can fail; a rider's "runs on" attachment is not a cable."""
    return getattr(link, "kind", "link") == "link"


def weighted(topology) -> bool:
    """Has anyone given any link a cost other than 1? The canvas labels every costed link once a
    topology is weighted, and none while it is not -- so a plain lab looks exactly as before."""
    return any(get(l, COST) != DEFAULTS[COST] for l in topology.links.values()
               if is_costed(topology, l))


def describe(link) -> str:
    """One line for a tooltip or the AI: `cost 4 · fails ≈ every 120 s, repaired ≈ 30 s`."""
    v = values(link)
    parts = [f"cost {v[COST]}"]
    if v[FAIL_AFTER] > 0:
        rep = (f", repaired after ≈ {v[REPAIR_AFTER]:g} s" if v[REPAIR_AFTER] > 0
               else ", stays down until restored")
        parts.append(f"fails after ≈ {v[FAIL_AFTER]:g} s{rep}")
    else:
        parts.append("never fails")
    return " · ".join(parts)
