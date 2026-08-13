"""Twin contracts — the Concern (a point that matters), the Coverage report (what the model says
it addressed), and the Objection (the Twin's challenge). Plain dataclasses; no Qt, no LLM.

A Concern is only ever emitted with `evidence` — a deterministic ground fact from the substrate.
The Twin can only cite what GINI can prove (REASONING_2.0_DESIGN.md §7)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Concern:
    id: str            # stable, e.g. "objective:web-reach", "legality:off_task"
    kind: str          # objective | legality | grammar-option | watcher-event | composition-gap
    statement: str     # human-readable: what matters ("'Wire the LAN to a router' is unmet")
    evidence: str      # the deterministic ground fact backing it (explain why / verdict data)
    salience: int = 1  # 0..3 (rules-only, twin/salience.py); >= MUST_ADDRESS -> must be covered
    source: str = ""   # which substrate produced it (debugging / eval)


@dataclass
class Coverage:
    """The model's self-report, used ONLY as an index for the exact diff — never trusted as
    truth (omission justifications get validated; false 'addressed' claims are the eval
    harness's and verify_claims' business)."""
    addressed: frozenset = field(default_factory=frozenset)   # concern ids
    omitted: dict = field(default_factory=dict)               # concern id -> one-line why


def parse_coverage(obj) -> Coverage | None:
    """The `coverage` member of the persona's JSON reply -> Coverage, or None when absent or
    malformed (-> the coverage-silent posture; must never raise)."""
    if not isinstance(obj, dict):
        return None
    addressed = obj.get("addressed")
    omitted_raw = obj.get("omitted")
    if not isinstance(addressed, list) and not isinstance(omitted_raw, list):
        return None
    omitted: dict = {}
    for entry in omitted_raw if isinstance(omitted_raw, list) else []:
        if isinstance(entry, dict) and entry.get("id"):
            omitted[str(entry["id"])] = str(entry.get("why", ""))
        elif isinstance(entry, str) and entry:
            omitted[entry] = ""
    return Coverage(
        addressed=frozenset(str(a) for a in (addressed or []) if a),
        omitted=omitted)


@dataclass(frozen=True)
class Objection:
    """A challenge the Twin poses for a silently-missed (or unjustified) concern."""
    concern: Concern
    question: str
