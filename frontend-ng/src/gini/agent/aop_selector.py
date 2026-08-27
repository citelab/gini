"""The teaching AI drafts the observation plan.

This is the ONLY model-touching module in the AOP pipeline, and it exists so a teacher never has to
assemble a plan by hand. They describe the activity the way they would describe it to a colleague;
the model reads that against plain-English descriptions of what GINI can observe and drafts a plan;
the teacher argues with the draft rather than authoring one.

**What the model is asked for, and what it is not.** It returns a `Selection`: pattern keys and
their parameters. That is all. It never writes an expectation, never sees a probe string, and never
touches the schema — `domain/aop_patterns.catalogue_brief()` gives it plain English only. So the
model's output space is small, finite, and mechanically checkable, while the part a teacher
actually cares about — reading intent and choosing what to watch — is exactly the part a language
model is good at.

Everything downstream is deterministic. `aop_assemble.assemble()` expands the selection with no
model involved, so the plan a teacher approves is reproducible and gBuilder can actuate it
directly.

**No model, no plan.** Unlike the deterministic modes elsewhere in GINI, there is no keyword
fallback here: guessing at a teacher's intent with a word-matcher would produce a plausible plan
that watches the wrong things, and the teacher has no way to tell. If no backend is reachable, say
so and let them pick patterns manually.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..domain import aop_patterns as _patterns
from ..domain.aop_assemble import PatternRef, Selection, SelectionError, dry_run

MAX_QUESTIONS = 5                    # design §3.3 — a cap, and zero answers must still work
MAX_REPAIRS = 1                      # one chance to fix its own invalid output, then give up


_SYSTEM = """\
You help a teacher set up an observation plan for a hands-on networking lab in GINI.

The teacher describes an activity in their own words. Your job is to decide WHAT SHOULD BE \
OBSERVED while their students work, by choosing from the catalogue of observation patterns below. \
You do not write checks yourself — you choose patterns and set their parameters.

The students work freely: they draw their own network and are never shown the plan. The plan only \
decides what GINI watches for.

CATALOGUE OF OBSERVATION PATTERNS
{catalogue}

RULES
- Choose only pattern keys from the catalogue above. Never invent one.
- Set only parameters listed for that pattern. Never invent one.
- Prefer including a pattern over leaving it out. A pattern that turns out not to apply costs the \
teacher nothing; a missing one means the students' work in that area is never observed.
- Read "Does NOT observe" carefully. If the teacher's intent is mostly about something no pattern \
observes, say so in your note rather than choosing a pattern that merely sounds close.
- Ask a question ONLY when the answer would change which patterns you pick. Never more than {maxq}.

Reply with ONLY a JSON object, no prose around it:
{{"patterns": [{{"key": "...", "params": {{...}}}}],
  "questions": ["..."],
  "note": "one or two plain sentences for the teacher explaining what will be observed and what \
will not",
  "coverage": {{"addressed": ["<ids of the points below your choice covers>"],
               "omitted": [{{"id": "<id>", "why": "<one line>"}}]}}}}

POINTS THAT MATTER for this activity are listed below when there are any. Every one must appear in
`addressed` or `omitted` — leaving something out is fine WITH a reason, but leaving it out
SILENTLY is not, because the teacher cannot approve a gap they were never shown.
{concerns}
"""

_REPAIR = """\
That selection was rejected:
{defects}

