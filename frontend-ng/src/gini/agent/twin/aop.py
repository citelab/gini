"""Concerns about a drafted Activity Observation Plan — the Twin's fourth surface.

These run at RATIFY time, teacher-facing. The teacher describes an activity in prose, the model
picks certified patterns, and the assembler expands them; what nobody has checked is whether the
resulting plan watches what the teacher actually meant. That is a *coverage* question, which is
precisely what the Twin was built for: enumerate deterministically, ask the model to report against
the enumeration, diff exactly, and turn silent misses into questions.

Everything here is derived from ground the substrate can prove — the teacher's own words, the
catalogue's declared descriptions, and the assembled plan — never from a second opinion by a model.
A concern is a question put to the teacher, never a block: an AOP that deliberately ignores half of
what was said is a legitimate teaching choice, and the Twin's job is to make sure it was a *choice*.

The most valuable concern here has no analogue in the other surfaces. Every certified pattern
declares what it does **not** observe, so when a teacher's intent leans on something the chosen
patterns explicitly exclude — packet captures, TTL values, timing measurements — that gap can be
stated as fact rather than discovered by a student's grade.
"""
from __future__ import annotations

import re

from .contracts import Concern
from .salience import cap

#: A word has to be worth matching on. These are the ones that carry no signal about *what* should
#: be observed, so they would otherwise make every pattern look relevant to every intent.
_STOP = frozenset("""
a about also an and any anything are as at be been build built by can cannot do does for from get
give go had has have how i if in into is it its let mainly make making me my need needs none not of
on one or our out own rest show shows so some still students students' that the their them then
there these they this those to under up use used using want we what when where which who will with
work works would you your
""".split())

#: `not_covered` often ends with advice about what to pair the pattern with — "Pair this with
#: multi-lan so the network itself is observed too". Those words describe a REMEDY, not a gap, and
#: matching an intent against them made a pattern look blind to the very thing its companion covers.
_ADVISORY = re.compile(r"(?:^|(?<=[.!?])\s)\s*(?:pair|use|combine|see)\b.*", re.I | re.S)

#: How much better a *rejected* pattern's word overlap must be before the Twin asks about it. Below
#: this, near-ties are noise: a pattern that matched one more word than the chosen one is not
#: evidence of a mistake, and asking about it every time is how a challenger becomes a nag.
_DISAGREE_MARGIN = 2


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


def _match_score(intent_words: set, pattern) -> int:
    """How strongly a pattern's own description answers this intent.

    Scored against `choose_when` and `title` — the text written to say when the pattern applies —
    and NOT against `not_covered`, which describes the opposite. Counting the exclusions would make
    a pattern look most relevant to the very intents it disclaims.
    """
    hay = _words(f"{pattern.title} {pattern.choose_when}")
    return len(intent_words & hay)


def aop_concerns(intent_text: str, plan, selection=None, catalogue=None) -> list[Concern]:
    """Deterministic concerns about a drafted plan, for the ratify surface.

    `plan` is an assembled `domain.aop.Aop`; `selection` the model's `Selection` (optional, for the
    parameters it bound); `catalogue` defaults to the certified pattern catalogue. Never raises —
    a concern enumerator that can fail takes the whole draft down with it, and the plan is more
    important than the audit of it.
    """
    concerns: list[Concern] = []
    try:
        from ...domain import aop_patterns as _patterns
        cat = catalogue or _patterns.CATALOGUE
        chosen = set(getattr(plan.header, "patterns", ()) or ())
        intent_words = _words(intent_text)

        concerns += _unchosen_but_matching(intent_words, chosen, cat)
        concerns += _declared_blind_spots(intent_words, chosen, cat)
        concerns += _stated_donts(intent_text, plan)
        concerns += _shape_gaps(plan)
    except Exception:                       # noqa: BLE001 — never take the draft down with us
        return []
    return cap(concerns)


