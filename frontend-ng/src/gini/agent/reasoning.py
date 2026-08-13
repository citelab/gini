"""The Reasoning persona — the centre of the loop (GINI_MISSIONS_AGENT_ARCHITECTURE.md §3,5,6). It
reads TRUTH from the blackboard (never recomputes it), the mission intent, and the shared curated
memory, then reasons out the next move for whatever woke it (a salient Notification or a student
Intent). The deterministic layer decides *when* to wake it and supplies the facts; the wording and the
pedagogy are entirely the model's.

Grounding is by construction: the prompt carries the live objective/legality facts, so the answer is
about the student's actual board — not canned. A non-LLM fallback keeps it usable offline (degraded).
"""
from __future__ import annotations

from .contracts import Intent, Move, Notification
from .personas import Persona, PersonaRunner, first_json

_SYSTEM = (
    "You are the game master of a hands-on lab mission — warm, concise, and in character. You ONLY "
    "produce the next short line to the student (1-2 sentences). Ground every statement in the FACTS "
    "you are given; never invent devices, connections, or states that aren't listed. If the facts "
    "don't cover the question, say what IS known and stop — don't guess.")

REASONING = Persona("Reasoning", system=_SYSTEM, temperature=0.35, stateful=True)

# The covered variant (Reasoning 2.0): same voice, but the reply is ONE JSON object carrying the
# line plus a coverage report the Twin diffs exactly. Decoder-constrained where the backend
# supports structured outputs (schema); the tolerant first_json path handles the rest.
def _covered_persona():
    from .twin.dialectic import COVERAGE_SCHEMA
    return Persona("Reasoning", system=_SYSTEM, temperature=0.35, stateful=True,
                   schema=COVERAGE_SCHEMA)


REASONING_COVERED = None   # built lazily (avoids a twin import at module load)

# how a triggering change maps to the SHAPE of the move (routing, not reasoning — the model still
# writes the content)
_MOVE_KIND = {
    "mission_complete": "advance", "objective_met": "say", "objective_unmet": "hint",
    "off_task_added": "flag", "illegal_link_added": "flag", "forbid_tripped": "flag",
    "question": "answer",
}


