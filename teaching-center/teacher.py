"""Teacher console — the API behind the Teaching Center's web UI.

Pure functions over the course directory + the GINI fragment library. Imports only `gini.domain`
(+ `gini.agent.authoring`), which are headless — no Qt, no LLM. That's deliberate:

  * AUTHORING is deterministic here. The teacher picks verified fragments and sees the EXACT ladder
    the student will get. Nothing is invented server-side, so nothing can be ungradable.
  * NUANCE is plain language. The teacher's `intent.notes` travel WITH the mission and are
    interpreted by the game master on the student's machine, where a model already runs. The course
    server therefore never needs an LLM.
  * GRADING is qualitative. Bands (gold/pass/partial/incomplete), concept mastery, attempts and
    time — never points.

Data layout (COURSE_ROOT):
    courses/<course>/manifest.json     lessons/<id>/lesson.yaml
    data/roster.json                   data/submissions.jsonl     data/profiles/<student>.json
"""
from __future__ import annotations

import json
import hashlib
import secrets
from pathlib import Path

import yaml

from gini.domain import assembly as _assembly
from gini.domain import composition as _comp
from gini.domain import fragments as _frag
from gini.domain import grader as _grader
from gini.domain import lesson as _lesson
from gini.domain import objectives as _obj

BANDS = ("gold", "pass", "partial", "incomplete")


# ---- the fragment library (what a teacher can build from) ------------------ #
def fragment_library() -> list[dict]:
    """Every foundational fragment a teacher may compose with — grouped by what it teaches."""
    out = []
    for f in _frag.all_fragments():
        out.append({
            "id": f.id, "summary": f.summary, "teaches": f.teaches, "layer": f.layer,
            "parent": f.parent, "catalog": f.catalog, "spirit": f.spirit,
            "objectives": [{"say": o.say, "level": _obj.level_of(o)} for o in f.objectives],
            "has_live": any(o.kind == "behavioral" for o in f.objectives),
        })
    return sorted(out, key=lambda d: (d["layer"] != "core", d["parent"], d["id"]))


# ---- live preview: exactly what the student will see ----------------------- #
def preview(spec: dict) -> dict:
    """Assemble a draft composition and return the STUDENT-FACING ladder. This is the trust
    mechanism: the teacher never guesses what's graded."""
    problems = _comp.missing_refs(spec)
    if problems:
        return {"ok": False, "problems": problems, "ladder": []}
    try:
        les = _comp.from_composition(spec, lesson_id=spec.get("id") or "draft")
    except _comp.CompositionError as e:
        return {"ok": False, "problems": [str(e)], "ladder": []}
    problems = _lesson.validate(les)

    rungs: dict[int, list] = {}
    for o in les.objectives:
        rungs.setdefault(_obj.level_of(o), []).append(
            {"say": o.say, "live": o.kind == "behavioral"})
    ladder = [{"level": lv, "name": _obj.LEVEL_NAME.get(lv, ""), "objectives": rungs[lv]}
              for lv in sorted(rungs)]
    return {
        "ok": not problems, "problems": problems, "ladder": ladder,
        "title": les.title, "brief": les.brief, "genre": les.genre, "level": les.level,
        "fragments": les.fragments, "needs_run": bool(les.behavioral_ids()),
    }