Reply with ONLY a corrected JSON object in the same format. Use only pattern keys and parameter \
names from the catalogue.
"""


@dataclass
class Draft:
    """What the teacher is shown: a selection, why, and anything still unclear."""
    selection: Selection | None = None
    note: str = ""
    questions: list = field(default_factory=list)
    error: str = ""
    #: Twin objections about the assembled plan — things the enumeration says matter that the
    #: model did not account for. Questions for the teacher, never blocks: a plan that ignores half
    #: of what was said can be a legitimate teaching choice, and these make sure it was a choice.
    objections: list = field(default_factory=list)
    #: True when the model returned no coverage report at all (no schema support, or it ignored the
    #: instruction). The Twin then objects only about the urgent tier rather than guessing what the
    #: prose covered.
    coverage_silent: bool = False

    @property
    def ok(self) -> bool:
        return self.selection is not None and not self.error


class SelectorUnavailable(RuntimeError):
    """No model backend. Drafting a plan is the model's whole job here, so this is fatal rather
    than something to fall back from."""


def _first_json(text: str):
    """The first balanced JSON object in a reply. Small models like to wrap JSON in prose or a
    code fence, and rejecting that would be pedantry rather than safety."""
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


def _prompt(catalogue_keys=None, concerns=None) -> str:
    """The system prompt. `concerns` is empty on the FIRST turn by necessity: the Twin enumerates
    against an assembled plan, and there is no plan until the model has chosen. They arrive on the
    revision turn, which is the Twin's designed shape — draft, audit, re-ask with the enumeration
    in hand."""
    block = ""
    if concerns:
        from .twin.dialectic import concern_context
        block = concern_context(list(concerns)) + "\nPoint ids to report on:\n" + "\n".join(
            f"  {c.id}: {c.statement}" for c in concerns)
    return _SYSTEM.format(catalogue=_patterns.catalogue_brief(catalogue_keys),
                          maxq=MAX_QUESTIONS, concerns=block)


def _to_selection(obj: dict, intent: str, params: dict, answers, deadline_s) -> Selection:
    refs = []
    for p in (obj.get("patterns") or []):
        if isinstance(p, str):                        # tolerated: a bare key instead of an object
            refs.append(PatternRef(key=p))
        elif isinstance(p, dict) and p.get("key"):
            raw = p.get("params") or {}
            refs.append(PatternRef(key=str(p["key"]),
                                   params=dict(raw) if isinstance(raw, dict) else {}))
    return Selection(intent=intent, patterns=tuple(refs), params=dict(params),
                     answers=tuple(answers or ()), deadline_s=deadline_s)


def draft(intent: str, llm, *, params=None, answers=(), deadline_s=None,
          catalogue_keys=None, twin=None) -> Draft:
    """Draft a plan from a teacher's description.

    `llm` is any callable taking a prompt and returning text — matching the other agent modules,
    so a scripted backend drives this in tests without a model.

    `answers` carries the teacher's replies to earlier questions; passing them back in is what
    makes the loop converge rather than re-asking. An empty `answers` must still yield a plan
    (design §3.3): a teacher in a hurry gets a defaulted draft to push back on.

    `twin` controls the Reasoning Twin audit: None builds one, a `Twin` instance reuses it across a
    conversation (so its history of covered concerns accumulates), and False disables it entirely —
    with it off, this function behaves exactly as it did before the Twin existed.
    """
    if llm is None:
        raise SelectorUnavailable(
            "Drafting an observation plan needs a language model. Connect one in Settings, or "
            "choose observation patterns yourself.")

    convo = _prompt(catalogue_keys) + f"\n\nTEACHER'S ACTIVITY:\n{intent.strip()}\n"
    if answers:
        convo += "\nTHE TEACHER HAS ALREADY ANSWERED:\n" + "\n".join(
            f"- {a.get('q', '')} -> {a.get('a', '')}" for a in answers) + "\n"

    try:
        raw = llm(convo)
    except Exception as e:                            # noqa: BLE001 — surface, never crash authoring
        return Draft(error=f"The model could not be reached: {e}")

    obj = _first_json(raw)
    if not isinstance(obj, dict):
        return Draft(error="The model did not return a usable plan. Try rephrasing the activity.")

    questions = [str(q) for q in (obj.get("questions") or []) if str(q).strip()][:MAX_QUESTIONS]
    note = str(obj.get("note") or "").strip()
    selection = _to_selection(obj, intent, params or {}, answers, deadline_s)

    if not selection.patterns:
        # Questions without a selection is a legitimate first turn — but a plan must still be
        # obtainable without answering, so this is only an error when it also asked nothing.
        if questions:
            return Draft(note=note, questions=questions)
        return Draft(error="The model chose no observation patterns. None may fit this activity — "
                           "check the catalogue, or rephrase.", note=note)

    # Repair against the real validator, so the model's mistakes are fixed here rather than
    # surfacing to the teacher as jargon. MAX_REPAIRS counts *retries*, so the model is called at
    # most MAX_REPAIRS + 1 times in total — a budget, not a loop that might run away on a small
    # model that keeps producing the same invalid answer.
    defects = dry_run(selection)
    for _ in range(MAX_REPAIRS):
        if not defects:
            break
        try:
            raw = llm(convo + _REPAIR.format(
                defects="\n".join(f"- {d}" for d in defects[:8])))
        except Exception:                             # noqa: BLE001
            break
        obj = _first_json(raw)
        if not isinstance(obj, dict):
            break
        selection = _to_selection(obj, intent, params or {}, answers, deadline_s)
        note = str(obj.get("note") or note).strip()
        defects = dry_run(selection)

    if defects:
        return Draft(error="The drafted plan did not pass validation: "
                           + "; ".join(str(d) for d in defects[:3]), note=note)

    result = Draft(selection=selection, note=note, questions=questions)
    if twin is False:
        return result

    engine = twin if hasattr(twin, "audit") else None
    concerns, objections = _audit(result, intent, obj, engine)
    if not objections:
        return result

    # One revision round, now that there IS a plan to enumerate against. The model gets the
    # concerns and is asked to account for each — the same enumeration that just audited it. A
    # concern it can justify is defeated; one it ignores again survives to the teacher.
    try:
        revised = llm(_prompt(catalogue_keys, concerns)
                      + f"\n\nTEACHER'S ACTIVITY:\n{intent.strip()}\n"
                      + "\nYour previous choice left these unanswered:\n"
                      + "\n".join(f"- {o.question}" for o in objections))
        obj2 = _first_json(revised)
    except Exception:                                 # noqa: BLE001
        obj2 = None
    if isinstance(obj2, dict):
        candidate = _to_selection(obj2, intent, params or {}, answers, deadline_s)
        if candidate.patterns and not dry_run(candidate):
            result.selection = candidate
            result.note = str(obj2.get("note") or result.note).strip()
        _concerns2, objections = _audit(result, intent, obj2, engine)
    result.objections = list(objections)
    return result


def _audit(result: "Draft", intent: str, reply: dict, twin=None) -> tuple:
    """Run the Reasoning Twin over the assembled plan, in place.

    The Twin is a **challenger, never a judge**: it enumerates deterministically what matters,
    diffs the model's own coverage report against that enumeration, and turns silent misses into
    questions for the teacher. It cannot change the selection, and switched off (`twin=False`) the
    draft is byte-for-byte what it was before.

    Failures are swallowed on purpose. An audit that could take the draft down with it would make
    the safety feature the least safe part of the pipeline.
    """
    try:
        from ..domain import aop_assemble as _asm
        from .twin import Twin, aop_concerns, parse_coverage
        from .twin.contracts import Coverage
        from .twin.dialectic import TwinContext

        plan = _asm.assemble(result.selection, validate_plan=False)
        concerns = aop_concerns(intent, plan, result.selection)
        if not concerns:
            return (), ()
        coverage = parse_coverage(reply.get("coverage"))
        result.coverage_silent = coverage is None
        if coverage is None:
            # Coverage-silence normally means a DEGRADED model — one that cannot follow the schema
            # — so the Twin softens to objecting only about the urgent tier rather than nagging.
            # That posture does not fit this surface, on either pass. On the first, no concerns
            # exist yet (there is no plan to enumerate against), so the model was never asked. On
            # the revision, it was handed the concern ids explicitly and still said nothing — a
            # non-answer, not a capability gap. Treating either as "degraded" made the Twin fall
            # silent on every draft: it never engaged at all, and an ignored objection vanished
            # instead of reaching the teacher.
            coverage = Coverage()
        engine = twin if isinstance(twin, Twin) else Twin()
        objections = engine.audit(concerns, coverage, TwinContext(
            move_kind="author", utterance=result.note, history=set()))
        result.objections = list(objections)
        return concerns, objections
    except Exception:                                 # noqa: BLE001
        result.objections = []
        return (), ()


def concerns_for(intent: str, selection) -> list:
    """The Twin's enumeration for a selection — what the ratify surface shows as *considered*.

    Separate from the objections so a teacher sees what was weighed as well as what went
    unanswered: the difference between a conversation and a checklist.
    """
    try:
        from ..domain import aop_assemble as _asm
        from .twin import aop_concerns
        return aop_concerns(intent, _asm.assemble(selection, validate_plan=False), selection)
    except Exception:                                 # noqa: BLE001
        return []


_BACKTRANSLATE = """\
Below is an observation plan that was assembled for a teacher's activity. Explain to the teacher, \
in plain English and in at most six sentences, what will be observed while their students work and \
what will NOT be. Do not use technical notation. Do not add anything that is not in the plan.

THE TEACHER ASKED FOR:
{intent}

THE PLAN OBSERVES:
{observes}

PATTERNS USED AND WHAT THEY EXPLICITLY DO NOT OBSERVE:
{gaps}
"""


def back_translate(plan, llm) -> str:
    """Render an assembled plan back into prose for the teacher to check against their intent.

    Deliberately NOT the model approving its own work (design §3.2). The model translates; the
    teacher judges whether the translation matches what they asked for. The literal plan is always
    available beside this via `aop_assemble.describe()`, so the prose can be checked rather than
    trusted.
    """
    if llm is None:
        return ""
    observes = "\n".join(f"- {e.say}" for e in plan.expectations)
    gaps = "\n".join(f"- {_patterns.get(k).title}: {_patterns.get(k).not_covered}"
                     for k in plan.header.patterns if k in _patterns.CATALOGUE)
    try:
        return str(llm(_BACKTRANSLATE.format(
            intent=plan.header.intent, observes=observes, gaps=gaps))).strip()
    except Exception:                                 # noqa: BLE001
        return ""