class ReasoningAgent:
    def __init__(self, runner: PersonaRunner, blackboard, lesson) -> None:
        self.runner = runner
        self.bb = blackboard
        self.lesson = lesson
        self.last_coverage = None    # the last covered turn's report (None = coverage-silent)

    # -- grounding: turn the blackboard's verdicts into a compact fact sheet -- #
    def _situation(self) -> str:
        say = {o.id: o.say for o in self.lesson.objectives}
        met = [say.get(s, s) for s in
               (v.subject for v in self.bb.verdicts() if v.verifier_id.startswith("objective:") and v.value)]
        unmet_ids = self.bb.unmet_objectives()
        unmet = [say.get(s, s) + self._why(s) for s in unmet_ids]   # each open item + WHY it's red
        flags = self.bb.flags()
        lines = [f"Objectives met ({len(met)}/{len(met) + len(unmet)}): " + ("; ".join(met) or "none"),
                 "Still open: " + ("; ".join(unmet) or "none — all done")]
        if flags.get("off_task"):
            lines.append("Off-task elements on the board: " + ", ".join(flags["off_task"]))
        if flags.get("illegal_links"):
            lines.append("Illegal connections present.")
        return "\n".join(lines)

    def _why(self, objective_id: str) -> str:
        """A deterministic, board-grounded reason an objective is red (the predicate explainer). We
        need the live topology for this; the blackboard caches the world on evaluation."""
        obj = next((o for o in self.lesson.objectives if o.id == objective_id), None)
        world = getattr(self.bb, "_world", None)
        if obj is None or world is None:
            return ""
        try:
            from ..domain import explain as _explain
            reason = _explain.diagnose(obj, world)
        except Exception:
            reason = ""
        return f" — {reason}" if reason else ""

    def _intent(self) -> str:
        it = self.lesson.intent
        parts = [f"Mission goal: {it.goal}"] if it.goal else []
        if it.spirit:
            parts.append(f"What success means: {it.spirit}")
        if it.misconceptions:
            parts.append("Watch for these misconceptions: " + "; ".join(it.misconceptions))
        # the instructor's own nuance, authored on the Teaching Center and interpreted HERE (this is
        # why the course server needs no LLM — the teacher writes plain language, we reason over it)
        if getattr(it, "notes", ""):
            parts.append(f"Your instructor's guidance for this lab: {it.notes}")
        return "\n".join(parts)

    def _context(self) -> str:
        return "\n".join(p for p in (self._intent(), "FACTS:\n" + self._situation(),
                                     self.bb.memory.digest()) if p)

    # -- the reasoning turn ------------------------------------------------- #
    def react(self, trigger, *, note: str = "", coverage_concerns=None) -> Move:
        """React to a Notification or a student Intent → one grounded Move. `note` (optional) is a
        critique from the Critic persona used to revise a first draft. `coverage_concerns`
        (Reasoning 2.0) asks the persona to ALSO report coverage against the Twin's concern list;
        the parsed report lands on `self.last_coverage` (None = coverage-silent)."""
        change, task, refs = self._frame(trigger)
        if note:
            task = f"{task}\nA reviewer noted: {note}. Fix that in your line."
        self.last_coverage = None
        if coverage_concerns:
            global REASONING_COVERED
            if REASONING_COVERED is None:
                REASONING_COVERED = _covered_persona()
            from .twin.dialectic import concern_context, coverage_instruction
            # twin-as-context: the concern set rides in the GROUNDING (guaranteed recall shapes
            # the draft), and again as the coverage checklist the exact diff runs against.
            ctx = "\n\n".join(p for p in (self._context(),
                                          concern_context(coverage_concerns)) if p)
            raw = self.runner.call(REASONING_COVERED, context=ctx,
                                   task=task + coverage_instruction(coverage_concerns))
            text = self._parse_covered(raw) or self._fallback(change)
        else:
            text = (self.runner.call(REASONING, context=self._context(), task=task)
                    or self._fallback(change))
        if not note:                        # don't double-record on a revision pass
            self._remember(change, trigger)
        return Move(kind=_MOVE_KIND.get(change, "say"), text=text, refs=refs)

    def _parse_covered(self, raw: str) -> str:
        """A covered reply -> the tutor line, with the coverage report set aside for the Twin.
        Tolerant: a model that ignored the JSON shape just becomes coverage-silent prose."""
        from .twin.contracts import parse_coverage
        obj = first_json(raw or "")
        if isinstance(obj, dict) and isinstance(obj.get("text"), str):
            self.last_coverage = parse_coverage(obj.get("coverage"))
            return obj["text"].strip()
        return (raw or "").strip()

    def _frame(self, trigger) -> tuple[str, str, tuple]:
        if isinstance(trigger, Intent):
            return ("question",
                    f"The student asks: {trigger.text!r}. Answer using ONLY the facts above, tied to "
                    "what they've built.", trigger.refs)
        change = getattr(trigger, "change", "say")
        subs = tuple(getattr(trigger, "subjects", ()) or ())
        tasks = {
            "mission_complete": "The student just completed every objective. Congratulate them in ONE "
                                "specific line tied to what they achieved.",
            "objective_met": "The student just satisfied an objective. Say 'warmer' in ONE line, naming "
                             "what improved — do NOT reveal the next step.",
            "objective_unmet": "The student moved away from a goal. Nudge in ONE line — no solution.",
            "off_task_added": f"The student placed something off-task ({', '.join(subs)}). Flag it in "
                              "ONE in-character line and tell them to remove it — do NOT remove it yourself.",
            "illegal_link_added": "The student made a connection that isn't allowed. Flag it in ONE line "
                                  "and tell them to rewire it.",
            "forbid_tripped": f"The student tripped a rule that must stay false ({trigger.data}). Flag it "
                              "in ONE line.",
        }
        return (change, tasks.get(change, "Give the student ONE short, helpful line for the current "
                                            "situation."), subs)

    def _remember(self, change: str, trigger) -> None:
        if change == "question" and isinstance(trigger, Intent):
            self.bb.memory.threads.append(trigger.text)
        elif change == "objective_met":
            self.bb.memory.note_fact("progress: an objective was satisfied")
        elif change in ("off_task_added", "illegal_link_added", "forbid_tripped"):
            self.bb.memory.note_tried(f"made a flagged move ({change})")
        self.bb.memory.arc = self._situation().splitlines()[0]

    def _fallback(self, change: str) -> str:
        return {"mission_complete": "Nice — every objective is green. You did it!",
                "off_task_added": "That doesn't belong in this mission — take it off the board.",
                "objective_met": "Warmer — that helped.",
                }.get(change, "Keep going — check what's still open above.")
