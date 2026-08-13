"""Justification validation — the crux of the dialectic (REASONING_2.0_DESIGN.md §3.6).

The Twin never takes the model's word for an omission. Each stated `why` is classified into a
small set of checkable patterns and validated DETERMINISTICALLY:

  • pedagogical ("it would give the answer away")  → allowed by POLICY for hint-shaped moves
    (Socratic withholding is legitimate); logged either way.
  • scope ("that's not what they asked")           → checked lexically against the utterance:
    valid only when there IS a question and its words genuinely don't touch the concern.
  • already-addressed ("covered in an earlier hint") → checked against the Twin's history of
    concern ids the model actually covered in prior turns of this mission.
  • state ("not needed — the LAN is single-subnet") → the free text is TRANSLATED (one focused,
    schema-constrained LLM call) onto the oracle's OWN predicate grammar, then EVALUATED with
    `objectives.evaluate_check` against the live world. The translation may use a model; the
    verdict never does. Untranslatable / no world → unvalidated → the objection stands.
  • unknown                                        → unvalidated → the objection stands.

An objection is DEFEATED only by a validated justification — defeasible reasoning with ground
truth as the referee."""
from __future__ import annotations

import re
from dataclasses import dataclass

# move kinds where withholding detail is a legitimate teaching move (the mission GM's hint;
# coach surfaces extend this in phase C)
PEDAGOGICAL_OK_KINDS = {"hint"}

_PED_RE = re.compile(r"give\s*(it|the answer)?\s*away|reveal|spoil|socratic|full answer|"
                     r"answer for them|figure it out", re.I)
_SCOPE_RE = re.compile(r"not what (they|the student) asked|off.?topic|different question|"
                       r"unrelated|about .+ not|this question'?s topic|other topic", re.I)
# must pair with a COVERAGE verb — a bare "already" is usually a state claim ("there are
# already two hosts"), which belongs to the oracle-checked STATE path, not this one.
_ALREADY_RE = re.compile(
    r"already (covered|addressed|mentioned|said|explained|answered|hinted|told)|"
    r"covered (before|earlier|previously|that)|previous (hint|turn|answer|line)|"
    r"(earlier|last) (hint|turn|answer|time|line)", re.I)

PEDAGOGICAL, SCOPE, ALREADY, STATE, UNKNOWN = "pedagogical", "scope", "already", "state", "unknown"


def classify(why: str) -> str:
    """Deterministic keyword classification of a stated omission reason. Order matters:
    pedagogical/scope/already are specific idioms; anything else that *asserts something about
    the world* is treated as a state claim (and must survive translation + evaluation)."""
    w = (why or "").strip()
    if not w:
        return UNKNOWN
    if _PED_RE.search(w):
        return PEDAGOGICAL
    if _SCOPE_RE.search(w):
        return SCOPE
    if _ALREADY_RE.search(w):
        return ALREADY
    return STATE


# -- state-claim translation (LLM proposes, the oracle disposes) -------------- #
TRANSLATE_SCHEMA: dict = {
    "type": "object",
    "properties": {"predicate": {"type": ["string", "null"]}},
    "required": ["predicate"],
}

_TRANSLATE_SYSTEM = (
    "You translate ONE claim about a network lab board into ONE predicate from this exact "
    "grammar (nothing else): exists(type) · count(type) >= n · link(a, b) · path(a, b) · "
    "through(a, b, gate) · contains_type(group, type) · all_linked(type). Types are element "
    "type keys (host, switch, router, …). Output ONLY JSON: {\"predicate\": \"<one predicate>\"} "
    "or {\"predicate\": null} if the claim cannot be expressed in this grammar. No prose.")


def llm_translate(runner, why: str) -> str | None:
    """One focused, schema-constrained call turning a free-text state claim into a predicate the
    oracle can evaluate. Returns None when there's no model, no clean parse, or the model says
    the claim isn't expressible — all of which leave the justification unvalidated."""
    if runner is None or getattr(runner, "_llm", None) is None:
        return None
    from ..personas import Persona, first_json
    persona = Persona("Translator", system=_TRANSLATE_SYSTEM, temperature=0.0,
                      schema=TRANSLATE_SCHEMA)
    out = runner.call(persona, task=f"Claim: {why!r}")
    obj = first_json(out)
    pred = obj.get("predicate") if isinstance(obj, dict) else None
    return pred if isinstance(pred, str) and pred.strip() else None


def state_holds(predicate: str, world) -> bool | None:
    """Evaluate a translated predicate against the live world with the oracle's OWN evaluator
    (`objectives.evaluate_check`) — one predicate semantics, never a second one. None = cannot
    evaluate (bad predicate / no world) — which leaves the objection standing."""
    if not predicate or world is None:
        return None
    try:
        from ...domain import objectives as _obj
        if not _obj.check_ok(predicate):
            return None
        # accept either the predicate-World interface (has .exists) or a bare Topology
        view = world if hasattr(world, "exists") else _obj.TopologyWorld(world)
        return bool(_obj.evaluate_check(predicate, view))
    except Exception:
        return None


# -- adjudication ------------------------------------------------------------- #
@dataclass(frozen=True)
class Adjudication:
    valid: bool
    kind: str
    reason: str = ""     # why it failed (feeds the objection question) / how it was validated


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def adjudicate(concern, why: str, ctx) -> Adjudication:
    """Validate one omission justification against ground truth. `ctx` carries move_kind,
    utterance, world, history (concern ids covered in prior turns), and a translate callable."""
    kind = classify(why)

    if kind == PEDAGOGICAL:
        if getattr(ctx, "move_kind", "") in PEDAGOGICAL_OK_KINDS:
            return Adjudication(True, kind, "withholding is legitimate for a hint")
        return Adjudication(False, kind,
                            "this turn isn't a hint — answering shouldn't withhold this")

    if kind == SCOPE:
        utterance = getattr(ctx, "utterance", "") or ""
        if not utterance:
            return Adjudication(False, kind, "nothing was asked — there is no topic to be off")
        overlap = _tokens(utterance) & (_tokens(concern.statement) | _tokens(concern.id))
        if overlap:
            return Adjudication(False, kind,
                                f"the question mentions {', '.join(sorted(overlap))} — it IS on topic")
        return Adjudication(True, kind, "the question doesn't touch this concern")

    if kind == ALREADY:
        if concern.id in (getattr(ctx, "history", None) or set()):
            return Adjudication(True, kind, "it was covered in an earlier turn")
        return Adjudication(False, kind, "it was never actually covered in an earlier turn")

    if kind == STATE:
        translate = getattr(ctx, "translate", None)
        pred = translate(why) if callable(translate) else None
        if not pred:
            return Adjudication(False, kind, "the claim couldn't be checked against the board")
        held = state_holds(pred, getattr(ctx, "world", None))
        if held is True:
            return Adjudication(True, kind, f"checked: {pred} holds")
        if held is False:
            return Adjudication(False, kind, f"checked: {pred} is FALSE on this board")
        return Adjudication(False, kind, "the claim couldn't be checked against the board")

    return Adjudication(False, UNKNOWN, "no checkable reason was given")
