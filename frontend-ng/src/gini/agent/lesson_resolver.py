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
    lesson: object                       # domain.lesson.Lesson (None when infeasible)
    rationale: str = ""
    candidates: list = field(default_factory=list)   # shortlisted archetype ids (for the UI)
    infeasible: str = ""                 # set (with lesson=None) when DOs and DON'Ts conflict
    suppressed: str = ""                 # human note: what was left out to honour the DON'Ts


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


def best_lexical(intent_text: str) -> str:
    """The archetype id whose text overlaps the intent MOST (or '' if nothing overlaps). Used as a
    deterministic coverage backstop: whatever the student literally asked for ('firewall') is always
    represented among the assembled cores, even if a small model picks a different primary.

    A word in the archetype's DEFINING fields (its id + summary) counts double a word that only
    appears incidentally in the spirit/teaches — otherwise a game that merely *mentions* a firewall
    as one option (reachability-boundary) ties the game that IS about a firewall (service-chain)."""
    want = set(_lex.normalize(intent_text, query=True))
    best_id, best_score = "", 0
    for a in _catalog.all_archetypes():
        strong = set(_lex.normalize(a.id.replace("-", " ") + " " + a.summary))
        weak = set(_lex.normalize(a.spirit + " " + a.teaches.replace("-", " ")))
        score = 2 * len(want & strong) + len(want & (weak - strong))
        if score > best_score:
            best_score, best_id = score, a.id
    return best_id


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


# -- student-facing composer ("describe a mission and I'll build it") -------- #
# Unlike `resolve` (a teacher drafting a lesson to ratify), `compose` is the STUDENT path: it turns
# a free-form wish into a playable mission on the spot. Crucially it does NOT invent objectives — it
# *selects* the closest catalog archetype and may *combine* a second, so every win-condition comes
# from a verified, gradable archetype. The LLM only interprets intent + reskins the framing; a
# `validate()` guardrail drops the composition back to the primary archetype if it isn't gradable.
# That keeps the "it made a mission just for me" magic without ever dropping a student into an
# unwinnable mission — the exact split the design asks for (GINI owns structure; the LLM owns
# understanding).

_COMPOSE_PROMPT = (
    "A student wants to practise with a hands-on lab mission. They said: {intent!r}.\n"
    "Pick the SINGLE best-fitting game below. Only if their goal clearly needs a second game to be "
    "complete, also pick a secondary to combine with it. Then write a short title and a 1-2 "
    "sentence brief that frames the mission in the STUDENT'S own words. Optionally pick a genre — "
    "experience (build it & watch), expedition (investigate toward a goal), or challenge (prove it, "
    "little help). List anything the student explicitly does NOT want (e.g. 'metrics', 'dashboard') "
    "in \"exclude\".\n{menu}\n"
    "Reply ONLY as JSON: {{\"primary\": \"<id>\", \"secondary\": \"<id or empty>\", "
    "\"genre\": \"<experience|expedition|challenge or empty>\", \"exclude\": [\"<thing to leave out>\"], "
    "\"title\": \"<short title>\", \"brief\": \"<1-2 sentences>\"}}. No prose."
)


def compose(intent_text: str, llm=None, *, lesson_id: str = "", persona: str = "coach",
            genre: str | None = None, level: int | None = None, **overrides) -> Proposal | None:
    """Compose a playable Lesson from a student's free-form wish by ASSEMBLING catalog fragments
    (select the closest core, optionally a second, then close the graph + fill exercise/observe
    layers). Objectives are never authored — only copied from verified fragments. Returns a Proposal
    (its `.lesson` is ready to launch), or None if nothing is even lexically close."""
    from ..domain import assembly as _assembly
    from ..domain import constraints as _con
    from ..domain import fragments as _frag
    cands = shortlist(intent_text)
    if not cands:
        return None

    primary_id = secondary_id = title = brief = ""
    g = genre
    llm_excl_terms: list = []
    if llm is not None:
        try:
            obj = _first_json(llm(_COMPOSE_PROMPT.format(intent=intent_text, menu=_menu(cands))))
        except Exception:
            obj = None
        if isinstance(obj, dict):
            primary_id = str(obj.get("primary", ""))
            secondary_id = str(obj.get("secondary", ""))
            title = str(obj.get("title", ""))
            brief = str(obj.get("brief", ""))
            if g is None and obj.get("genre") in (_assembly.EXPERIENCE, _assembly.EXPEDITION,
                                                  _assembly.CHALLENGE):
                g = str(obj.get("genre"))
            if isinstance(obj.get("exclude"), list):
                llm_excl_terms = obj["exclude"]

    # DON'Ts: a reliable model-free negation scan of the intent, plus the model's own exclude list
    excl = _con.merge(_con.from_text(intent_text), _con.from_terms(llm_excl_terms))

    primary = _catalog.get(primary_id) or cands[0]       # fall back to the top lexical candidate
    core_ids = [primary.id]
    # coverage backstop: guarantee whatever was literally asked for is present, so the mission's
    # objectives can't drift from its narrative (the "asked for a firewall, got none" bug). Match on
    # the POSITIVE intent only, and never force in a core the student excluded.
    cover = best_lexical(_con.positive_text(intent_text))
    cover_frag = _frag.get(cover) if cover else None
    if cover and cover not in core_ids and not (cover_frag and _con.fragment_excluded(cover_frag, excl)):
        core_ids.append(cover)
    if secondary_id and secondary_id != primary.id and _catalog.get(secondary_id) is not None \
            and secondary_id not in core_ids:
        core_ids.append(secondary_id)
    core_ids = core_ids[:2]                               # cap at two cores (keep missions focused)

    lid = lesson_id or f"described-{primary.id}"
    lesson = _assembly.assemble(core_ids, genre=g, level=level, lesson_id=lid, title=title,
                                brief=brief, persona=persona, exclude=excl, **overrides)

    # feasibility: if a thing the student asked FOR still needs a thing they asked to leave OUT,
    # don't quietly build a mismatched mission — report the conflict back
    conflict = _con.objective_conflicts(lesson.objectives, excl)
    if conflict:
        want = ", ".join(conflict)
        return Proposal(archetype_id=primary.id, params={}, lesson=None,
                        candidates=[a.id for a in cands],
                        infeasible=f"That doesn't quite work: this mission needs {want}, but you "
                                   f"asked to leave that out. Drop that exclusion, or ask for a "
                                   f"topology that doesn't rely on {want}.")
    suppressed = excl.label() if excl else ""
    return Proposal(archetype_id=primary.id, params={}, lesson=lesson, suppressed=suppressed,
                    rationale=f"Assembled from {', '.join(lesson.fragments)}.",
                    candidates=[a.id for a in cands])
