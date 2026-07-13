"""Assembly — turn a set of chosen fragments into ONE complete, gradable mission by graph closure.

This is the engine behind "the reasoner pieces templates together into bigger missions." Given one
or more **core** fragments, assembly:

  1. **closes the graph** — every `requires` is satisfied by some fragment's `provides`, pulling in a
     provider when a requirement is unmet (so nothing dangles);
  2. **fills layers** — for engaged genres it adds an *exercise* and an *observe* companion so a bare
     skeleton becomes educational (the completeness rule of the design);
  3. **defaults genre + quest level** from the shape of the assembly (both pinnable);
  4. **emits a Lesson** — the union of the fragments' objectives (semantic-dedup on the predicate,
     ids kept unique) + merged intent + a help level from the genre — and rejects anything that
     doesn't `validate()` (never ship an ungradable mission).

The LLM never runs here: assembly is deterministic. The model's only job upstream is choosing the
core(s) and reskinning — GINI owns the gradable structure. See GINI_MISSIONS_COMPOSABLE_DESIGN.md.
"""
from __future__ import annotations

from . import capabilities as _caps
from . import fragments as _frag
from . import lesson as _lesson
from .objectives import Objective

EXPERIENCE, EXPEDITION, CHALLENGE = "experience", "expedition", "challenge"

# how much the game master helps, per genre
_HELP_BY_GENRE = {EXPERIENCE: "full_tutor_logged", EXPEDITION: "warmer_colder", CHALLENGE: "none"}


def _provided(frags) -> set[str]:
    out: set[str] = set()
    for f in frags:
        out.update(f.provides)
    return out


def _requirement_met(required: str, frags) -> bool:
    return any(_caps.any_satisfies(f.provides, required) for f in frags)


def close_graph(frags: list, exclude=None) -> tuple[list, list[str]]:
    """Add providers until every `requires` is satisfied. Returns (fragments, still-unmet). Prefers
    a provider that is itself a core/enrichment already in the registry; deterministic + terminating
    (each role is resolved at most once). Providers the student excluded are never pulled in."""
    chosen = list(frags)
    chosen_ids = {f.id for f in chosen}
    unmet: list[str] = []
    guard = 0
    queue = list(chosen)
    while queue and guard < 100:
        guard += 1
        f = queue.pop(0)
        for req in f.requires:
            if _requirement_met(req, chosen):
                continue
            provider = next((p for p in _frag.find_providers(req)
                             if p.id not in chosen_ids and not _excluded(p, exclude)), None)
            if provider is None:
                if req not in unmet:
                    unmet.append(req)
                continue
            chosen.append(provider)
            chosen_ids.add(provider.id)
            queue.append(provider)
    return chosen, unmet


def _excluded(fragment, exclude) -> bool:
    if exclude is None:
        return False
    from . import constraints as _con
    return _con.fragment_excluded(fragment, exclude)


def _fill_layer(chosen: list, layer: str, exclude=None) -> list:
    """If no fragment of `layer` is present, add the first enrichment fragment of that layer whose
    own requirements are already satisfied AND that the student hasn't excluded. A layer the student
    excluded outright is never filled."""
    if exclude is not None and layer in getattr(exclude, "layers", set()):
        return chosen                        # the student asked to leave this layer out
    if any(f.layer == layer for f in chosen):
        return chosen
    ids = {f.id for f in chosen}
    for cand in _frag.by_layer(layer):
        if cand.id in ids or _excluded(cand, exclude):
            continue
        if all(_requirement_met(r, chosen) for r in cand.requires):
            return chosen + [cand]
    return chosen


def default_genre(chosen: list, *, faults: int = 0) -> str:
    has_ex = any(f.layer == _frag.EXERCISE for f in chosen)
    has_ob = any(f.layer == _frag.OBSERVE for f in chosen)
    if faults > 0 and has_ex and has_ob:
        return CHALLENGE
    if has_ex:
        return EXPEDITION
    return EXPERIENCE


def default_level(chosen: list, *, faults: int = 0, guided: bool = False) -> int:
    n_core = sum(1 for f in chosen if f.layer == _frag.CORE)
    has_ex = any(f.layer == _frag.EXERCISE for f in chosen)
    has_ob = any(f.layer == _frag.OBSERVE for f in chosen)
    score = max(0, n_core - 1) + (1 if has_ex else 0) + (1 if has_ob else 0) + faults
    score -= 1 if guided else 0
    return max(0, min(3, score))


