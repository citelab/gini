"""The authoring/compose concern enumerator (REASONING_2.0_DESIGN.md §5, third surface).

These concerns run at RATIFY time, teacher-facing: deterministic observations about the gap
between what the teacher's words said and what the model picked — "the words strongly match Y
but X was chosen; why not Y?" — plus a check that stated DON'Ts really were honoured. The
ratify UI renders them as questions beside the Proposal; nothing here blocks or judges."""
from __future__ import annotations

from .contracts import Concern
from .salience import cap

_DISAGREE_MARGIN = 3     # the lexical-override threshold lesson_resolver already uses


def authoring_concerns(intent_text: str, proposal) -> list[Concern]:
    """Concerns about a compose/resolve Proposal, for the ratify surface. Duck-typed against
    `lesson_resolver.Proposal` (archetype_id, lesson, infeasible, suppressed)."""
    concerns: list[Concern] = []

    # 1) strong lexical disagreement: the teacher's words match a different archetype hard.
    try:
        from ..lesson_resolver import lexical_scores
        scores = lexical_scores(intent_text)
        if scores:
            top_id, top_score = scores[0]
            if top_id != getattr(proposal, "archetype_id", "") and top_score >= _DISAGREE_MARGIN:
                concerns.append(Concern(
                    id=f"authoring:lexical-disagreement:{top_id}",
                    kind="composition-gap",
                    statement=(f"the request's words strongly match '{top_id}' but "
                               f"'{proposal.archetype_id}' was chosen — why not {top_id}?"),
                    evidence=f"lexical score {top_score} for {top_id} (defining-field overlap)",
                    salience=2, source="lesson_resolver.lexical_scores"))
    except Exception:
        pass

    # 2) stated DON'Ts must be honoured in the assembled lesson (deterministic negation scan
    #    against the elements the lesson actually stages/grades).
    try:
        from ...domain import constraints as _con
        excluded = set(getattr(_con.from_text(intent_text), "exclude", ()) or ())
        lesson = getattr(proposal, "lesson", None)
        if excluded and lesson is not None:
            staged = {getattr(d, "type_key", "") for d in
                      getattr(getattr(lesson, "stage", None), "values", lambda: [])()} \
                if hasattr(getattr(lesson, "stage", None), "values") else set()
            violated = sorted(excluded & staged)
            if violated:
                concerns.append(Concern(
                    id="authoring:exclusion-violated", kind="composition-gap",
                    statement=("the request excluded " + ", ".join(violated) +
                               " but the proposal stages them"),
                    evidence=f"negation scan: exclude={sorted(excluded)}; staged∩={violated}",
                    salience=3, source="constraints.from_text"))
    except Exception:
        pass

    # 3) infeasibility / suppression the resolver already computed — surfaced as concerns so the
    #    ratify UI shows them in the same voice.
    if getattr(proposal, "infeasible", ""):
        concerns.append(Concern(
            id="authoring:infeasible", kind="composition-gap",
            statement=f"the request is infeasible as stated: {proposal.infeasible}",
            evidence=proposal.infeasible, salience=3, source="lesson_resolver"))
    if getattr(proposal, "suppressed", ""):
        concerns.append(Concern(
            id="authoring:suppressed", kind="composition-gap",
            statement=f"something was left out to honour the DON'Ts: {proposal.suppressed}",
            evidence=proposal.suppressed, salience=2, source="lesson_resolver"))

    return cap(concerns)
