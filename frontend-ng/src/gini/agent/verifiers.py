"""Tiny, single-aspect verifiers — the deterministic truth-agents (GINI_MISSIONS_AGENT_ARCHITECTURE
.md §3). Each checks ONE aspect and returns Verdicts; none of them reason. They wrap the evaluators we
already have (`domain/objectives`, `domain/legality`) so P1 is mostly a reframing, not new logic.

State-dependency keys (coarse for now — the exact scheme is an open decision): "topology" for
structural checks over the graph, "runtime" for behavioral probes. The blackboard re-runs a verifier
only when a changed key intersects its `deps()`.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain import legality as _legality
from ..domain import objectives as _obj
from .contracts import Fact, Verdict

TOPOLOGY, RUNTIME = "topology", "runtime"


@dataclass
class WorldView:
    """What a verifier is handed: the live topology, an objectives World over it, the lesson, and an
    optional behavioral runner. Built once per blackboard update and shared across verifiers."""
    topology: object
    world: object
    lesson: object
    runner: object = None

    @classmethod
    def of(cls, topology, lesson, *, runner=None):
        return cls(topology=topology, world=_obj.TopologyWorld(topology), lesson=lesson, runner=runner)


class ObjectiveVerifier:
    """One tiny verifier per objective — met/unmet (behavioral → pending counts as not-yet-met)."""

    def __init__(self, objective) -> None:
        self.objective = objective
        self.id = f"objective:{objective.id}"

    def deps(self) -> tuple[str, ...]:
        return (RUNTIME,) if self.objective.is_behavioral() else (TOPOLOGY,)

    def check(self, view: WorldView) -> list[Verdict]:
        r = _obj.evaluate(self.objective, view.world, view.runner)
        return [Verdict(self.id, subject=self.objective.id, value=r.met,
                        evidence=Fact("status", r.status), deps=self.deps())]


class OffTaskVerifier:
    """Off-task element aspect: are any placed elements outside the mission's relevant family?"""
    id = "legality:off_task"

    def __init__(self, lesson) -> None:
        self.lesson = lesson

    def deps(self) -> tuple[str, ...]:
        return (TOPOLOGY,)

    def check(self, view: WorldView) -> list[Verdict]:
        devices = _legality.off_task_devices(self.lesson, view.topology)
        return [Verdict(self.id, subject="off_task", value=(not devices),
                        evidence=Fact("devices", list(devices)), deps=(TOPOLOGY,))]


class IllegalLinkVerifier:
    """Connection-grammar aspect: are any drawn links disallowed?"""
    id = "legality:illegal_links"

    def deps(self) -> tuple[str, ...]:
        return (TOPOLOGY,)

    def check(self, view: WorldView) -> list[Verdict]:
        bad = _legality.illegal_links(view.topology)
        return [Verdict(self.id, subject="illegal_links", value=(not bad),
                        evidence=Fact("links", list(bad)), deps=(TOPOLOGY,))]


class ForbidVerifier:
    """One tiny verifier per lesson forbid-rule — the rule must stay FALSE (value True = still safe)."""

    def __init__(self, index: int, forbid) -> None:
        self.index = index
        self.forbid = forbid
        self.id = f"forbid:{index}"

    def deps(self) -> tuple[str, ...]:
        return (TOPOLOGY,)

    def check(self, view: WorldView) -> list[Verdict]:
        try:
            tripped = bool(self.forbid.check) and _obj.evaluate_check(self.forbid.check, view.world)
        except _obj.PredicateError:
            tripped = False
        return [Verdict(self.id, subject=f"forbid:{self.index}", value=(not tripped),
                        evidence=Fact("say", self.forbid.say), deps=(TOPOLOGY,))]


def for_lesson(lesson) -> list:
    """The full set of tiny verifiers a lesson needs: one per objective + the legality aspects + one
    per forbid rule. This is 'many tiny', assembled per mission."""
    vs: list = [ObjectiveVerifier(o) for o in lesson.objectives]
    vs.append(OffTaskVerifier(lesson))
    vs.append(IllegalLinkVerifier())
    vs.extend(ForbidVerifier(i, f) for i, f in enumerate(getattr(lesson, "forbid", []) or []))
    return vs
