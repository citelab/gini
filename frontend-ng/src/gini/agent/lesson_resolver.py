"""Lesson resolver — the "teacher can be vague" path.

Because the arena is bounded, the games are pre-authored (the Game Catalog). So a professor need
not hand-write objectives: they express intent loosely ("teach keeping a database private, ~25
min"), the resolver **matches it to an archetype and proposes a concrete Lesson**, and the
professor **ratifies** (approves/tweaks). This keeps the professor sovereign — the AI drafts, the
human disposes.

Two stages, cheap-first:
  1. a lexical prefilter shortlists candidate archetypes (fast, deterministic);
  2. a reasoning LLM picks the best archetype from the shortlist, fills its params, and suggests a
     time limit / title (the reasoning step). With no model, the resolver degrades to the top
     lexical candidate + demo params — still a usable proposal to ratify.

Returns a `Proposal` (never auto-releases). Pure except the injected `llm`, so it's testable with
a scripted model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..domain import catalog as _catalog
from ..domain import lexicon as _lex
from ..domain import lesson as _lesson


@dataclass
class Proposal:
    archetype_id: str
    params: dict
    lesson: object                       # domain.lesson.Lesson
    rationale: str = ""
    candidates: list = field(default_factory=list)   # shortlisted archetype ids (for the UI)


def _archetype_text(a) -> str:
    return " ".join((a.id.replace("-", " "), a.summary, a.spirit, a.teaches.replace("-", " ")))


def shortlist(intent_text: str, k: int = 5) -> list:
    """Lexically rank archetypes against the teacher's intent (the cheap prefilter)."""
    want = set(_lex.normalize(intent_text, query=True))
    scored = []
    for a in _catalog.all_archetypes():
        toks = set(_lex.normalize(_archetype_text(a)))
        overlap = len(want & toks)
        if overlap:
            scored.append((overlap, a))
    scored.sort(key=lambda s: -s[0])
    picks = [a for _, a in scored[:k]]
    return picks or list(_catalog.all_archetypes())[:k]


_PROMPT = (
    "A teacher wants a lab. Their intent: {intent!r}. Choose the best-fitting game from this "
    "catalog and fill its parameters with short device names.\n{menu}\n"
    "Reply ONLY as JSON: {{\"archetype\": \"<id>\", \"params\": {{<ref>: <name>, ...}}, "
    "\"time_limit\": \"<e.g. 25m>\", \"title\": \"<short title>\"}}. No prose."
)


def _menu(cands) -> str:
    lines = []
    for a in cands:
        refs = ", ".join(a.params)
        lines.append(f"- {a.id} (params: {refs}) — {a.summary}")
    return "Catalog:\n" + "\n".join(lines)


def _first_json(text: str):
    depth, start = 0, None
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = None
    return None


def resolve(intent_text: str, llm=None, *, lesson_id: str = "", **overrides) -> Proposal | None:
    """Resolve a vague teacher intent into a proposed Lesson to ratify. `llm(prompt)->str` is the
    reasoning step; omit it for a deterministic (lexical-only) proposal."""
    cands = shortlist(intent_text)
    if not cands:
        return None

    pick_id, params, time_limit, title, rationale = "", {}, "", "", ""
    if llm is not None:
        try:
            obj = _first_json(llm(_PROMPT.format(intent=intent_text, menu=_menu(cands))))
        except Exception:
            obj = None
        if isinstance(obj, dict):
            pick_id = str(obj.get("archetype", ""))
            if isinstance(obj.get("params"), dict):
                params = {str(k): str(v) for k, v in obj["params"].items()}
            time_limit = str(obj.get("time_limit", ""))
            title = str(obj.get("title", ""))

    arch = _catalog.get(pick_id) or cands[0]        # fall back to the top lexical candidate
    # ensure every ref is bound: fill any gaps from the archetype's demo params
    demo = _catalog.demo_params(arch.id)
    for ref in arch.params:
        params.setdefault(ref, demo.get(ref, ref.upper()))
    params = {ref: params[ref] for ref in arch.params}     # drop stray keys, keep order

    les = _lesson.from_archetype(
        arch.id, params, id=lesson_id or f"resolved-{arch.id}",
        title=title or arch.summary, brief=arch.summary,
        time_limit=time_limit or overrides.get("time_limit", "20m"),
        **{k: v for k, v in overrides.items() if k != "time_limit"})
    return Proposal(archetype_id=arch.id, params=params, lesson=les,
                    rationale=rationale or f"Matched intent to '{arch.id}'.",
                    candidates=[a.id for a in cands])
