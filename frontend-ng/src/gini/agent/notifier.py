"""Notification / salience — the deterministic layer that decides which changes are worth waking the
Reasoning persona (GINI_MISSIONS_AGENT_ARCHITECTURE.md §3-4). Salience is **rules-only** (decided): a
fixed table, no LLM on the hot path. Input = the verdicts that FLIPPED on the last blackboard update
(plus explicit events like a student question or a run completing); output = salient Notifications.

`MissionMonitor` ties the deterministic swarm together: world change → blackboard re-verify → salient
notifications. It's the seam the live UI and the Reasoning persona both sit against.
"""
from __future__ import annotations

from .blackboard import Blackboard
from .contracts import Notification

# rules-only salience table (0..1). Tune the RULES if noisy — never add a model here.
SALIENCE = {
    "objective_met": 0.6,
    "objective_unmet": 0.4,
    "off_task_added": 0.9,
    "off_task_cleared": 0.3,
    "illegal_link_added": 0.9,
    "illegal_link_cleared": 0.3,
    "forbid_tripped": 1.0,
    "forbid_cleared": 0.3,
    "mission_complete": 1.0,
    "question": 1.0,
    "run_complete": 0.7,
}


def _note(change: str, subjects=(), data=None) -> Notification:
    return Notification(change, subjects=tuple(subjects), salience=SALIENCE.get(change, 0.5), data=data)


def notifications_from_flips(flipped, blackboard: Blackboard) -> list[Notification]:
    """Map flipped verdicts → salient notifications by rule. `flipped` is what `Blackboard.update`
    returned (verdicts whose value changed or newly appeared)."""
    notes: list[Notification] = []
    for v in flipped:
        if v.verifier_id.startswith("objective:"):
            notes.append(_note("objective_met" if v.value else "objective_unmet", subjects=(v.subject,)))
        elif v.subject == "off_task":
            data = list(v.evidence.data) if v.evidence else []
            notes.append(_note("off_task_cleared" if v.value else "off_task_added",
                               subjects=tuple(data), data=data))
        elif v.subject == "illegal_links":
            data = list(v.evidence.data) if v.evidence else []
            notes.append(_note("illegal_link_cleared" if v.value else "illegal_link_added",
                               subjects=tuple(data), data=data))
        elif v.subject.startswith("forbid:"):
            say = v.evidence.data if v.evidence else ""
            notes.append(_note("forbid_cleared" if v.value else "forbid_tripped",
                               subjects=(v.subject,), data=say))
    # a completion is derived from the whole board, not a single flip
    if flipped and blackboard.all_objectives_met():
        notes.append(_note("mission_complete"))
    return notes


def top(notes) -> Notification | None:
    """The single most salient notification (what the reasoner should react to first), or None."""
    return max(notes, key=lambda n: n.salience) if notes else None


class MissionMonitor:
    """The deterministic swarm, packaged: keeps a blackboard current and emits salient notifications
    on every world change. The Reasoning persona reads the blackboard; this tells it when to wake."""

    def __init__(self) -> None:
        self.bb = Blackboard()

    def load(self, lesson, *, pack=None, runner=None) -> None:
        verifiers = pack.verifiers(lesson) if pack is not None else None
        self.bb.load_lesson(lesson, verifiers=verifiers, runner=runner)

    def clear(self) -> None:
        self.bb.clear()

    def on_world_change(self, topology, *, changed: set[str] | None = None) -> list[Notification]:
        flipped = self.bb.update(topology, changed=changed)
        return notifications_from_flips(flipped, self.bb)

    def on_question(self, text: str) -> Notification:
        return _note("question", data=text)

    def on_run_complete(self) -> Notification:
        return _note("run_complete")