def _merge_objectives(chosen: list) -> list[Objective]:
    objs: list[Objective] = []
    seen_checks: set[str] = set()
    seen_ids: set[str] = set()
    for f in chosen:
        for o in f.instantiate():
            if o.check and o.check in seen_checks:
                continue                     # same requirement already covered by another fragment
            if o.check:
                seen_checks.add(o.check)
            oid = o.id if o.id not in seen_ids else f"{f.id}-{o.id}"
            seen_ids.add(oid)
            objs.append(Objective(id=oid, say=o.say, kind=o.kind, check=o.check, probe=o.probe,
                                  level=o.level))
    from .objectives import by_level
    return by_level(objs)        # progressive ladder: place → connect → group → prove live


def assemble(core_ids, *, genre: str | None = None, level: int | None = None, lesson_id: str,
             title: str = "", brief: str = "", persona: str = "coach", fill: bool | None = None,
             exclude=None, **overrides) -> _lesson.Lesson:
    """Assemble one Lesson from the given core fragment id(s).

    genre/level default from the assembly's shape but may be pinned. `fill` forces layer-filling on
    (Expedition/Challenge default) or off (Experience default = core only, guided). `exclude` (a
    constraints.Excludes) suppresses layers/companions the student asked to leave out."""
    seeds = [_frag.get(cid) for cid in core_ids]
    seeds = [f for f in seeds if f is not None] or [_frag.cores()[0]]

    chosen, _unmet = close_graph(seeds, exclude)

    # experience = keep it a bare, guided build; engaged genres fill exercise + observe
    engaged = genre in (EXPEDITION, CHALLENGE) if genre else True
    do_fill = engaged if fill is None else fill
    if do_fill:
        chosen = _fill_layer(chosen, _frag.EXERCISE, exclude)
        chosen = _fill_layer(chosen, _frag.OBSERVE, exclude)

    guided = genre == EXPERIENCE
    genre = genre or default_genre(chosen)
    if level is None:
        level = default_level(chosen, guided=guided)
    help_level = overrides.get("help", _HELP_BY_GENRE.get(genre, "warmer_colder"))

    primary = chosen[0]
    objs = _merge_objectives(chosen)
    spirit = " ".join(f.spirit for f in chosen if f.spirit)
    misconceptions: list[str] = []
    for f in chosen:
        for m in f.misconceptions:
            if m not in misconceptions:
                misconceptions.append(m)
    steps = [_lesson.Step(say=f.step) for f in chosen if f.step] if guided else []

    intent = _lesson.Intent(concept=primary.teaches, goal=title or primary.summary, spirit=spirit,
                            misconceptions=misconceptions)
    summary = brief or " ".join(f.summary for f in chosen if f.summary)
    quest_tag = f"[Quest L{level} · {genre}] "

    cw = overrides.get("complete_when") or getattr(primary, "complete_when", "all")
    lesson = _lesson.Lesson(
        id=lesson_id, title=title or primary.summary, brief=quest_tag + summary, objectives=objs,
        steps=steps, stage=dict(getattr(primary, "stage", {}) or {}), complete_when=cw,
        time_limit_s=_lesson.parse_duration(overrides.get("time_limit", "20m")),
        attempts=int(overrides.get("attempts", 3)), help=help_level, persona=persona,
        intent=intent, archetype=primary.id, genre=genre, level=level,
        fragments=[f.id for f in chosen])

    if not _lesson.is_valid(lesson):                 # guardrail: fall back to the primary core alone
        objs = _merge_objectives([primary])
        lesson = _lesson.Lesson(
            id=lesson_id, title=title or primary.summary, brief=quest_tag + (brief or primary.summary),
            objectives=objs, complete_when=primary.complete_when,
            time_limit_s=_lesson.parse_duration(overrides.get("time_limit", "20m")),
            attempts=int(overrides.get("attempts", 3)), help=help_level, persona=persona,
            intent=_lesson.Intent(concept=primary.teaches, goal=title or primary.summary,
                                  spirit=primary.spirit, misconceptions=list(primary.misconceptions)),
            archetype=primary.id, genre=genre, level=level, fragments=[primary.id])
    return lesson
