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
    return d


def _obj_to_dict(o: ObjectiveTemplate) -> dict:
    d = {"id": o.id, "say": o.say, "check": o.check}
    if o.kind and o.kind != "structural":
        d["kind"] = o.kind
    if o.probe:
        d["probe"] = o.probe
    if o.level:
        d["level"] = o.level
    if not o.check:
        d.pop("check")
    return d


def fragment_from_dict(d: dict):
    from .fragments import Fragment, ObjectiveTemplate
    objs = tuple(ObjectiveTemplate(id=o["id"], say=o.get("say", o["id"]),
                                   kind=o.get("kind", "structural"),
                                   check=o.get("check", ""), probe=o.get("probe", ""),
                                   level=o.get("level"))
                 for o in (d.get("objectives", []) or []))
    return Fragment(
        id=d["id"], layer=d.get("layer", "core"), teaches=d.get("teaches", ""),
        spirit=d.get("spirit", ""), summary=d.get("summary", ""),
        provides=tuple(d.get("provides", []) or ()), requires=tuple(d.get("requires", []) or ()),
        objectives=objs, misconceptions=tuple(d.get("misconceptions", []) or ()),
        complete_when=d.get("complete_when", "all"), parent=d.get("parent", ""),
        peers=tuple(d.get("peers", []) or ()), step=d.get("step", ""),
        catalog=bool(d.get("catalog", True)), stage=dict(d.get("stage", {}) or {}))


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
    seen = set()
    for o in frag.objectives:
        if o.id in seen:
            problems.append(f"duplicate objective id {o.id!r}")
        seen.add(o.id)
        if o.kind == "structural" and o.check:
            if not check_ok(o.check):
                problems.append(f"objective {o.id!r}: check does not parse: {o.check!r}")
            else:
                bad = unknown_element_types(o.check)
                if bad:
                    problems.append(f"objective {o.id!r}: unknown element types {bad}")
        elif o.kind == "behavioral":
            from .probes import probe_ok
            if not o.probe:
                problems.append(f"behavioral objective {o.id!r} has no probe")
            elif not probe_ok(o.probe):
                problems.append(f"objective {o.id!r}: probe does not parse: {o.probe!r}")
    return problems


def load_dir(path: str) -> dict[str, Fragment]:
    """Load + validate every *.yaml fragment in a directory. Raises on the first invalid pack."""
    out: dict[str, Fragment] = {}
    for fp in sorted(glob.glob(os.path.join(path, "*.yaml"))):
        frag = fragment_from_dict(yaml.safe_load(open(fp, encoding="utf-8").read()))
        problems = validate(frag)
        if problems:
            raise ValueError(f"{os.path.basename(fp)}: {'; '.join(problems)}")
        out[frag.id] = frag
    return out
