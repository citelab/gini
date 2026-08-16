"""The dialectic — the Twin's audit of one reasoning turn (REASONING_2.0_DESIGN.md §3.4).

Deterministic control flow throughout: an EXACT set diff of the model's coverage report against
the must-address concerns; ADJUDICATION of every claimed omission against ground truth
(twin/justify.py — an objection is defeated only by a VALIDATED justification); template-
generated objections; a bounded dialectic (max_rounds revisions or the time budget, whichever
first) driven by the persona's existing `react(note=…)` mechanism; and surviving objections
turned into a visible flag appended to the move — never a silent ship, never suppression.

The Twin also keeps per-mission state: the HISTORY of concern ids the model actually covered
(what "already addressed" is checked against) and METRICS (phase-E raw material)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .contracts import Concern, Coverage, Objection
from .justify import Adjudication, adjudicate

# The decoder-constrained reply shape for a covered reasoning turn (via Ollama structured
# outputs — see agent/llm/ollama.py `schema=`). text = the tutor line; coverage = the report.
COVERAGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "coverage": {
            "type": "object",
            "properties": {
                "addressed": {"type": "array", "items": {"type": "string"}},
                "omitted": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
                    "required": ["id"],
                }},
            },
            "required": ["addressed", "omitted"],
        },
    },
    "required": ["text", "coverage"],
}


def concern_context(concerns: list[Concern]) -> str:
    """Twin-as-context (phase B): the concern set injected UP FRONT into the grounding, so the
    substrate's guaranteed recall shapes the DRAFT — the same enumeration that later audits it.
    Statements + evidence only; what to do about them stays the model's call."""
    if not concerns:
        return ""
    lines = [f"- {c.statement} ({c.evidence})" for c in concerns]
    return "Things that matter right now (ground truth):\n" + "\n".join(lines)


def coverage_instruction(concerns: list[Concern]) -> str:
    """The reporting checklist appended to the persona task — the INDEX the exact diff runs
    against (ids the reply must account for, addressed or justified)."""
    lines = [f'  {c.id}: {c.statement}' for c in concerns]
    return (
        "\nRespond as ONE JSON object: {\"text\": \"<your line to the student>\", \"coverage\": "
        "{\"addressed\": [<concern ids your line addresses>], \"omitted\": [{\"id\": \"<concern "
        "id>\", \"why\": \"<one line>\"}]}}. Every concern below must appear in either list; "
        "omitting is fine WITH a reason (e.g. it would give the answer away, or it is off this "
        "question's topic).\nConcerns:\n" + "\n".join(lines))


def _question(c: Concern) -> str:
    if c.kind == "legality":
        return (f"You did not address this: {c.statement}. It is an active rule violation "
                f"({c.evidence}) — why not?")
    return (f"You did not address this: {c.statement} ({c.evidence}). "
            "Is it OK to leave that out? If it belongs in your line, work it in.")


def _rejected_question(c: Concern, why: str, adj: Adjudication) -> str:
    """The objection for an omission whose justification failed adjudication — it tells the
    model exactly why its excuse doesn't hold (the ground the Twin checked)."""
    return (f"You left out {c.statement!r} saying {why!r}, but {adj.reason}. "
            "Address it, or give a reason that actually holds.")


@dataclass
class TwinContext:
    """What omission adjudication needs from the turn (built by the orchestrator)."""
    move_kind: str = "say"
    utterance: str = ""
    world: object = None                      # the live board (for state-claim checks)
    history: set = field(default_factory=set)  # concern ids covered in prior turns
    translate: object = None                   # callable(why) -> predicate | None (LLM-backed)


@dataclass
class TwinResult:
    """What one audit produced — for tests, metrics (phase E), and the instructor log."""
    concerns: tuple = ()
    coverage_silent: bool = False
    objections: tuple = ()           # first-round objections
    surviving: tuple = ()            # objections still standing after the revision round(s)
    accepted_omissions: dict = field(default_factory=dict)   # id -> VALIDATED why
    rejected_omissions: dict = field(default_factory=dict)   # id -> why the justification failed
    rounds: int = 0