def _unchosen_but_matching(intent_words: set, chosen: set, cat: dict) -> list[Concern]:
    """A pattern the teacher's words point at that was NOT selected — "why not X?".

    The same shape as the lesson-authoring surface's lexical disagreement, and the same reasoning:
    the model's pick is a judgement, but a clear mismatch between the words and the choice is a fact
    worth putting in front of the teacher before codes go out.
    """
    out = []
    best_chosen = max((_match_score(intent_words, cat[k]) for k in chosen if k in cat),
                      default=0)
    for key in sorted(set(cat) - chosen):
        score = _match_score(intent_words, cat[key])
        if score and score >= best_chosen + _DISAGREE_MARGIN:
            out.append(Concern(
                id=f"aop:unchosen:{key}",
                kind="composition-gap",
                statement=(f"the activity's words point at '{cat[key].title}' but it was not "
                           f"selected — should it be watched too?"),
                evidence=f"{score} matching terms against its 'choose when', vs {best_chosen} "
                         f"for the chosen pattern(s)",
                salience=2, source="twin.aop.match_score"))
    return out


def _declared_blind_spots(intent_words: set, chosen: set, cat: dict) -> list[Concern]:
    """What the CHOSEN patterns say they do not observe, when the intent leans on it.

    Unique to this surface and the most useful thing here. Every certified pattern already declares
    its limits in `not_covered`, so a teacher who asks for something outside them can be told
    plainly at ratify time — rather than finding out when a student's report is silent about the
    thing the activity was really about.
    """
    out = []
    for key in sorted(chosen):
        pattern = cat.get(key)
        if not pattern or not pattern.not_covered:
            continue
        # Matched against the WHOLE exclusion text, minus the advisory tail. Filtering out words
        # that also appear in `choose_when` looks tidier and is wrong: multi-lan says both "pick me
        # when they mention TTL or MAC rewriting" AND "I do not observe TTL or MAC rewriting". That
        # overlap is not a contradiction, it is the exact thing a teacher needs telling — the right
        # pattern, with a real gap inside it. Removing it silenced the most useful concern here.
        overlap = intent_words & _words(_ADVISORY.sub("", pattern.not_covered))
        if len(overlap) >= _DISAGREE_MARGIN:
            out.append(Concern(
                id=f"aop:blind-spot:{key}",
                kind="composition-gap",
                statement=(f"'{pattern.title}' will NOT observe part of what you described — "
                           f"the students can still do it, but the report will be silent on it"),
                evidence=f"its stated limits mention {', '.join(sorted(overlap))}",
                salience=2, source="twin.aop.not_covered"))
    return out


def _stated_donts(intent_text: str, plan) -> list[Concern]:
    """A teacher's explicit exclusions, checked against what the plan actually watches.

    Reuses the deterministic negation scan already used for mission constraints, so "…but no
    firewalls" is honoured the same way in both places rather than being re-invented here.
    """
    out = []
    try:
        from ...domain import aop as _aop
        from ...domain import constraints as _con
        excluded = set(getattr(_con.from_text(intent_text), "exclude", ()) or ())
        if not excluded:
            return out
        watched: set = set()
        for e in plan.expectations:
            for token, _fn in (_aop.probe_tokens(e.probe) + _aop.check_tokens(e.check)):
                watched.add(str(token).partition("@")[0])
        violated = sorted(excluded & watched)
        if violated:
            out.append(Concern(
                id="aop:exclusion-watched", kind="composition-gap",
                statement=(f"you said to leave out {', '.join(violated)}, but the plan still "
                           f"watches for it"),
                evidence=f"expectations reference {', '.join(violated)}",
                salience=3, source="domain.constraints.from_text"))
    except Exception:                       # noqa: BLE001
        pass
    return out


def _shape_gaps(plan) -> list[Concern]:
    """Two ways a plan can be well-formed and still not do its job."""
    out = []
    behavioural = [e for e in plan.expectations if e.is_behavioural]
    structural = [e for e in plan.expectations if not e.is_behavioural]

    if plan.expectations and not behavioural:
        # Everything is graph-shaped, so the plan can tell you they DREW the right thing and
        # nothing about whether it ever worked.
        out.append(Concern(
            id="aop:no-behavioural", kind="composition-gap",
            statement=("nothing in this plan is measured on a running lab — it can only observe "
                       "what was drawn, never whether it worked"),
            evidence=f"{len(plan.expectations)} expectations, none behavioural",
            salience=3, source="twin.aop.shape"))

    if plan.expectations and not structural:
        out.append(Concern(
            id="aop:no-structural", kind="composition-gap",
            statement=("nothing observes what the students built, only how it behaves — a "
                       "borrowed topology would look identical"),
            evidence=f"{len(plan.expectations)} expectations, none structural",
            salience=2, source="twin.aop.shape"))
    return out