# ---- authoring: write a lesson + release it -------------------------------- #
class Course:
    def __init__(self, root: str | Path, course: str) -> None:
        self.root = Path(root)
        self.course = course
        self.data = self.root / "data"
        self.data.mkdir(parents=True, exist_ok=True)

    # -- lessons -------------------------------------------------------------- #
    @property
    def _manifest_path(self) -> Path:
        p = self.root / "courses" / self.course / "manifest.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def manifest(self) -> list[dict]:
        p = self._manifest_path
        return json.loads(p.read_text()) if p.exists() else []

    def lesson_spec(self, lesson_id: str) -> dict:
        p = self.root / "lessons" / lesson_id / "lesson.yaml"
        return yaml.safe_load(p.read_text()) if p.exists() else {}

    def lessons(self) -> list[dict]:
        out = []
        for row in self.manifest():
            spec = self.lesson_spec(row["id"])
            out.append({**row, "fragments": spec.get("fragments", []),
                        "genre": spec.get("genre", ""), "brief": spec.get("brief", "")})
        return out

    def save_lesson(self, spec: dict, *, release: str = "", due: str = "",
                    attempts: int = 3) -> dict:
        """Validate, write the pack, and put it in the manifest (= released to the class)."""
        pv = preview(spec)
        if not pv["ok"]:
            return {"ok": False, "problems": pv["problems"]}
        lid = spec["id"]
        p = self.root / "lessons" / lid / "lesson.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)
        p.write_text(text, encoding="utf-8")

        rows = [r for r in self.manifest() if r["id"] != lid]
        rows.append({"id": lid, "title": spec.get("title", lid),
                     "release": release, "due": due, "attempts": int(attempts),
                     "pack_hash": hashlib.sha256(text.encode()).hexdigest()})
        rows.sort(key=lambda r: (r.get("release") or "", r["id"]))
        self._manifest_path.write_text(json.dumps(rows, indent=2) + "\n")
        return {"ok": True, "lesson": lid}

    def delete_lesson(self, lesson_id: str) -> dict:
        rows = [r for r in self.manifest() if r["id"] != lesson_id]
        self._manifest_path.write_text(json.dumps(rows, indent=2) + "\n")
        return {"ok": True}

    # -- roster (enrolment) --------------------------------------------------- #
    @property
    def _roster_path(self) -> Path:
        return self.data / "roster.json"

    def roster(self) -> list[dict]:
        p = self._roster_path
        return json.loads(p.read_text()) if p.exists() else []

    def enrol(self, student_id: str, name: str = "") -> dict:
        rows = [r for r in self.roster() if r["id"] != student_id]
        row = {"id": student_id, "name": name or student_id,
               "token": secrets.token_urlsafe(12)}
        rows.append(row)
        rows.sort(key=lambda r: r["id"])
        self._roster_path.write_text(json.dumps(rows, indent=2) + "\n")
        return row

    def unenrol(self, student_id: str) -> dict:
        rows = [r for r in self.roster() if r["id"] != student_id]
        self._roster_path.write_text(json.dumps(rows, indent=2) + "\n")
        return {"ok": True}

    # -- submissions ---------------------------------------------------------- #
    def submissions(self) -> list[dict]:
        p = self.data / "submissions.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out

    def _resolve_lesson(self, lesson_id: str):
        spec = self.lesson_spec(lesson_id)
        if not spec:
            return None
        try:
            return _comp.from_composition(spec, lesson_id=lesson_id)
        except _comp.CompositionError:
            return None

    # -- progress: the QUALITATIVE gradebook ---------------------------------- #
    def progress(self) -> dict:
        """Roster x lesson bands — server re-graded (a tampered claim is downgraded). No points."""
        subs = self.submissions()
        lessons = [r["id"] for r in self.manifest()]
        roster = self.roster()
        known = {s["id"] for s in roster}
        for s in subs:                                   # include anyone who submitted but isn't enrolled
            if s.get("student") and s["student"] not in known:
                roster.append({"id": s["student"], "name": s["student"] + " (not enrolled)",
                               "token": ""})
                known.add(s["student"])

        gb = _grader.gradebook_official(subs, resolve_lesson=self._resolve_lesson)
        downgraded = 0
        detail: dict = {}
        for s in subs:
            les = self._resolve_lesson(s.get("lesson_id", ""))
            if les is None:
                continue
            try:
                off = _grader.official_result(les, s)
            except Exception:                            # noqa: BLE001
                continue
            if off.regraded_down:
                downgraded += 1
            d = detail.setdefault((s["student"], s["lesson_id"]), {})
            d["attempts"] = max(d.get("attempts", 0), int(s.get("attempt", 1)))
            d["time_s"] = min(d.get("time_s", 1e9), float(s.get("time_taken", 0) or 0))
            d["band"] = _grader.better_band(d.get("band", ""), off.band)

        rows = []
        for st in sorted(roster, key=lambda r: r["id"]):
            cells = []
            for lid in lessons:
                d = detail.get((st["id"], lid), {})
                cells.append({"lesson": lid, "band": gb.band(st["id"], lid) or "",
                              "attempts": d.get("attempts", 0),
                              "time_s": round(d.get("time_s", 0) or 0)})
            cells_done = sum(1 for c in cells if c["band"] in ("gold", "pass"))
            rows.append({"student": st["id"], "name": st.get("name", st["id"]),
                         "cells": cells, "completed": cells_done})
        return {"lessons": lessons, "rows": rows, "downgraded": downgraded,
                "submissions": len(subs)}

    # -- insights: WHAT to reteach (the highest-value view) -------------------- #
    def insights(self) -> dict:
        """Which objectives the class is failing, and which concepts are weak. A gradebook tells you
        WHO is struggling; this tells you WHAT to reteach."""
        subs = self.submissions()
        stuck: dict = {}
        for s in subs:
            lid = s.get("lesson_id", "")
            les = self._resolve_lesson(lid)
            if les is None:
                continue
            says = {o.id: o.say for o in les.objectives}
            results = dict(s.get("objective_results", []))
            for oid, say in says.items():
                key = (lid, oid)
                rec = stuck.setdefault(key, {"lesson": lid, "objective": say,
                                             "level": 0, "failed": 0, "total": 0,
                                             "students": set()})
                obj = next((o for o in les.objectives if o.id == oid), None)
                rec["level"] = _obj.level_of(obj) if obj else 1
                st = s.get("student", "")
                if st in rec["students"]:
                    continue                            # count each student once, best attempt
                rec["students"].add(st)
                rec["total"] += 1
                if results.get(oid) != "met":
                    rec["failed"] += 1

        rows = []
        for rec in stuck.values():
            if not rec["total"]:
                continue
            rows.append({"lesson": rec["lesson"], "objective": rec["objective"],
                         "level": rec["level"], "failed": rec["failed"], "total": rec["total"],
                         "pct": round(100 * rec["failed"] / rec["total"])})
        rows.sort(key=lambda r: (-r["pct"], -r["failed"]))

        # concept mastery across the class, from the authoritative profiles
        concepts: dict = {}
        for p in sorted((self.data / "profiles").glob("*.json")) if (self.data / "profiles").exists() else []:
            try:
                prof = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            for rec in (prof.get("lessons") or {}).values():
                c = rec.get("concept") or "?"
                d = concepts.setdefault(c, {"concept": c, "mastered": 0, "attempted": 0})
                d["attempted"] += 1
                if rec.get("completed"):
                    d["mastered"] += 1
        cons = sorted(concepts.values(), key=lambda d: d["mastered"] / max(1, d["attempted"]))
        return {"stuck": rows[:20], "concepts": cons}
