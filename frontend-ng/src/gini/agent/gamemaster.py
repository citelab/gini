"""The Game Master — the pivotal per-turn reasoning loop of a Mission.

Each turn the game master reasons over three inputs and emits ONE move:

    TEACHER INTENT   — the lesson's goal + spirit + misconceptions (from its Game-Catalog
                       archetype); what success really means, mechanism-free.
    STUDENT INTENT   — what the student means / is attempting, interpreted (never keyword-matched)
                       from their words and the change in the canvas.
    RUNTIME FACTS    — the objective results GINI's runtime produced (met / unmet / pending).

Deterministic *guards* frame the decision (completion, expiry, help level), but the interpretation
of the student and the phrasing of every message are the reasoning LLM's job — this is where the AI
is genuinely drawn into the loop. GINI owns the facts; the game master owns the understanding.

Missions are hard-gated on a reasoning LLM: with `llm=None` the game master is inert (returns a
`quiet` move), matching the design's "no model → no Mission." Pure orchestration + prompts; the
`llm` callable is injected, so the whole loop is unit-testable with a scripted model.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# student progress reads
ATTEMPTING, STUCK, ASKING_HINT, SYMPTOM, OFF_TASK, CELEBRATING, SILENT = (
    "attempting", "stuck", "asking_hint", "reporting_symptom", "off_task", "celebrating", "silent")

# move kinds
BRIEF, NUDGE, HINT, INTERPRET, ANSWER, CELEBRATE, DEFEAT, QUIET = (
    "brief", "nudge", "hint", "interpret", "answer", "celebrate", "defeat", "quiet")

_PERSONA_VOICE = {
    "coach": "You are a warm, encouraging coach on the student's side. Be supportive and concise.",
    "challenger": "You are a playful challenger running a contest. Be spirited and a little "
                  "competitive, but never mean. Keep it short.",
}


@dataclass
class StudentRead:
    progress: str = SILENT
    objective_ref: str = ""
    is_question: bool = False
    raw: str = ""


@dataclass
class Move:
    kind: str
    text: str = ""
    objective_ref: str = ""
    logged: bool = False        # help usage recorded to the profile (full_tutor_logged)


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


class GameMaster:
    def __init__(self, lesson, llm=None, *, persona: str | None = None) -> None:
        self.lesson = lesson
        self.llm = llm
        self.persona = persona or getattr(lesson, "persona", "coach")
        self._prev_met: int | None = None       # for warmer/colder deltas

    # -- LLM plumbing ------------------------------------------------------- #
    def _ask(self, prompt: str) -> str:
        if self.llm is None:
            return ""
        try:
            return self.llm(prompt) or ""
        except Exception:
            return ""

    def _voice(self) -> str:
        return _PERSONA_VOICE.get(self.persona, _PERSONA_VOICE["coach"])

    def _intent(self) -> str:
        it = self.lesson.intent
        parts = [f"Lesson goal: {it.goal}"] if it.goal else []
        if it.spirit:
            parts.append(f"What success means (the SPIRIT — any mechanism counts): {it.spirit}")
        if it.misconceptions:
            parts.append("Common misconceptions: " + "; ".join(it.misconceptions))
        return "\n".join(parts)

    # -- student intent interpretation (reasoning, not keyword) ------------- #
    def interpret(self, utterance: str, results) -> StudentRead:
        """Interpret what the student means / is attempting. With an utterance the LLM classifies
        it against THIS lesson's objectives; with none, infer a light read from the facts."""
        if not (utterance or "").strip():
            met = sum(1 for r in results if r.met)
            return StudentRead(progress=ATTEMPTING if met < len(results) else CELEBRATING)
        objs = "; ".join(f"{r.id}: {r.say} [{r.status}]" for r in results)
        prompt = (
            f"{self._voice()}\nYou interpret a student mid-lab. {self._intent()}\n"
            f"Objectives (id: goal [state]): {objs}\n"
            f'Student said: {utterance!r}\n'
            "Reply ONLY as JSON: {\"progress\": attempting|stuck|asking_hint|reporting_symptom|"
            "off_task|celebrating, \"objective_ref\": \"<objective id or empty>\", "
            "\"is_question\": true|false}. No prose.")
        obj = _first_json(self._ask(prompt))
        if not isinstance(obj, dict):
            # couldn't parse → treat a '?' as a question, else attempting (never keyword-decide meaning)
            return StudentRead(progress=ASKING_HINT if "?" in utterance else ATTEMPTING,
                               is_question="?" in utterance, raw=utterance)
        prog = obj.get("progress")
        prog = prog if prog in (ATTEMPTING, STUCK, ASKING_HINT, SYMPTOM, OFF_TASK, CELEBRATING) \
            else ATTEMPTING
        return StudentRead(progress=prog, objective_ref=str(obj.get("objective_ref", "")),
                           is_question=bool(obj.get("is_question", False)), raw=utterance)

    # -- the reasoning loop ------------------------------------------------- #
    def decide(self, mission, results, *, utterance: str = "", world_digest: str = "") -> Move:
        """Reason over teacher intent + student intent + runtime facts → one move."""
        if self.llm is None:
            return Move(QUIET)                       # no model → inert (Missions are LLM-gated)

        help_level = self.lesson.help
        met_now = sum(1 for r in results if r.met)
        first_turn = self._prev_met is None
        delta = 0 if first_turn else met_now - self._prev_met
        self._prev_met = met_now
        read = self.interpret(utterance, results)

        # --- deterministic guards frame the move; the LLM interprets + phrases ---
        if mission.complete:
            return Move(CELEBRATE, self._say_victory(mission))
        if getattr(mission, "state", "") == "done" and not mission.complete:
            return Move(DEFEAT, self._say_encourage(results))

        # a question / explicit ask — governed by the lesson's help level
        if read.is_question or read.progress in (ASKING_HINT, SYMPTOM):
            if help_level == "none":
                return Move(QUIET)                   # proctored / no-help: stay silent
            if help_level == "full_tutor_logged":
                return Move(ANSWER, self._answer(utterance, results), logged=True)
            return Move(HINT, self._hint(read, results), objective_ref=read.objective_ref)

        # warmer / colder — grounded in the change in met objectives
        if help_level != "none" and not first_turn and delta > 0:
            return Move(NUDGE, self._warmer(results))
        if help_level != "none" and not first_turn and delta < 0:
            return Move(NUDGE, self._colder(results))

        # stuck or drifting → interpret + guide (spirit-aware), if help allows
        if help_level != "none" and read.progress == STUCK:
            return Move(HINT, self._hint(read, results), objective_ref=read.objective_ref)
        if read.progress == OFF_TASK:
            return Move(INTERPRET, self._redirect(results))

        return Move(QUIET)

    def observe(self, mission, results) -> Move:
        """Convenience for an action-only turn (no utterance) — e.g. the canvas changed."""
        return self.decide(mission, results)

    # -- phrasing (the LLM's voice, grounded in intent + facts) ------------- #
    def _unmet(self, results):
        return [r for r in results if not r.met]

    def _facts(self, results) -> str:
        return "; ".join(f"{r.say} [{r.status}]" for r in results)

    def brief_line(self) -> str:
        """Narrate the mission brief in character (falls back to the authored brief text)."""
        return self._ask(
            f"{self._voice()}\n{self._intent()}\nBrief the student on this mission in 1-2 short "
            f"lines, in character, ending by starting the clock. The task: {self.lesson.brief}"
        ) or self.lesson.brief

    # -- guided beats (multi-turn) ----------------------------------------- #
    def present_step(self, step, index: int, total: int, *, acked: str = "") -> str:
        """Present ONE beat in character. `acked` (optional) is a one-line acknowledgement of what
        the student just did, so consecutive beats feel like a conversation, not a list."""
        lead = (f"The student just completed the previous beat ({acked}). Acknowledge it in a few "
                "words, then " if acked else "")
        return self._ask(
            f"{self._voice()}\n{self._intent()}\nThis is a guided lab, beat {index}/{total}. "
            f"{lead}give the student THIS one instruction, in character, in 1-2 short lines — just "
            f"this step, don't reveal later steps: {step.say}") or step.say

    def react_reply(self, step, utterance: str, results) -> str:
        """Respond to the student's answer on a read/reflect beat, then they move on."""
        return self._ask(
            f"{self._voice()}\n{self._intent()}\nThe student was asked: {step.say!r} and replied: "
            f"{utterance!r}. Respond in 1-2 short lines — affirm or gently correct, grounded in the "
            "lesson's goal; then we move on.") or "Good — let's continue."

    def off_step_nudge(self, step, results) -> str:
        """The student did something that doesn't satisfy the current beat — a spirit-aware nudge."""
        return self._ask(
            f"{self._voice()}\n{self._intent()}\nThe current beat asks: {step.say!r}. The student "
            f"acted but hasn't satisfied it yet. Objectives: {self._facts(results)}. Give ONE short "
            "nudge toward THIS beat's goal — don't do it for them.") or "Not quite yet — keep at this step."

    def flag_note(self, reasons) -> str:
        """Call out an off-task / wrongly-wired move the student just made — playfully firm, and
        it tells them to FIX it (the game master never deletes anything itself)."""
        joined = "; ".join(reasons)
        return self._ask(
            f"{self._voice()}\n{self._intent()}\nThe student just did something that doesn't fit "
            f"this mission: {joined}. In ONE short, in-character line, flag it and tell them to fix "
            "it — do NOT remove it for them.") or ("Heads up — " + joined)

    def _say_victory(self, mission) -> str:
        return self._ask(f"{self._voice()}\n{self._intent()}\nThe student just completed the "
                         f"mission ({mission.score().summary}). Congratulate them in ONE short, "
                         "specific line tied to what they achieved.") or "Nice — you did it!"

    def _say_encourage(self, results) -> str:
        return self._ask(f"{self._voice()}\nThe attempt ended incomplete. Objectives: "
                         f"{self._facts(results)}. Encourage the student in ONE short line and "
                         "point at what's still open — no solution.") or "Out of time — give it another go."

    def _warmer(self, results) -> str:
        return self._ask(f"{self._voice()}\n{self._intent()}\nThe student just got CLOSER. "
                         f"Objectives: {self._facts(results)}. Say 'warmer' in ONE short line, "
                         "naming what improved — do NOT reveal the next step.") or "Warmer — keep going."

    def _colder(self, results) -> str:
        return self._ask(f"{self._voice()}\nThe student just moved AWAY from the goal. Objectives: "
                         f"{self._facts(results)}. Say 'colder' gently in ONE short line — no "
                         "solution.") or "Hmm, that went the wrong way."

    def _hint(self, read: StudentRead, results) -> str:
        # prefer an authored hint for the referenced/nearest-unmet objective if the lesson has one
        target = read.objective_ref or (self._unmet(results)[0].id if self._unmet(results) else "")
        return self._ask(
            f"{self._voice()}\n{self._intent()}\nThe student seems stuck on objective {target!r}. "
            f"Objectives: {self._facts(results)}. Give ONE nudging hint toward the GOAL (spirit, "
            "not a specific mechanism) — do not hand them the answer.") or "Think about the goal, not the tool."

    def _answer(self, utterance: str, results) -> str:
        return self._ask(
            f"{self._voice()}\n{self._intent()}\nObjectives: {self._facts(results)}. The student "
            f"asked: {utterance!r}. Answer helpfully and concisely, grounded in the lesson's goal; "
            "guide, don't just solve it for them.") or "Let's think it through together."

    def _redirect(self, results) -> str:
        return self._ask(f"{self._voice()}\n{self._intent()}\nThe student drifted off-task. In ONE "
                         "friendly line, steer them back to the mission's goal.") or "Let's refocus on the task."
