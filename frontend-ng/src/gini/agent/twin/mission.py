"""The mission-GM concern enumerator — a deterministic projection of what already sits on the
blackboard into the Twin's Concern set. Nothing is sensed anew and nothing is judged: every
concern is backed by a cached Verdict (and, for objectives, the predicate explainer's
board-grounded why). REASONING_2.0_DESIGN.md §3.2, first row of the surface table."""
from __future__ import annotations

from .contracts import Concern
from .salience import cap, salience_for


def _why(objective, world) -> str:
    """The deterministic, board-grounded reason an objective is red (domain/explain.py) —
    the same explainer the Reasoning persona uses; '' when the world isn't on hand."""
    if objective is None or world is None:
        return ""
    try:
        from ...domain import explain as _explain
        return _explain.diagnose(objective, world) or ""
    except Exception:
        return ""


def mission_concerns(blackboard, lesson) -> list[Concern]:
    """Enumerate this turn's concerns for the mission game-master, salience-capped.

    Sources (all already deterministic, all already computed):
      • every UNMET objective — statement is its `say`, evidence is the explainer's why
        (or the bare verdict when no world is cached);
      • legality flags (off-task elements, illegal links) — evidence is the flagged names.
    """
    concerns: list[Concern] = []
    say = {o.id: o for o in getattr(lesson, "objectives", [])}
    world = getattr(blackboard, "_world", None)

    for oid in blackboard.unmet_objectives():
        obj = say.get(oid)
        why = _why(obj, world)
        statement = getattr(obj, "say", oid)
        concerns.append(Concern(
            id=f"objective:{oid}", kind="objective",
            statement=f"'{statement}' is unmet",
            evidence=why or "objective verdict: unmet",
            salience=salience_for("objective"), source="blackboard"))

    flags = blackboard.flags()
    if flags.get("off_task"):
        names = ", ".join(flags["off_task"])
        concerns.append(Concern(
            id="legality:off_task", kind="legality",
            statement=f"off-task element(s) on the board: {names}",
            evidence=f"off_task verdict lists: {names}",
            salience=salience_for("legality"), source="blackboard"))
    if flags.get("illegal_links"):
        concerns.append(Concern(
            id="legality:illegal_links", kind="legality",
            statement="illegal connection(s) present",
            evidence="illegal_links verdict is failing",
            salience=salience_for("legality"), source="blackboard"))

    return cap(concerns)
