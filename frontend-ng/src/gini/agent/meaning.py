"""The meaning-side agents around the Reasoning persona: Understanding, Critic, the always-on
fast-path Classifier, and the MissionAgent that orchestrates one turn (GINI_MISSIONS_AGENT_ARCHITECTURE
.md §3-5). All are the SAME model in different personas; each is a narrow task with a strict small
output — the way we keep a small model reliable.

Turn flow: classify (always) → understand (if an utterance and routed) → reason → critique + verify
claims (if routed) → optionally one revision. Understanding & Critic are stateless; Reasoning owns the
shared memory.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import CONCEPT, ENTITY, META, NEXT_STEP, OBJECTIVE, OFF_TASK, Intent, Move
from .personas import Persona, PersonaRunner, first_json
from .reasoning import ReasoningAgent

_KINDS = {CONCEPT, ENTITY, OBJECTIVE, NEXT_STEP, OFF_TASK, META}


# ---- Understanding -------------------------------------------------------- #
class UnderstandingAgent:
    persona = Persona("Understanding",
                      system="You classify a student's message during a hands-on lab. Output ONLY "
                             "JSON: {\"kind\": concept|entity|objective|next_step|off_task|meta, "
                             "\"objective_ref\": \"<objective id or empty>\", \"refs\": [\"<name>\"]}. "
                             "No prose.", temperature=0.1)

    def __init__(self, runner: PersonaRunner, lesson) -> None:
        self.runner = runner
        self.lesson = lesson

    def parse(self, utterance: str) -> Intent:
        objs = "; ".join(f"{o.id}: {o.say}" for o in self.lesson.objectives)
        out = self.runner.call(self.persona, context=f"Objectives — {objs}",
                               task=f"Message: {utterance!r}")
        obj = first_json(out)
        if not isinstance(obj, dict):
            # tolerant fallback (never keyword-decide meaning): a '?' reads as a question
            return Intent(kind=OBJECTIVE if "?" in utterance else META, text=utterance)
        kind = obj.get("kind") if obj.get("kind") in _KINDS else META
        return Intent(kind=kind, text=utterance,
                      refs=tuple(str(r) for r in obj.get("refs", []) or []),
                      objective_ref=str(obj.get("objective_ref", "")))


# ---- Critic --------------------------------------------------------------- #
@dataclass
class Critique:
    ok: bool = True
    missing: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()

    def note(self) -> str:
        bits = []
        if self.missing:
            bits.append("missing: " + "; ".join(self.missing))
        if self.unsupported:
            bits.append("unsupported: " + "; ".join(self.unsupported))
        return " / ".join(bits)


class CriticAgent:
    persona = Persona("Critic",
                      system="You audit a draft tutor line against the FACTS. Output ONLY JSON: "
                             "{\"ok\": true|false, \"missing\": [\"...\"], \"unsupported\": [\"...\"]}. "
                             "'unsupported' = claims not backed by the facts; 'missing' = the question "
                             "isn't actually answered. No prose.", temperature=0.0)

    def __init__(self, runner: PersonaRunner) -> None:
        self.runner = runner

    def audit(self, move: Move, facts: str, *, question: str = "") -> Critique:
        out = self.runner.call(self.persona, context="FACTS:\n" + facts,
                               task=f"Question: {question!r}\nDraft: {move.text!r}")
        obj = first_json(out)
        if not isinstance(obj, dict):
            return Critique(ok=True)
        return Critique(ok=bool(obj.get("ok", True)),
                        missing=tuple(str(x) for x in obj.get("missing", []) or []),
                        unsupported=tuple(str(x) for x in obj.get("unsupported", []) or []))

    @staticmethod
    def verify_claims(move: Move, blackboard) -> list[str]:
        """Deterministic grounding check: any Claim whose expected value contradicts the blackboard.
        This is the oracle re-checking the model, independent of the LLM Critic."""
        bad = []
        for c in move.claims:
            v = blackboard.verdict(c.predicate)
            if v is not None and v.value != c.expected:
                bad.append(c.predicate)
        return bad


# ---- fast-path classifier (always-on) ------------------------------------- #
@dataclass
class Route:
    reason: bool = True
    understand: bool = False
    critic: bool = False


class Classifier:
    persona = Persona("Router",
                      system="You route one turn of a lab tutor. Output ONLY JSON: {\"reason\": "
                             "true|false, \"understand\": true|false, \"critic\": true|false}. "
                             "reason=does it need a reply; understand=is there a message to interpret; "
                             "critic=is it worth auditing (complex/answering a question). No prose.",
                      temperature=0.0)

    def __init__(self, runner: PersonaRunner) -> None:
        self.runner = runner

    def route(self, *, change: str = "", utterance: str = "") -> Route:
        # deterministic fallback when offline: reply to any trigger, understand iff there's a message
        if self.runner is None or self.runner._llm is None:
            return Route(reason=bool(change or utterance), understand=bool(utterance), critic=False)
        out = self.runner.call(self.persona,
                               context=f"change={change or 'none'} utterance={utterance!r}",
                               task="Route this turn.")
        obj = first_json(out) or {}
        understand = bool(obj.get("understand", bool(utterance))) and bool(utterance)
        return Route(reason=bool(obj.get("reason", True)), understand=understand,
                     critic=bool(obj.get("critic", False)))


# ---- orchestration -------------------------------------------------------- #
class MissionAgent:
    """One model wearing all three hats, plus the deterministic blackboard, run as a single turn.
    With a `twin` (Reasoning 2.0), the turn is additionally audited for COVERAGE: the Twin
    enumerates the concerns that matter, diffs the persona's coverage report exactly, objects
    once ("why not X?"), and flags whatever survives. `twin=None` ≡ the pre-Twin behavior."""

    def __init__(self, runner: PersonaRunner, blackboard, lesson, twin=None) -> None:
        self.runner = runner
        self.bb = blackboard
        self.lesson = lesson
        self.classifier = Classifier(runner)
        self.understanding = UnderstandingAgent(runner, lesson)
        self.reasoning = ReasoningAgent(runner, blackboard, lesson)
        self.critic = CriticAgent(runner)
        self.twin = twin
        self.last_twin_result = None      # TwinResult of the last audited turn (tests/metrics)

    def turn(self, trigger=None, *, utterance: str = "") -> Move:
        change = getattr(trigger, "change", "") if trigger is not None else ""
        route = self.classifier.route(change=change, utterance=utterance)   # always-on
        if not route.reason:
            return Move("quiet")
        intent = self.understanding.parse(utterance) if (route.understand and utterance) else None
        react_trigger = intent if intent is not None else (
            trigger if trigger is not None else Intent(text=utterance))
        concerns = self._concerns()
        move = self.reasoning.react(react_trigger, coverage_concerns=concerns or None)

        bad = self.critic.verify_claims(move, self.bb)          # deterministic oracle check (always)
        if route.critic or bad:
            crit = self.critic.audit(move, self.reasoning._context(), question=utterance)
            note = crit.note()
            if bad:
                note = (note + " / " if note else "") + "contradicts facts: " + "; ".join(bad)
            if not crit.ok or crit.unsupported or bad:
                move = self.reasoning.react(react_trigger, note=note,      # one revision pass
                                            coverage_concerns=concerns or None)
        return self._twin_audit(move, react_trigger, concerns)

    # -- Reasoning 2.0: the Twin's coverage audit --------------------------- #
    def _concerns(self) -> list:
        if self.twin is None:
            return []
        try:
            from .twin.mission import mission_concerns
            return mission_concerns(self.bb, self.lesson)
        except Exception:
            return []                     # the Twin is strictly additive — never break a turn

    def _twin_ctx(self, move: Move, react_trigger):
        """The adjudication context for this turn: what shape of move this is, what (if anything)
        was asked, the live board, the covered-history, and the schema-constrained translator for
        state claims (LLM proposes the predicate; the oracle evaluates it)."""
        from .twin.dialectic import TwinContext
        from .twin.justify import llm_translate
        utterance = react_trigger.text if isinstance(react_trigger, Intent) else ""
        return TwinContext(move_kind=move.kind, utterance=utterance,
                           world=getattr(self.bb, "_world", None),
                           history=self.twin.history,
                           translate=lambda why: llm_translate(self.runner, why))

    def _twin_audit(self, move: Move, react_trigger, concerns: list) -> Move:
        if self.twin is None or not concerns:
            return move
        try:
            import time
            from .twin.dialectic import TwinResult
            twin = self.twin
            twin.metrics["turns"] += 1
            cov = self.reasoning.last_coverage
            first_cov = cov
            if cov is None:
                twin.metrics["coverage_silent"] += 1
            objections = twin.audit(concerns, cov, self._twin_ctx(move, react_trigger))
            first = tuple(objections)
            twin.metrics["objections"] += len(first)
            # the bounded dialectic: revise while objections stand, up to max_rounds or budget
            rounds, deadline = 0, time.monotonic() + twin.budget_s
            while objections and rounds < twin.max_rounds and time.monotonic() < deadline:
                move = self.reasoning.react(react_trigger, note=twin.note(objections),
                                            coverage_concerns=concerns)
                rounds += 1
                twin.metrics["revisions"] += 1
                cov = self.reasoning.last_coverage
                objections = twin.audit(concerns, cov, self._twin_ctx(move, react_trigger))
            surviving = tuple(objections)
            if surviving:
                move = self.twin.flag(move, list(surviving))    # visible, never a silent ship
                twin.metrics["flags"] += 1
            twin.record(cov)                                    # covered ids -> mission history
            accepted, rejected = twin.split_omissions(concerns, cov)
            self.last_twin_result = TwinResult(
                concerns=tuple(concerns), coverage_silent=first_cov is None,
                objections=first, surviving=surviving,
                accepted_omissions=accepted, rejected_omissions=rejected, rounds=rounds)
            return move
        except Exception:
            return move                    # additive: a Twin fault must not lose the turn
