"""Move legality & relevance for Missions — the deterministic layer behind the red "that's wrong"
flag. Fast (no model), so the flag is INSTANT and reliable; the game master supplies the spoken
reasoning separately (async).

Three kinds of flag, all non-destructive (nothing is ever deleted — the flag clears when the
student fixes it):

  • off-task element — a placed element whose TYPE is outside the mission's relevant family. The
    family is generous (the taught concept's elements + the types the objectives mention + all
    their grammar-legal neighbours), so a SUPERSET of the requirement is welcome — more hosts,
    switches, routers are fine on a LAN mission. Only genuinely off-domain drops (a K8s cluster in
    a switching lesson) fall out here. That superset-vs-off-domain distinction is the reasoning.
  • illegal link — a connection the grammar says can't exist.
  • forbid violation — a lesson `forbid:` rule (a predicate that must stay false) that got tripped
    (e.g. `link(database, cloud)` — the DB wired to the Internet).

Pure logic; evaluated against the live topology / an objectives World.
"""
from __future__ import annotations

from . import concepts as _concepts
from . import connection_rules as _cr
from .objectives import PredicateError, element_types_in_check, evaluate_check

# grouping boxes are scaffolding, never "off-task"
_CONTAINERS = {"vpc", "cloud_subnet", "region"}


def relevant_types(lesson) -> set[str]:
    """The element types that belong in this mission (generous — supersets welcome)."""
    types: set[str] = set(_CONTAINERS)
    concept = getattr(getattr(lesson, "intent", None), "concept", "") or ""
    c = _concepts.by_key(concept)
    if c is not None:
        types.update(c.elements)
    for o in getattr(lesson, "objectives", []):
        if getattr(o, "check", ""):
            types.update(element_types_in_check(o.check))
    for f in getattr(lesson, "forbid", []):
        chk = getattr(f, "check", "") or ""
        if chk:
            types.update(element_types_in_check(chk))
    # a legit grammar-neighbour of an in-family element is in-domain too (e.g. an Internet for a
    # router-based lesson) — this is what makes the family reason about the domain, not a fixed list
    for tk in list(types):
        types.update(_cr.partner_types(tk))
    return types


def off_task_devices(lesson, topology) -> list[str]:
    """Device ids whose type is outside the relevant family (candidate off-task flags)."""
    fam = relevant_types(lesson)
    return [d.id for d in topology.devices.values()
            if getattr(d, "type_key", "") and d.type_key not in fam]


def illegal_links(topology) -> list[str]:
    """Link ids whose endpoints the connection grammar says shouldn't connect."""
    out: list[str] = []
    for l in topology.links.values():
        s = topology.devices.get(l.source_id)
        t = topology.devices.get(l.target_id)
        if s is None or t is None:
            continue
        if t.type_key not in _cr.partner_types(s.type_key):
            out.append(l.id)
    return out


def forbid_violations(lesson, world) -> list:
    """Forbid rules currently TRUE — a rule that must stay false was tripped."""
    out = []
    for f in getattr(lesson, "forbid", []):
        chk = getattr(f, "check", "") or ""
        try:
            if chk and evaluate_check(chk, world):
                out.append(f)
        except PredicateError:
            pass
    return out


def flags(lesson, topology, world=None) -> dict:
    """The full flag set for the current board: {device_id -> reason} for off-task devices +
    forbid-violation endpoints, plus illegal link ids. Drives the red badge/glow; recomputed on
    every canvas change (cheap, deterministic)."""
    from .objectives import TopologyWorld
    world = world or TopologyWorld(topology)
    dev_flags: dict[str, str] = {}
    for did in off_task_devices(lesson, topology):
        d = topology.devices.get(did)
        label = getattr(d, "type", None)
        name = getattr(label, "label", None) or getattr(d, "type_key", "element")
        dev_flags[did] = f"A {name} isn't part of this mission — remove it or you're off-task."
    bad_links = illegal_links(topology)
    for lid in bad_links:                        # flag both endpoints of a bad connection
        l = topology.links.get(lid)
        if l is None:
            continue
        for did in (l.source_id, l.target_id):
            dev_flags.setdefault(did, "This connection isn't allowed here — rewire it.")
    forbids = forbid_violations(lesson, world)
    return {"devices": dev_flags, "links": bad_links, "forbid": forbids}
