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
will not"}}
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


def _prompt(catalogue_keys=None) -> str:
    return _SYSTEM.format(catalogue=_patterns.catalogue_brief(catalogue_keys), maxq=MAX_QUESTIONS)


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
          catalogue_keys=None) -> Draft:
    """Draft a plan from a teacher's description.

    `llm` is any callable taking a prompt and returning text — matching the other agent modules,
    so a scripted backend drives this in tests without a model.

    `answers` carries the teacher's replies to earlier questions; passing them back in is what
    makes the loop converge rather than re-asking. An empty `answers` must still yield a plan
    (design §3.3): a teacher in a hurry gets a defaulted draft to push back on.
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
    return Draft(selection=selection, note=note, questions=questions)


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
