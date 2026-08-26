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
import time
from pathlib import Path

import yaml

from gini.domain import assembly as _assembly
from gini.domain import composition as _comp
from gini.domain import fragments as _frag
from gini.domain import grader as _grader
from gini.domain import lesson as _lesson
from gini.domain import objectives as _obj

BANDS = ("gold", "pass", "partial", "incomplete")


# ---- fragment registration (author → TC upload, Build 3) ------------------- #
def register_fragment(yaml_text: str) -> dict:
    """Register a teacher-authored fragment uploaded from gBuilder.

    The version gate is the validation itself: the fragment is checked against THIS server's
    vocabulary (its known elements / predicates / probes / capabilities). A primitive the server
    doesn't have — because the fragment was authored on a newer engine — makes validation fail with
    the exact missing thing. That's the honest 'your engine has something mine doesn't' refusal,
    rather than silently mis-composing. On success it lands in the course content layer and is
    immediately available to compose experiments from."""
    from gini.domain import content as _content
    from gini.domain import fragment_yaml as _fy
    from gini.domain import fragments as _frag
    try:
        d = yaml.safe_load(yaml_text)
        frag = _fy.fragment_from_dict(d)
    except Exception as e:                               # noqa: BLE001 — malformed upload
        return {"ok": False, "error": f"Couldn't read the fragment: {e}"}
    if not frag.id:
        return {"ok": False, "error": "Fragment has no id."}
    problems = _fy.validate(frag)                        # ← the version gate, against OUR vocabulary
    if problems:
        return {"ok": False, "error": "This server can't grade it (likely authored on a newer "
                                      "gBuilder): " + "; ".join(problems),
                "engine_version": getattr(_content, "ENGINE_VERSION", "")}
    path = _content.ensure_user_content_dir() / f"{frag.id}.yaml"
    path.write_text(_fy.to_yaml(frag), encoding="utf-8")
    _frag.reload()                                       # available to compose immediately
    return {"ok": True, "id": frag.id,
            "engine_version": getattr(frag, "engine_version", ""),
            "forks": len(frag.forks)}


def vocabulary() -> dict:
    """The server's asset manifest — the discovery protocol. What a fragment may be built from on this
    engine version. Lets an author (or another TC) know what's composable here."""
    from gini.domain import vocabulary as _vocab
    return _vocab.export()


def authored_content() -> list[dict]:
    """The teacher-authored fragments in this course's content layer, for OTA pull by student clients.
    Built-ins are NOT included — students already ship those; the channel carries only what the
    teacher added. Each item is hash-pinned so a client can skip what it already has."""
    import hashlib

    from gini.domain import content as _content
    d = _content.user_content_dir()
    out = []
    if d.exists():
        for p in sorted(d.glob("*.yaml")):
            text = p.read_text(encoding="utf-8")
            try:
                spec = yaml.safe_load(text) or {}
            except Exception:                            # noqa: BLE001 — skip an unreadable file
                continue
            out.append({"id": p.stem, "engine_version": str(spec.get("engine_version", "")),
                        "hash": hashlib.sha256(text.encode()).hexdigest(), "yaml": text})
    return out


def delete_fragment(fragment_id: str) -> dict:
    """Remove a teacher-authored fragment from this course's content layer. This is the source of
    truth clients OTA-pull, so deleting here stops it re-seeding to students (they drop it on next
    sync). Matches by INTERNAL id or filename stem, so a fragment saved with a spaced/mixed-case id
    (e.g. "simple LAN.yaml") is still deletable. Built-ins don't live here, so they're never touched."""
    from gini.domain import content as _content
    from gini.domain import fragments as _frag
    if not fragment_id:
        return {"ok": False, "error": "No fragment id."}
    d = _content.user_content_dir()
    removed = []
    if d.exists():
        for p in list(d.glob("*.yaml")):
            try:
                spec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:                            # noqa: BLE001 — unreadable → try the stem
                spec = {}
            if str(spec.get("id", "")) == fragment_id or p.stem == fragment_id:
                p.unlink(missing_ok=True); removed.append(p.stem)
    _frag.reload()                                       # drop it from what this TC composes with
    if not removed:
        return {"ok": False, "error": f"No authored fragment '{fragment_id}' here to delete."}
    return {"ok": True, "id": fragment_id, "removed": removed}


