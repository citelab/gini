"""The Blackboard — the shared truth cache the deterministic swarm keeps current and the Reasoning
persona reads from (GINI_MISSIONS_AGENT_ARCHITECTURE.md §4). It holds the latest Verdict per subject
plus the shared, curated MissionMemory. Verdicts are recomputed incrementally: an update names the
state keys that changed, and only verifiers whose `deps()` intersect those keys re-run.

Pure Python (no Qt): the substrate is unit-testable with a real Topology + lesson. The live wiring
(bus → observers → verifiers → notifications → reasoning) sits on top in later phases.
"""
from __future__ import annotations

from .contracts import Fact, MissionMemory, Verdict
from .verifiers import WorldView, for_lesson


class Registry:
    def __init__(self) -> None:
        self._verifiers: dict[str, object] = {}

    def add(self, verifier) -> None:
        self._verifiers[verifier.id] = verifier

    def add_all(self, verifiers) -> None:
        for v in verifiers:
            self.add(v)

    def remove(self, verifier_id: str) -> None:
        self._verifiers.pop(verifier_id, None)

    def clear(self) -> None:
        self._verifiers.clear()

    def verifiers(self) -> list:
        return list(self._verifiers.values())

    def affected(self, changed: set[str] | None) -> list:
        """Verifiers to re-run: all when `changed` is None, else those whose deps intersect it."""
        if changed is None:
            return self.verifiers()
        return [v for v in self._verifiers.values() if set(v.deps()) & changed]


class Blackboard:
    def __init__(self) -> None:
        self.registry = Registry()
        self.memory = MissionMemory()
        self._verdicts: dict[str, object] = {}
        self._lesson = None
        self._runner = None

    # -- lifecycle ---------------------------------------------------------- #
    def load_lesson(self, lesson, *, verifiers=None, runner=None) -> None:
        """Register the tiny verifiers a mission needs and reset the truth cache. `verifiers` lets a
        domain pack supply its own set; it defaults to the networking `for_lesson`."""
        self.registry.clear()
        self.registry.add_all(verifiers if verifiers is not None else for_lesson(lesson))
        self._verdicts.clear()
        self._lesson = lesson
        self._runner = runner

    def clear(self) -> None:
        self.registry.clear()
        self._verdicts.clear()
        self._lesson = None
        self.memory = MissionMemory()

    # -- truth maintenance -------------------------------------------------- #
    def update(self, topology, *, changed: set[str] | None = None) -> list:
        """Re-run the affected verifiers against the current topology; refresh the cache. Returns the
        verdicts that CHANGED (value flipped or newly appeared) — the raw material for notifications."""
        if self._lesson is None:
            return []
        view = WorldView.of(topology, self._lesson, runner=self._runner)
        flipped = []
        for v in self.registry.affected(changed):
            for verdict in v.check(view):
                prev = self._verdicts.get(verdict.subject)
                if prev is None or prev.value != verdict.value:
                    flipped.append(verdict)
                self._verdicts[verdict.subject] = verdict
        return flipped

    def ingest_results(self, results) -> None:
        """Set objective verdicts directly from pre-computed ObjectiveResults (the mission already
        evaluated them) — so the Reasoning persona is grounded without a second evaluation and even
        when only results, not a topology, are on hand."""
        for r in results:
            self._verdicts[r.id] = Verdict(f"objective:{r.id}", subject=r.id, value=r.met,
                                           evidence=Fact("status", r.status), deps=("topology",))

    # -- reads -------------------------------------------------------------- #
    def verdict(self, subject: str):
        return self._verdicts.get(subject)

    def value(self, subject: str) -> bool:
        v = self._verdicts.get(subject)
        return bool(v and v.value)

    def verdicts(self) -> list:
        return list(self._verdicts.values())

    def unmet_objectives(self) -> list[str]:
        return [v.subject for v in self._verdicts.values()
                if v.verifier_id.startswith("objective:") and not v.value]

    def all_objectives_met(self) -> bool:
        objs = [v for v in self._verdicts.values() if v.verifier_id.startswith("objective:")]
        return bool(objs) and all(v.value for v in objs)

    def flags(self) -> dict:
        """Current legality problems (for the red badges), read straight off the cache."""
        off = self.verdict("off_task")
        bad = self.verdict("illegal_links")
        return {"off_task": list(off.evidence.data) if off and off.evidence and not off.value else [],
                "illegal_links": list(bad.evidence.data) if bad and bad.evidence and not bad.value else []}
