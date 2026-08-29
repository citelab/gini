"""Fragment YAML — read/write mission fragments as data (GINI_MISSIONS_COMPOSABLE_DESIGN.md §7).

This is the "content is data, behaviour is code" line made concrete: a *foundational fragment* is a
YAML file the local install ships (bound to the local oracle's predicates); the engine composes them.
The Teaching Center distributes compositions that REFERENCE these — never new foundational fragments
(a new primitive would need a predicate the local oracle can't run). See the loader's `validate`,
which refuses any fragment whose predicates don't parse, whose roles are unknown, or whose element
types aren't real — so a broken pack never reaches a student.
"""
from __future__ import annotations

import glob
import os

import yaml

from . import capabilities as _caps
from .objectives import check_ok, unknown_element_types

# `fragments` is imported lazily inside the functions below — importing this module must NOT trigger
# the fragments load (fragments imports us back to load its YAML packs; a top-level import would cycle).
_LAYERS = {"core", "exercise", "observe"}


def to_dict(frag: Fragment) -> dict:
    """A fragment → a clean dict for YAML (empty fields pruned)."""
    d: dict = {"id": frag.id, "layer": frag.layer}
    for k in ("teaches", "spirit", "summary", "parent", "step"):
        v = getattr(frag, k)
        if v:
            d[k] = v
    if frag.provides:
        d["provides"] = list(frag.provides)
    if frag.requires:
        d["requires"] = list(frag.requires)
    if frag.complete_when and frag.complete_when != "all":
        d["complete_when"] = frag.complete_when
    if frag.objectives:
        d["objectives"] = [_obj_to_dict(o) for o in frag.objectives]
    if frag.misconceptions:
        d["misconceptions"] = list(frag.misconceptions)
    if frag.peers:
        d["peers"] = list(frag.peers)
    if not frag.catalog:                     # standalone-mission by default; only note the exceptions
        d["catalog"] = False
    if frag.stage:
        d["stage"] = frag.stage
    if getattr(frag, "slots", ()):
        d["slots"] = [_slot_to_dict(s) for s in frag.slots]
    if getattr(frag, "peerings", ()):
        d["peerings"] = [_peering_to_dict(p) for p in frag.peerings]
    if frag.forks:
        d["forks"] = [_fork_to_dict(f) for f in frag.forks]
    for k in ("engine_version", "author"):   # provenance — only present on authored fragments
        v = getattr(frag, k)
        if v:
            d[k] = v
    if getattr(frag, "certified", False):    # the runtime-playtest stamp travels to the TC
        d["certified"] = True
    if frag.schema_version and frag.schema_version != 1:
        d["schema_version"] = frag.schema_version
    return d


def _slot_to_dict(s) -> dict:
    d = {"name": s.name, "role": s.role}
    if s.min != 1:
        d["min"] = s.min
    if s.max != 1:
        d["max"] = s.max
    if not s.distinct:
        d["distinct"] = False
    return d


def _slots_from(rows) -> tuple:
    from .fragments import Slot
    return tuple(Slot(name=s["name"], role=s["role"], min=int(s.get("min", 1)),
                      max=int(s.get("max", 1)), distinct=bool(s.get("distinct", True)))
                 for s in (rows or []))


def _peering_to_dict(p) -> dict:
    d = {"name": p.name, "role": p.role, "topology": p.topology}
    if p.min != 2:
        d["min"] = p.min
    if p.max != 0:
        d["max"] = p.max
    return d


def _peerings_from(rows) -> tuple:
    from .fragments import Peering
    return tuple(Peering(name=p["name"], role=p["role"], min=int(p.get("min", 2)),
                         max=int(p.get("max", 0)), topology=p.get("topology", "mesh"))
                 for p in (rows or []))


def _fork_to_dict(f) -> dict:
    d = {"id": f.id}
    if f.label:
        d["label"] = f.label
    d["difficulty"] = f.difficulty
    d["kind"] = f.kind
    d["objectives"] = [_obj_to_dict(o) for o in f.objectives]
    return d


def _obj_to_dict(o: ObjectiveTemplate) -> dict:
    d = {"id": o.id, "say": o.say, "check": o.check}
    if o.kind and o.kind != "structural":
        d["kind"] = o.kind
    if o.probe:
        d["probe"] = o.probe
    if o.level:
        d["level"] = o.level
    if getattr(o, "stars", 0):
        d["stars"] = o.stars
    if not o.check:
        d.pop("check")
    return d


def _objs_from(rows) -> tuple:
    from .fragments import ObjectiveTemplate
    return tuple(ObjectiveTemplate(id=o["id"], say=o.get("say", o["id"]),
                                   kind=o.get("kind", "structural"),
                                   check=o.get("check", ""), probe=o.get("probe", ""),
                                   level=o.get("level"), stars=int(o.get("stars", 0) or 0))
                 for o in (rows or []))


