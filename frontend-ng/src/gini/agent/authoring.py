"""Authoring loop — a teacher turns a rough intent into a releasable mission (M5).

The flow: **propose** (a vague intent → a composition-by-reference the teacher can read) →
**playtest** (assemble + validate + a dry-run report so nothing broken is released) → **to_pack**
(the YAML the Teaching Center distributes). The teacher is sovereign: the LLM drafts, the human
ratifies/tweaks the spec before releasing.

A proposal is a *composition* (references local fragment ids) — never new foundational fragments — so
whatever a teacher authors is gradable by construction on any install that has the referenced
fragments (guarded by the version/existence check in `domain.composition`).
"""
from __future__ import annotations

import yaml

from ..domain import composition as _comp
from ..domain import fragments as _frag
from ..domain import lesson as _lesson


def propose(intent_text: str, llm=None, *, lesson_id: str = "", genre: str | None = None) -> dict:
    """Draft a composition spec from a rough intent (reuses the student-facing composer, but emits a
    releasable REFERENCE spec — the core fragment ids + framing — not a one-off lesson)."""
    from . import lesson_resolver
    prop = lesson_resolver.compose(intent_text, llm, lesson_id=lesson_id or "authored", genre=genre)
    if prop is None or prop.lesson is None:
        return {}
    les = prop.lesson
    # reference only the CORE fragments the teacher chose; genre re-derives the enrichment on assembly
    cores = [fid for fid in les.fragments
             if (_frag.get(fid) is not None and _frag.get(fid).layer == _frag.CORE)]
    spec = {"id": lesson_id or "authored", "fragments": cores or [prop.archetype_id],
            "genre": les.genre or "expedition", "title": les.title,
            "brief": les.brief.split("] ", 1)[-1] if les.brief.startswith("[") else les.brief}
    if les.level is not None:
        spec["level"] = les.level
    return spec


def playtest(spec: dict) -> dict:
    """Assemble + validate a spec without releasing. Returns a report: {ok, problems, summary,
    objectives}. This is the safety gate — a teacher sees exactly what a student would be graded on."""
    problems = list(_comp.missing_refs(spec))
    les = None
    if not problems:
        try:
            les = _comp.from_composition(spec)
        except _comp.CompositionError as e:
            problems.append(str(e))
    if les is not None:
        problems.extend(_lesson.validate(les))
    return {
        "ok": not problems,
        "problems": problems,
        "summary": (f"{les.title} — {len(les.objectives)} objectives"
                    f" · L{les.level} · {les.genre}") if les else "",
        "objectives": [o.say for o in les.objectives] if les else [],
    }


def to_pack(spec: dict) -> str:
    """The releasable YAML pack (a composition-by-reference the Teaching Center serves)."""
    ordered = {k: spec[k] for k in ("id", "fragments", "genre", "level", "title", "brief",
                                    "time_limit", "attempts", "help", "persona") if k in spec}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100)


def ratify(spec: dict, **tweaks) -> dict:
    """Apply a teacher's edits (title, genre, level, fragments, brief…) to a proposed spec."""
    out = dict(spec)
    out.update({k: v for k, v in tweaks.items() if v is not None})
    return out
