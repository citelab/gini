"""AgentGameMaster — a drop-in for `GameMaster` whose reasoning runs through the multi-agent stack
(blackboard + personas), so the live mission loop is *exercised* by the new architecture without
touching MissionController's proven mechanics (panel, guided steps, scoring, completion, flags).

It exposes the exact method surface the controller drives — `brief_line`, `present_step`,
`react_reply`, `decide`, `flag_note` — but:
  • grounds every line in the blackboard's live objective verdicts (fed from the mission's own
    results, so no double evaluation);
  • routes a student question (decide with an utterance) through the full MissionAgent turn
    (classify → understand → reason → critic), and a quiet canvas-reaction through the Reasoning
    persona alone (fast, no extra model calls per drop);
  • returns a `contracts.Move` whose `kind == "quiet"` is what the controller treats as silence.

Missions stay LLM-gated: with `llm=None` every method degrades to the authored text (inert), matching
`GameMaster`.
"""
from __future__ import annotations

from .blackboard import Blackboard
from .contracts import Move, Notification
from .meaning import MissionAgent
from .personas import PersonaRunner
from .reasoning import REASONING


class AgentGameMaster:
    def __init__(self, lesson, llm=None, *, persona: str | None = None,
                 twin_enabled: bool = False) -> None:
        self.lesson = lesson
        self.llm = llm
        self.persona = persona or getattr(lesson, "persona", "coach")
        self.runner = PersonaRunner(llm)
        self.bb = Blackboard()
        self.bb.load_lesson(lesson)
        # Reasoning 2.0: the Twin audits covered turns. Model-gated — with no LLM the persona
        # answers are authored text and a coverage dialogue would only add noise.
        twin = None
        if twin_enabled and llm is not None:
            from .twin.dialectic import Twin
            twin = Twin()
        self.agent = MissionAgent(self.runner, self.bb, lesson, twin=twin)
        self.reasoning = self.agent.reasoning
        self._prev_met: int | None = None
        self._get_world = None                          # bound by the controller (for the explainer)

    def bind_world(self, getter) -> None:
        """The controller hands us a way to read the live world, so the Reasoning persona can ground
        its 'why is this red' diagnosis in the actual board (the predicate explainer)."""
        self._get_world = getter

    def _sync_world(self) -> None:
        if self._get_world is not None:
            try:
                self.bb._world = self._get_world()
            except Exception:
                self.bb._world = None

    # -- narration (Reasoning persona, grounded in mission intent) ----------- #
    def brief_line(self) -> str:
        if self.llm is None:
            return self.lesson.brief
        return self.runner.call(
            REASONING, context=self.reasoning._intent(),
            task=f"Brief the student on this mission in 1-2 short lines, in character, ending by "
                 f"starting the clock. The task: {self.lesson.brief}") or self.lesson.brief

    def present_step(self, step, index: int, total: int, *, acked: str = "") -> str:
        if self.llm is None:
            return step.say
        lead = (f"The student just finished the previous beat ({acked}); acknowledge it in a few "
                "words, then ") if acked else ""
        return self.runner.call(
            REASONING, context=self.reasoning._context(),
            task=f"This is a guided lab, beat {index}/{total}. {lead}give the student THIS one "
                 f"instruction in 1-2 short lines — just this step: {step.say}") or step.say

    def react_reply(self, step, utterance: str, results) -> str:
        self.bb.ingest_results(results)
        if self.llm is None:
            return "Good — let's continue."
        return self.runner.call(
            REASONING, context=self.reasoning._context(),
            task=f"The student was asked: {step.say!r} and replied: {utterance!r}. Respond in 1-2 "
                 "short lines — affirm or gently correct, grounded in the goal; then we move on."
        ) or "Good — let's continue."

    def run_note(self, failed) -> str:
        """React to a Run whose live checks FAILED — grounded so the model can't claim a false win."""
        joined = "; ".join(failed)
        if self.llm is None:
            return f"The live check didn't pass yet: {joined}. Check the real connection."
        return self.runner.call(
            REASONING, context=self.reasoning._context(),
            task=f"The student ran the system and a LIVE check FAILED: {joined}. In ONE short line, "
                 "tell them the live verification did not pass yet and to check the actual "
                 "connection — do NOT claim it succeeded.") or f"The live check didn't pass: {joined}."

    def flag_note(self, reasons) -> str:
        joined = "; ".join(reasons)
        if self.llm is None:
            return "Heads up — " + joined
        return self.runner.call(
            REASONING, context=self.reasoning._context(),
            task=f"The student just placed something off-task: {joined}. In ONE short in-character "
                 "line, flag it and tell them to fix it — do NOT remove it for them."
        ) or ("Heads up — " + joined)

    # -- the reasoning turn (what the controller calls on change / ask) ------ #
    def decide(self, mission, results, *, utterance: str = "", world_digest: str = "") -> Move:
        if self.llm is None:
            return Move("quiet")
        self.bb.ingest_results(results)                     # sync truth from the mission's own eval
        self._sync_world()                                  # give the explainer the live board

        if utterance:                                       # a question → full trio (grounded + audited)
            return self.agent.turn(utterance=utterance)

        met = sum(1 for r in results if r.met)
        first = self._prev_met is None
        prev = self._prev_met
        self._prev_met = met

        if mission.complete:
            trigger = Notification("mission_complete", salience=1.0)
        elif getattr(mission, "state", "") == "done":
            trigger = Notification("objective_unmet", salience=0.4)   # ran out — encourage
        elif self.lesson.help == "none":
            return Move("quiet")                            # proctored: silent except completion
        elif not first and met > prev:
            trigger = Notification("objective_met", salience=0.6)
        elif not first and met < prev:
            trigger = Notification("objective_unmet", salience=0.4)
        else:
            return Move("quiet")                            # no salient change → stay quiet

        return self.reasoning.react(trigger)                # quiet reaction path: Reasoning only (fast)

    # parity with GameMaster's convenience method
    def observe(self, mission, results) -> Move:
        return self.decide(mission, results)
