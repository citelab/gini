"""Multi-agent message contracts — the domain-neutral types the mission agents pass on the bus /
blackboard. Pinning these down first is the whole point: a multi-agent system with sloppy message
types is the real maintenance nightmare. See GINI_MISSIONS_AGENT_ARCHITECTURE.md §7.

Two families:
  • truth & change (deterministic agents): Observation, Fact, Verdict, Notification;
  • meaning (LLM personas): Intent, Claim, Move; plus the shared, curated MissionMemory.

Everything here is a plain dataclass (no Qt, no LLM) so the whole substrate is unit-testable. The
`Verifier`/`Observer` protocols are the domain-pack seam: a domain implements tiny verifiers and
observers; the framework never looks inside them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---- truth & change ------------------------------------------------------- #


@dataclass(frozen=True)
class Fact:
    """A checkable datum — a counterexample, disconnected components, a value, a status. NEVER prose;
    turning a Fact into meaning is always the Reasoning persona's job."""
    kind: str
    data: Any = None


@dataclass(frozen=True)
class Verdict:
    """The result of ONE tiny verifier for ONE subject: a boolean + the fact that evidences it."""
    verifier_id: str
    subject: str                 # what was checked (an objective id, a device id, "off_task" …)
    value: bool
    evidence: Fact | None = None
    deps: tuple[str, ...] = ()   # state keys this verdict depends on (for incremental re-check)


@dataclass(frozen=True)
class Observation:
    """A sensed datum from the running world (a snapshot or a discrete event)."""
    source: str
    data: Any = None
    event: str = ""              # "" for a snapshot; else a discrete event name (panic, run_done…)
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Notification:
    """A salient CHANGE, routed to wake the Reasoning persona. Salience is rules-only (0..1)."""
    change: str                  # e.g. "objective_met", "off_task_added", "run_complete", "question"
    subjects: tuple[str, ...] = ()
    salience: float = 1.0
    data: Any = None
    ts: float = field(default_factory=time.time)


# ---- meaning -------------------------------------------------------------- #

# Intent kinds the Understanding persona classifies a student input into
CONCEPT, ENTITY, OBJECTIVE, NEXT_STEP, OFF_TASK, META = (
    "concept", "entity", "objective", "next_step", "off_task", "meta")


@dataclass(frozen=True)
class Intent:
    """A student input, understood: what they mean and what it references."""
    kind: str = META
    text: str = ""
    refs: tuple[str, ...] = ()          # resolved world-entity ids
    objective_ref: str = ""


@dataclass(frozen=True)
class Claim:
    """A single factual assertion inside a Move — re-checkable against the oracle, so the model may
    reason freely while nothing false ships."""
    predicate: str               # a verifier subject or a structural predicate to re-evaluate
    expected: bool = True


@dataclass(frozen=True)
class Move:
    """The Reasoning persona's output for a turn."""
    kind: str = "say"            # say | hint | advance | flag | answer | quiet
    text: str = ""
    refs: tuple[str, ...] = ()
    claims: tuple[Claim, ...] = ()


@dataclass
class MissionMemory:
    """The Reasoning persona's curated, SHARED working memory (read-only to other personas). Compact
    and structured — not a raw transcript — so a small model stays sharp over a long mission."""
    arc: str = ""                       # where the mission is, in a line
    tried: list[str] = field(default_factory=list)     # what the student has attempted
    facts: list[str] = field(default_factory=list)     # established facts worth carrying
    threads: list[str] = field(default_factory=list)   # open questions / loose ends

    def note_tried(self, s: str) -> None:
        if s and s not in self.tried:
            self.tried.append(s)

    def note_fact(self, s: str) -> None:
        if s and s not in self.facts:
            self.facts.append(s)

    def digest(self, *, max_items: int = 6) -> str:
        """A compact slice for a persona prompt (bounded — small models drown in clutter)."""
        parts = []
        if self.arc:
            parts.append(f"Where we are: {self.arc}")
        if self.tried:
            parts.append("Tried: " + "; ".join(self.tried[-max_items:]))
        if self.facts:
            parts.append("Known: " + "; ".join(self.facts[-max_items:]))
        if self.threads:
            parts.append("Open: " + "; ".join(self.threads[-max_items:]))
        return "\n".join(parts)


# ---- the domain-pack seam ------------------------------------------------- #


@runtime_checkable
class Verifier(Protocol):
    """A tiny, single-aspect, deterministic checker. Produces Verdicts for a WorldView; declares the
    state keys it depends on so the blackboard only re-runs it when relevant state changes."""
    id: str

    def deps(self) -> tuple[str, ...]: ...

    def check(self, view) -> list[Verdict]: ...


@runtime_checkable
class Observer(Protocol):
    """Senses the running world into Observations (a snapshot or discrete events)."""
    id: str

    def observe(self, world) -> list[Observation]: ...