def fragment_from_dict(d: dict):
    from .fragments import CONVERGE, Fork, Fragment
    forks = tuple(Fork(id=f["id"], label=f.get("label", ""),
                       difficulty=int(f.get("difficulty", 1)), kind=f.get("kind", CONVERGE),
                       objectives=_objs_from(f.get("objectives")))
                  for f in (d.get("forks", []) or []))
    return Fragment(
        id=d["id"], layer=d.get("layer", "core"), teaches=d.get("teaches", ""),
        spirit=d.get("spirit", ""), summary=d.get("summary", ""),
        provides=tuple(d.get("provides", []) or ()), requires=tuple(d.get("requires", []) or ()),
        objectives=_objs_from(d.get("objectives")),
        misconceptions=tuple(d.get("misconceptions", []) or ()),
        complete_when=d.get("complete_when", "all"), parent=d.get("parent", ""),
        peers=tuple(d.get("peers", []) or ()), step=d.get("step", ""),
        catalog=bool(d.get("catalog", True)), stage=dict(d.get("stage", {}) or {}),
        slots=_slots_from(d.get("slots")), peerings=_peerings_from(d.get("peerings")),
        forks=forks, engine_version=str(d.get("engine_version", "")),
        schema_version=int(d.get("schema_version", 1)), author=str(d.get("author", "")),
        certified=bool(d.get("certified", False)))


def to_yaml(frag: Fragment) -> str:
    return yaml.safe_dump(to_dict(frag), sort_keys=False, allow_unicode=True, width=100)


def from_yaml(text: str) -> Fragment:
    return fragment_from_dict(yaml.safe_load(text))


def validate(frag: Fragment) -> list[str]:
    """Problems that make a fragment unloadable (empty = good)."""
    problems: list[str] = []
    if not frag.id:
        problems.append("fragment missing id")
    if frag.layer not in _LAYERS:
        problems.append(f"bad layer {frag.layer!r}")
    bad_roles = _caps.unknown_roles(list(frag.provides) + list(frag.requires))
    if bad_roles:
        problems.append(f"unknown capability roles {bad_roles}")
    from .fragments import CONVERGE, DIVERGE
    seen: set[str] = set()
    _validate_objectives(frag.objectives, seen, problems, "objective")
    for fk in frag.forks:                        # forks are validated exactly like the core ladder
        if fk.kind not in (CONVERGE, DIVERGE):
            problems.append(f"fork {fk.id!r}: bad kind {fk.kind!r}")
        if not fk.objectives:
            problems.append(f"fork {fk.id!r} has no objectives")
        _validate_objectives(fk.objectives, seen, problems, f"fork {fk.id!r} objective")
    return problems


def _validate_objectives(objs, seen: set, problems: list, label: str) -> None:
    from .probes import probe_ok
    for o in objs:
        if o.id in seen:
            problems.append(f"duplicate objective id {o.id!r}")
        seen.add(o.id)
        if o.kind == "structural" and o.check:
            if not check_ok(o.check):
                problems.append(f"{label} {o.id!r}: check does not parse: {o.check!r}")
            else:
                bad = unknown_element_types(o.check)
                if bad:
                    problems.append(f"{label} {o.id!r}: unknown element types {bad}")
        elif o.kind == "behavioral":
            if not o.probe:
                problems.append(f"behavioral {label} {o.id!r} has no probe")
            elif not probe_ok(o.probe):
                problems.append(f"{label} {o.id!r}: probe does not parse: {o.probe!r}")


def load_dir(path: str, *, strict: bool = True):
    """Load + validate every *.yaml fragment in a directory.

    Returns (fragments, warnings). `strict=True` (the system layer) raises on the first invalid pack —
    a broken built-in is a bug. `strict=False` (the user / OTA layer) DECLINES a bad pack and keeps
    going, collecting a warning — a broken authored/pulled fragment must never brick the client.
    """
    out: dict[str, Fragment] = {}
    warnings: list[str] = []
    if not os.path.isdir(path):
        return out, warnings
    for fp in sorted(glob.glob(os.path.join(path, "*.yaml"))):
        name = os.path.basename(fp)
        try:
            frag = fragment_from_dict(yaml.safe_load(open(fp, encoding="utf-8").read()))
            problems = validate(frag)
        except Exception as e:                   # noqa: BLE001 — malformed YAML is just "declined"
            problems = [f"could not read: {e}"]
            frag = None
        if problems:
            msg = f"{name}: {'; '.join(problems)}"
            if strict:
                raise ValueError(msg)
            warnings.append(msg)                 # refuse this one, keep the rest
            continue
        out[frag.id] = frag
    return out, warnings