# ---- the fragment library (what a teacher can build from) ------------------ #
def fragment_library() -> list[dict]:
    """Every foundational fragment a teacher may compose with — grouped by what it teaches. Each is
    flagged `authored` (teacher-added, in this course's content layer → deletable) vs a built-in that
    ships with the engine, and `certified` (runtime-playtested), so the author menu can act on them."""
    from gini.domain import content as _content
    authored_ids: set[str] = set()
    d = _content.user_content_dir()
    if d.exists():
        for p in d.glob("*.yaml"):
            authored_ids.add(p.stem)
            try:
                spec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                authored_ids.add(str(spec.get("id", "")))
            except Exception:                            # noqa: BLE001 — stem alone still flags it
                pass
    out = []
    for f in _frag.all_fragments():
        out.append({
            "id": f.id, "summary": f.summary, "teaches": f.teaches, "layer": f.layer,
            "parent": f.parent, "catalog": f.catalog, "spirit": f.spirit,
            "objectives": [{"say": o.say, "level": _obj.level_of(o)} for o in f.objectives],
            "has_live": any(o.kind == "behavioral" for o in f.objectives),
            "authored": f.id in authored_ids,
            "certified": bool(getattr(f, "certified", False)),
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
        from store import Store
        self.store = Store(root)         # roster/profiles/submissions live here now (lessons stay files)

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
            out.append({**row, "status": row.get("status", "released"),
                        "fragments": spec.get("fragments", []),
                        "genre": spec.get("genre", ""), "brief": spec.get("brief", "")})
        return out

    def released_manifest(self) -> list[dict]:
        """What STUDENTS see — released lessons only. Drafts never reach the student-facing manifest,
        so a work-in-progress experiment can't be played (or seen) until the teacher approves it."""
        return [r for r in self.manifest() if r.get("status", "released") != "draft"]

    def _write_manifest(self, rows: list[dict]) -> None:
        rows.sort(key=lambda r: (r.get("release") or "", r["id"]))
        self._manifest_path.write_text(json.dumps(rows, indent=2) + "\n")

    def save_lesson(self, spec: dict, *, release: str = "", due: str = "",
                    attempts: int = 3, release_now: bool = False) -> dict:
        """Validate + write the pack. By default it's saved as a DRAFT (the approval gate) — the
        teacher playtests the whole experiment, then approves to release. `release_now=True` skips
        the gate (e.g. re-releasing a known-good lesson)."""
        pv = preview(spec)
        if not pv["ok"]:
            return {"ok": False, "problems": pv["problems"]}
        lid = spec["id"]
        p = self.root / "lessons" / lid / "lesson.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)
        p.write_text(text, encoding="utf-8")

        prev = next((r for r in self.manifest() if r["id"] == lid), None)
        pack_hash = hashlib.sha256(text.encode()).hexdigest()
        # A re-save whose pack CHANGED must be re-playtested — the grading loop can't inherit an old
        # sign-off for different content. release_now (a known-good re-release) counts as playtested.
        playtested = bool(release_now) or (prev is not None and prev.get("playtested")
                                           and prev.get("pack_hash") == pack_hash)
        rows = [r for r in self.manifest() if r["id"] != lid]
        rows.append({"id": lid, "title": spec.get("title", lid),
                     "release": release, "due": due, "attempts": int(attempts),
                     "status": "released" if release_now else "draft",
                     "playtested": playtested,
                     "pack_hash": pack_hash})
        self._write_manifest(rows)
        return {"ok": True, "lesson": lid, "status": "released" if release_now else "draft"}

    def mark_playtested(self, lesson_id: str) -> dict:
        """The teacher confirms the composed experiment played correctly on the canvas — the last
        gate before it can be assigned to students (it's part of their grading loop)."""
        rows = self.manifest()
        row = next((r for r in rows if r["id"] == lesson_id), None)
        if row is None:
            return {"ok": False, "error": "No such experiment (save it first)."}
        row["playtested"] = True
        self._write_manifest(rows)
        return {"ok": True, "lesson": lesson_id, "playtested": True}

    def approve_lesson(self, lesson_id: str, *, release: str = "", due: str = "",
                       attempts: int | None = None) -> dict:
        """Release a draft to the class — the teacher's sign-off after playtesting the experiment."""
        rows = self.manifest()
        row = next((r for r in rows if r["id"] == lesson_id), None)
        if row is None:
            return {"ok": False, "error": "No such experiment (save it first)."}
        if not row.get("playtested"):
            return {"ok": False, "error": "Playtest the experiment on the canvas and confirm it "
                    "before releasing — it's part of the students' grading loop."}
        row["status"] = "released"
        if release:
            row["release"] = release
        if due:
            row["due"] = due
        if attempts is not None:
            row["attempts"] = int(attempts)
        self._write_manifest(rows)
        return {"ok": True, "lesson": lesson_id, "status": "released"}

    def unrelease_lesson(self, lesson_id: str) -> dict:
        """Pull a released experiment back to draft — students stop seeing it."""
        rows = self.manifest()
        row = next((r for r in rows if r["id"] == lesson_id), None)
        if row is None:
            return {"ok": False, "error": "No such experiment."}
        row["status"] = "draft"
        self._write_manifest(rows)
        return {"ok": True, "lesson": lesson_id, "status": "draft"}

    def delete_lesson(self, lesson_id: str) -> dict:
        rows = [r for r in self.manifest() if r["id"] != lesson_id]
        self._manifest_path.write_text(json.dumps(rows, indent=2) + "\n")
        return {"ok": True}

    # -- roster (enrolment) --------------------------------------------------- #
    def roster(self) -> list[dict]:
        return self.store.roster()

    def enrol(self, student_id: str, name: str = "", group: str = "",
              ai_hosted: bool = False, sis_id: str = "") -> dict:
        """Enrol (or re-enrol) a student.

        `id` is the USERNAME — the handle the student signs in with and everyone sees ('ravi'). The
        teacher picks it; it just has to be unique in the course. `sis_id` is the school-supplied
        number ('2511') — pure bookkeeping, never a login and never an address. Keeping them separate
        is the whole point: the human handle can be a friendly nickname while the registrar's id
        stays attached for records.

        Re-enrolling KEEPS the existing token, group, sis_id and ai grant unless a new value is given
        — fixing a display name must not invalidate the token a student is about to use."""
        old = self.store.enrolment(student_id) or {}
        row = {"id": student_id, "name": name or old.get("name") or student_id,
               "sis_id": sis_id if sis_id != "" else old.get("sis_id", ""),
               "token": old.get("token") or secrets.token_urlsafe(12),
               "group": group if group != "" else old.get("group", ""),
               # Phase E: may this student's AI be HOSTED on the course server (capacity is the
               # teacher's to give). Not granted → their AI just runs locally, as it already does.
               "ai_hosted": bool(ai_hosted or old.get("ai_hosted", False))}
        self.store.upsert_enrolment(student_id, name=row["name"], sis_id=row["sis_id"],
                                    token=row["token"], group=row["group"],
                                    ai_hosted=row["ai_hosted"])
        return row

    def unenrol(self, student_id: str) -> dict:
        self.store.delete_enrolment(student_id)
        return {"ok": True}

    # -- groups (teacher-formed; optional per class) --------------------------- #
    def set_group(self, student_id: str, group: str) -> dict:
        if self.store.enrolment(student_id) is None:
            return {"ok": False, "error": "No such student."}
        self.store.set_field(student_id, "group", (group or "").strip())
        return {"ok": True, "group": (group or "").strip()}

    def set_ai_hosted(self, student_id: str, on: bool) -> dict:
        if self.store.enrolment(student_id) is None:
            return {"ok": False, "error": "No such student."}
        self.store.set_field(student_id, "ai_hosted", bool(on))
        return {"ok": True, "ai_hosted": bool(on)}

    def groups(self) -> dict:
        """{group_name: [rows]}. Ungrouped students are simply absent — a class with no groups is a
        smaller product, not a broken one."""
        out: dict[str, list] = {}
        for r in self.roster():
            g = (r.get("group") or "").strip()
            if g:
                out.setdefault(g, []).append(r)
        return out

    def group_of(self, student_id: str) -> str:
        row = next((r for r in self.roster() if r["id"] == student_id), None)
        return (row or {}).get("group", "") or ""

    def members_of(self, group: str) -> list[str]:
        if not group:
            return []
        return [r["id"] for r in self.roster() if (r.get("group") or "") == group]

    # -- submissions ---------------------------------------------------------- #
    def submissions(self) -> list[dict]:
        return self.store.submissions()

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
        for r in self.roster():
            prof = self.store.profile(r["id"])
            if not prof:
                continue
            for rec in (prof.get("lessons") or {}).values():
                c = rec.get("concept") or "?"
                d = concepts.setdefault(c, {"concept": c, "mastered": 0, "attempted": 0})
                d["attempted"] += 1
                if rec.get("completed"):
                    d["mastered"] += 1
        cons = sorted(concepts.values(), key=lambda d: d["mastered"] / max(1, d["attempted"]))
        return {"stuck": rows[:20], "concepts": cons}


# --------------------------------------------------------------------------- #
# Activities — free-form assessed work observed against a frozen AOP.
#
# Deliberately mirrors the LESSON lifecycle above rather than inventing a parallel gate: an
# activity is a draft until approved, and changing the plan clears the approval, exactly as a
# changed `pack_hash` clears `playtested`. The reasoning is the same one written there — a sign-off
# cannot be inherited by different content.
#
# These are methods on Course because an activity belongs to a course, like a lesson does. The rows
# live in SQLite rather than in files because codes and submissions need uniqueness constraints
# that a directory cannot give.
# --------------------------------------------------------------------------- #
def _activity_methods():
    """Attached below; kept in one block so the activity surface reads as a unit."""


def activity_id(course: str, lab: str) -> str:
    return f"{course}/{lab}"


def _save_activity(self, lab: str, *, title: str = "", intent: str = "",
                   selection: dict | None = None, plan: dict | None = None,
                   vend_until: float = 0.0, session_minutes: int = 60) -> dict:
    """Write (or rewrite) an activity as a DRAFT.

    A re-save whose PLAN changed drops the activity back to draft even if it was released, because
    the released plan is the instrument students are being measured by and it must not change under
    them silently. Codes already vended keep naming the old plan_hash and stop validating (see
    `activities.check_code`), which is the intended, visible consequence.
    """
    import json as _json

    from gini.domain import aop as _aop

    aid = activity_id(self.course, lab)
    prev = self.store.activity(aid) or {}
    plan_obj = plan if plan is not None else (_json.loads(prev["plan"]) if prev.get("plan") else None)
    plan_hash = _aop.plan_hash(_aop.Aop.from_dict(plan_obj)) if plan_obj else ""

    unchanged = bool(prev) and prev.get("plan_hash") == plan_hash
    rec = {"id": aid, "course": self.course, "lab": lab,
           "title": title or prev.get("title", "") or lab,
           "intent": intent or prev.get("intent", ""),
           "selection": _json.dumps(selection, sort_keys=True) if selection is not None
                        else prev.get("selection", ""),
           "plan": _json.dumps(plan_obj, sort_keys=True) if plan_obj else prev.get("plan", ""),
           "plan_hash": plan_hash,
           "status": prev.get("status", "draft") if unchanged else "draft",
           "vend_until": vend_until or prev.get("vend_until", 0.0),
           "session_minutes": int(session_minutes or prev.get("session_minutes", 60)),
           "created": prev.get("created") or time.time(),
           "released": prev.get("released", 0.0) if unchanged else 0.0}
    self.store.activity_put(rec)
    return {"ok": True, "activity": aid, "status": rec["status"], "plan_hash": plan_hash}


def _release_activity(self, lab: str, *, vend_until: float = 0.0,
                      session_minutes: int = 0) -> dict:
    """Open vending. Refuses an activity with no plan — there would be nothing to observe."""
    aid = activity_id(self.course, lab)
    row = self.store.activity(aid)
    if not row:
        return {"ok": False, "error": "no such activity"}
    if not row.get("plan_hash"):
        return {"ok": False, "error": "this activity has no plan yet"}
    row = dict(row)
    row["status"] = "released"
    row["released"] = time.time()
    if vend_until:
        row["vend_until"] = vend_until
    if session_minutes:
        row["session_minutes"] = int(session_minutes)
    self.store.activity_put(row)
    return {"ok": True, "activity": aid, "status": "released"}


def _unrelease_activity(self, lab: str) -> dict:
    """Close vending. Codes already issued keep working — a student mid-session must not have the
    activity pulled out from under them; the deadline is what ends the activity, not this."""
    aid = activity_id(self.course, lab)
    row = self.store.activity(aid)
    if not row:
        return {"ok": False, "error": "no such activity"}
    row = dict(row)
    row["status"] = "draft"
    self.store.activity_put(row)
    return {"ok": True, "activity": aid, "status": "draft"}


def _activities(self) -> list[dict]:
    """The teacher's index: status, how many codes went out, how many came back.

    `vended` minus `submitted` is expected to be large and is not a problem — a student may take a
    code to look at it, or practise first and take one when ready.
    """
    out = []
    for row in self.store.activities(self.course):
        codes = self.store.codes_for(row["id"])
        out.append({**{k: row[k] for k in
                       ("id", "lab", "title", "status", "vend_until", "session_minutes",
                        "plan_hash")},
                    "vended": len(codes),
                    "submitted": len(self.store.activity_submissions(row["id"]))})
    return out


Course.activity_id = staticmethod(activity_id)
Course.save_activity = _save_activity
Course.release_activity = _release_activity
Course.unrelease_activity = _unrelease_activity
Course.activities = _activities
