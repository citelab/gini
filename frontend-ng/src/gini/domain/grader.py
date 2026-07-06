"""Server-side re-grade + gradebook — the buildable slice of the hybrid grading authority.

The client grades a Mission instantly for feedback and submits the canvas **snapshot** + its
reported objective results. To make the OFFICIAL result trustworthy, the Teaching Center re-grades
deterministically:

  • STRUCTURAL objectives — re-evaluated server-side from the submitted snapshot (pure graph
    predicates, NO Docker). This is fully authoritative and catches tampering: if the client
    claimed a structural objective met but the snapshot doesn't satisfy it, the server wins.
  • BEHAVIORAL objectives — re-running them needs GINI's runtime (Docker); until that grader tier
    exists (Phase 6.x, Mac/server-side), the server *trusts* the client's reported behavioral
    results. So this module makes structural grading authoritative and leaves behavioral as a
    documented trust boundary.

Plus a `Gradebook` that aggregates submissions into the teacher's roster×lesson view. Pure Python;
no Qt, no Docker, no network — fully unit-testable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

from . import objectives as _obj
from . import scoring as _scoring
from .profile import band_rank, better_band


# -- snapshot → world (no live Topology needed) ----------------------------- #
def snapshot_of(topology) -> dict:
    """Serialize the live topology to a portable snapshot (what a submission carries)."""
    return topology.to_dict()


def world_from_snapshot(snapshot) -> _obj.TopologyWorld:
    """Build the objective World from a serialized snapshot (dict or JSON string), so structural
    predicates can be re-evaluated server-side without reconstructing the full Topology model."""
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    devices = {}
    for d in snapshot.get("devices", []):
        devices[d["id"]] = SimpleNamespace(
            id=d["id"], name=d.get("name", ""), type_key=d.get("type_key", ""),
            parent_id=d.get("parent_id"), properties=d.get("properties", {}) or {})
    links = {}
    for i, l in enumerate(snapshot.get("links", [])):
        lid = l.get("id", f"l{i}")
        links[lid] = SimpleNamespace(source_id=l["source_id"], target_id=l["target_id"])
    return _obj.TopologyWorld(SimpleNamespace(devices=devices, links=links))


# -- re-grade --------------------------------------------------------------- #
def regrade_structural(lesson, snapshot) -> list:
    """Re-evaluate the lesson's STRUCTURAL objectives from the snapshot (behavioral → pending)."""
    return _obj.evaluate_all(lesson.objectives, world_from_snapshot(snapshot), runner=None)


@dataclass
class OfficialResult:
    band: str
    server_results: list                 # per-objective (structural authoritative, behavioral from client)
    regraded_down: bool                  # official band < the band the client claimed
    note: str = ""


def official_result(lesson, submission) -> OfficialResult:
    """Compute the official result from a submission {snapshot, objective_results, time_taken, band}.

    Structural objectives come from the server re-grade (authoritative); behavioral objectives are
    trusted from the client's reported results (until the Docker re-grader lands)."""
    client = dict(submission.get("objective_results", []))     # id -> status
    server_struct = {r.id: r for r in regrade_structural(lesson, submission.get("snapshot", {}))}

    merged = []
    for o in lesson.objectives:
        if o.kind == "behavioral":
            status = client.get(o.id, _obj.PENDING)            # trust the client for behavioral
            merged.append(_obj.ObjectiveResult(o.id, o.say, o.kind, status))
        else:
            merged.append(server_struct.get(
                o.id, _obj.ObjectiveResult(o.id, o.say, o.kind, _obj.UNMET)))

    limit = lesson.time_limit_s
    on_time = limit is None or float(submission.get("time_taken", 0)) <= limit
    sc = _scoring.score(merged, complete_when=lesson.complete_when, on_time=on_time)

    claimed = submission.get("band", "")
    down = band_rank(sc.band) < band_rank(claimed)
    note = ("official band is lower than the client claimed — structural re-grade disagreed"
            if down else "")
    return OfficialResult(band=sc.band, server_results=merged, regraded_down=down, note=note)


# -- gradebook -------------------------------------------------------------- #
@dataclass
class Gradebook:
    # student -> lesson_id -> best band
    rows: dict

    def band(self, student: str, lesson_id: str) -> str:
        return self.rows.get(student, {}).get(lesson_id, "")

    def students(self) -> list:
        return sorted(self.rows)

    def lessons(self) -> list:
        out = set()
        for lessons in self.rows.values():
            out.update(lessons)
        return sorted(out)

    def completed_count(self, student: str) -> int:
        return sum(1 for b in self.rows.get(student, {}).values()
                   if band_rank(b) >= band_rank("pass"))


def gradebook(submissions) -> Gradebook:
    """Aggregate raw submissions into a roster×lesson best-band table (the teacher face)."""
    rows: dict[str, dict[str, str]] = {}
    for s in submissions:
        student = s.get("student", "")
        lesson_id = s.get("lesson_id", "")
        if not student or not lesson_id:
            continue
        cur = rows.setdefault(student, {}).get(lesson_id, "")
        rows[student][lesson_id] = better_band(cur, s.get("band", ""))
    return Gradebook(rows=rows)