class Twin:
    """The Reasoning Twin's audit half. `audit` = exact diff + omission adjudication (phase B);
    `note`/`flag` are the two ways an objection re-enters the turn. Per-mission state: covered-
    concern history + metrics. Bounds: `max_rounds` revisions or `budget_s`, whichever first."""

    def __init__(self, max_rounds: int = 2, budget_s: float = 4.0) -> None:
        self.max_rounds = max_rounds
        self.budget_s = budget_s
        self.history: set = set()            # concern ids the model actually covered, ever
        self.metrics: dict = {"turns": 0, "coverage_silent": 0, "objections": 0,
                              "defeats": 0, "revisions": 0, "flags": 0}
        self.last_adjudications: dict = {}   # id -> Adjudication (of the most recent audit)

    def diff(self, concerns: list[Concern], coverage: Coverage | None) -> list[Objection]:
        """Exact diff of the coverage report against the must-address concerns — the SILENT
        misses only (omissions are the `audit` step's business).

        Coverage-silent (no report — model without schema support, or the offline fallback):
        don't guess what the prose covered; object only about the URGENT tier (salience 3),
        so a degraded model still surfaces rule violations without nagging about the rest."""
        from .salience import MUST_ADDRESS
        if coverage is None:
            return [Objection(c, _question(c)) for c in concerns if c.salience >= 3]
        seen = coverage.addressed | set(coverage.omitted)
        return [Objection(c, _question(c))
                for c in concerns
                if c.salience >= MUST_ADDRESS and c.id not in seen]

    def audit(self, concerns: list[Concern], coverage: Coverage | None,
              ctx: TwinContext | None = None) -> list[Objection]:
        """One audit round: silent misses (the diff) PLUS every claimed omission adjudicated
        against ground truth. A justification defeats its objection only if it VALIDATES."""
        from .salience import MUST_ADDRESS
        objections = self.diff(concerns, coverage)
        self.last_adjudications = {}
        if coverage is None or ctx is None:
            return objections
        by_id = {c.id: c for c in concerns}
        for cid, why in coverage.omitted.items():
            c = by_id.get(cid)
            if c is None or c.salience < MUST_ADDRESS:
                continue
            adj = adjudicate(c, why, ctx)
            self.last_adjudications[cid] = adj
            if adj.valid:
                self.metrics["defeats"] += 1
            else:
                objections.append(Objection(c, _rejected_question(c, why, adj)))
        return objections

    def record(self, coverage: Coverage | None) -> None:
        """Fold the turn's genuinely-covered concern ids into the mission history (what a later
        'already addressed' justification is checked against). Omissions do NOT count."""
        if coverage is not None:
            self.history |= set(coverage.addressed)

    def split_omissions(self, concerns: list[Concern], coverage: Coverage | None) -> tuple:
        """(accepted, rejected) omission maps from the last audit's adjudications."""
        if coverage is None:
            return {}, {}
        ids = {c.id for c in concerns}
        accepted, rejected = {}, {}
        for cid, why in coverage.omitted.items():
            if cid not in ids:
                continue
            adj = self.last_adjudications.get(cid)
            if adj is not None and not adj.valid:
                rejected[cid] = adj.reason or why
            else:
                accepted[cid] = why
        return accepted, rejected

    @staticmethod
    def note(objections: list[Objection]) -> str:
        """One revision note carrying every objection (batched — one round trip, not N)."""
        return " ".join(o.question for o in objections)

    @staticmethod
    def flag(move, objections: list[Objection]):
        """Surviving objections become a visible, clearly-separated addendum on the move (the
        ratified game-master surfacing: append). The Twin's voice is the concern STATEMENT —
        grounded substrate text, not model prose."""
        if not objections:
            return move
        worth = "; ".join(o.concern.statement for o in objections)
        from ..contracts import Move
        return Move(kind=move.kind, text=(move.text + f"\n(Also worth a look: {worth}.)").strip(),
                    refs=move.refs, claims=move.claims)
