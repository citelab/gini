"""Composition-by-reference — how the Teaching Center distributes missions (M4).

The agreed model: **foundational fragments are local** (bound to the local oracle's predicates); the
Center never ships new primitives. It ships **compositions** — a small spec that REFERENCES local
fragment ids plus framing (genre/level/title/brief), which the engine assembles locally against its
trusted primitives. This guarantees gradability (every objective comes from a verified local fragment)
and needs a **version/existence check**: a referenced fragment id (or capability role) that isn't
installed fails gracefully instead of mis-grading.

A composition spec (YAML/JSON):

    id: hw1
    fragments: [basic-lan, service-chain]      # local fragment ids
    genre: expedition                          # optional
    level: 2                                    # optional
    title: "Homework 1"                         # optional reskin
    brief: "Bridge two LANs to the Internet through a firewall."

An *escape hatch* is allowed: a spec with inline `objectives` (no `fragments`) is a self-contained
lesson — but its predicates are still bounded by what the local oracle can evaluate (validation).
"""
from __future__ import annotations


class CompositionError(ValueError):
    pass


def missing_refs(spec: dict) -> list[str]:
    """Referenced things the local install doesn't have — the version/existence check. Returns tags
    like 'fragment:<id>' / 'role:<name>'. Empty = the composition can be assembled here."""
    from . import capabilities as _caps
    from . import fragments as _frag
    miss: list[str] = []
    for fid in spec.get("fragments", []) or []:
        if _frag.get(fid) is None:
            miss.append(f"fragment:{fid}")
    for role in spec.get("requires_roles", []) or []:     # optional explicit role requirements
        if not _caps.is_role(role):
            miss.append(f"role:{role}")
    return miss


def from_composition(spec: dict, *, lesson_id: str | None = None):
    """Assemble a Lesson from a composition spec. Raises CompositionError if it references anything
    not installed locally (the version/existence guard)."""
    from . import assembly as _assembly
    from . import lesson as _lesson
    lid = lesson_id or spec.get("id") or "composed"

    if spec.get("fragments"):
        miss = missing_refs(spec)
        if miss:
            raise CompositionError(
                "this mission needs pieces this GINI doesn't have: " + ", ".join(miss)
                + " — update GINI or ask your instructor for a compatible version.")
        over = {k: spec[k] for k in ("time_limit", "attempts", "help", "persona", "complete_when")
                if k in spec}
        # the teacher's plain-language nuance rides along with the mission and is interpreted by the
        # student's game master — so the course server never needs a model of its own
        intent = dict(spec.get("intent") or {})
        if spec.get("notes"):
            intent.setdefault("notes", spec["notes"])
        if intent:
            over["intent"] = intent
        # A teacher's composition is LITERAL: the student gets exactly the fragments referenced —
        # never auto-filled exercise/observe layers they didn't ask for. (Want a load generator?
        # Reference `drive-load`.) Opt in explicitly with `fill: true`.
        return _assembly.assemble(
            list(spec["fragments"]), genre=spec.get("genre"), level=spec.get("level"),
            lesson_id=lid, title=spec.get("title", ""), brief=spec.get("brief", ""),
            persona=spec.get("persona", "coach"), fill=bool(spec.get("fill", False)), **over)

    # escape hatch: a self-contained lesson (inline objectives), still bounded by the local oracle
    if spec.get("objectives"):
        d = dict(spec)
        d.setdefault("id", lid)
        les = _lesson.from_dict(d)
        problems = _lesson.validate(les)
        if problems:
            raise CompositionError("lesson does not validate locally: " + "; ".join(problems))
        return les

    raise CompositionError("composition has neither 'fragments' nor 'objectives'")


def is_composition(spec) -> bool:
    return isinstance(spec, dict) and bool(spec.get("fragments") or spec.get("objectives"))
