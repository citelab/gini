#!/usr/bin/env python3
"""GINI Teaching Center — v1.

Four things and nothing else (TEACHING_CENTER_V1_SPEC.md):

    staff        the admin adds and removes teachers
    courses      several, in one portal; everything hangs off a course
    activities   labs: vend codes, gBuilder records the work, submit, read the report
    content      course materials a teacher uploads for students

**No AI.** No model client is imported and no outbound model call is made. That removes an entire
class of failure — "the model timed out", "the model chose badly" — from a system teachers depend
on at deadline time. There is also no observation plan: gBuilder records what the student DID and
the report narrates it, so the account is true by construction.

**No student accounts.** A vended code is the whole interaction. The portal never learns who did
the work; the teacher maps a receipt to a name in their own gradebook, which is the only place that
mapping is needed.

Run:  ./run.sh          (sets PYTHONPATH; gini.domain is a hard dependency)
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import traceback
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("COURSE_ROOT", "./tc-data")).resolve()
PORT = int(os.environ.get("PORT", "8080"))
MATERIALS = ROOT / "materials"

# Point GINI_HOME at the course root before any gini.domain import, so anything it writes is
# course-scoped rather than in the server operator's home.
os.environ.setdefault("GINI_HOME_DIR", str(ROOT))

from . import accounts as _accounts                                       # noqa: E402
from . import activities as _act                                          # noqa: E402
from . import search as _search                                     # noqa: E402
from .store import Store                                            # noqa: E402

_ACCTS = _accounts.Accounts(ROOT)
_STORE = Store(ROOT)

_MAX_UPLOAD = 25 * 1024 * 1024        # a handout, not a video library

# A receipt the server has never seen is USUALLY not a mistake: gBuilder computes it locally from
# the proof, so a student holds a correct receipt from the moment they finish, even if the upload
# has not landed yet. "No such receipt" reads as "your student is lying", which is the wrong
# first thought and the wrong conversation to start.
_NOT_HERE_YET = ("No submission has arrived under that receipt yet. The receipt is generated "
                 "locally when the student finishes, so it is valid even before their work "
                 "reaches this server — gBuilder retries on its own each time it starts. Ask them "
                 "to reopen gBuilder on the network, or check the receipt for a typo.")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _bearer(handler) -> str:
    return handler.headers.get("Authorization", "").removeprefix("Bearer ").strip()


def _number(sent, previous, default, cast):
    """Read a number a browser sent, falling back to what was already stored, then to a default.

    `None` and `""` mean "not supplied" — JSON has no NaN, so a field a browser could not fill
    arrives as null. Anything else must actually parse: raising here is deliberate, so the caller
    can name the field instead of silently storing a zero the teacher never chose.
    """
    for v in (sent, previous):
        if v is None or v == "":
            continue
        return cast(v)
    return cast(default)


class Handler(BaseHTTPRequestHandler):
    server_version = "GINI-TC/1"

    def log_message(self, *a):                 # quiet; the console is the interface
        pass

    # -- identity --------------------------------------------------------- #
    def _who(self) -> dict | None:
        return _ACCTS.whoami(_bearer(self))

    def _is_admin(self) -> bool:
        me = self._who()
        return me is not None and me["role"] == _accounts.ADMIN

    def _may(self, course: str) -> bool:
        """This caller may act on this course. An admin may act on any."""
        me = self._who()
        return me is not None and _STORE.staffs(course, me["who"], me["role"])

    # -- plumbing --------------------------------------------------------- #
    def _send(self, status: int, obj=None, *, text: str | None = None, raw: bytes | None = None,
              ctype: str = "application/json") -> None:
        body = raw if raw is not None else (
            (text if text is not None else json.dumps(obj) if obj is not None else "").encode())
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _q(self, key: str) -> str:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return (q.get(key) or [""])[0]

    def _page(self, name: str) -> None:
        p = Path(__file__).parent / "static" / name          # ships in the wheel; see pyproject
        self._send(200, text=p.read_text(encoding="utf-8"), ctype="text/html; charset=utf-8")

    # ===================================================================== #
    # public + code-authenticated. MUST be dispatched before the console API,
    # which claims every /api/ path and 401s it without a session.
    # ===================================================================== #
    def _open_routes(self) -> bool:
        p = self.path.split("?")[0]

        if p in ("/", "/console", "/console/"):
            self._page("console.html")
            return True
        if p == "/getcode":
            self._page("getcode.html")
            return True

        # --- auth (open by necessity: you cannot authenticate before you have a session) ---
        if p == "/auth/login" and self.command == "POST":
            b = self._body()
            self._send(200, _ACCTS.login(b.get("id", ""), b.get("password", "")))
            return True
        if p == "/auth/claim" and self.command == "POST":
            b = self._body()
            self._send(200, _ACCTS.claim(b.get("id", ""), b.get("claim_token", ""),
                                         b.get("password", "")))
            return True
        if p == "/auth/whoami" and self.command == "GET":
            me = self._who()
            self._send(200, me) if me else self._send(401, {"error": "not signed in"})
            return True
        if p == "/auth/logout" and self.command == "POST":
            _ACCTS.logout(_bearer(self))
            self._send(200, {"ok": True})
            return True

        # --- the student's two endpoints. Code-authenticated, never a session. ---
        if p == "/api/activity" and self.command == "GET":
            self._activity_for_code()
            return True
        if p == "/api/activity/submit" and self.command == "POST":
            self._submit()
            return True

        # --- what this course says about something. Public, like the materials it points at. ---
        if p == "/api/ask" and self.command == "GET":
            self._ask()
            return True

        # --- a course material, by link. Public so a student can open it. ---
        if p.startswith("/m/"):
            self._serve_material(p[3:])
            return True
        return False

    def _activity_for_code(self) -> None:
        """Two shapes: with a code, what gBuilder needs to arm; without, vend one."""
        code = _act.normalize(self._q("code"))
        if code:
            row = _STORE.code(code)
            act = _STORE.activity(row["activity"]) if row else None
            ok, why = _act.check_code(row, act)
            if not ok:
                # Deliberately nothing else: a refusal must not become an oracle for guessing
                # valid codes.
                self._send(403, {"ok": False, "reason": why, "error": _act.message(why)})
                return
            self._send(200, {"ok": True, "activity": act["id"], "title": act["title"],
                             "brief": act.get("brief", ""),
                             "session_minutes": act["session_minutes"],
                             "valid_until": row["valid_until"]})
            return

        act = _STORE.activity(_act.activity_id(self._q("course"), self._q("lab")))
        ok, why = _act.vending_open(act)
        if not ok:
            self._send(200, {"ok": False, "reason": why, "error": _act.message(why),
                             "title": (act or {}).get("title", "")})
            return
        issued = _act.mint_code(act)
        _STORE.code_put(issued)
        from gini.domain.ticket import Ticket
        self._send(200, {"ok": True, "activity": act["id"], "title": act["title"],
                         "brief": act.get("brief", ""),
                         "code": Ticket(issued["code"]).pretty,
                         "vend_until": act["vend_until"], "valid_until": issued["valid_until"],
                         "session_minutes": act["session_minutes"]})

    def _submit(self) -> None:
        b = self._body()
        code = _act.normalize(str(b.get("code") or ""))
        row = _STORE.code(code)
        act = _STORE.activity(row["activity"]) if row else None
        try:
            rec = _act.prepare(b, row, act)
        except _act.Rejected as e:
            self._send(409, {"ok": False, "reason": e.reason, "error": str(e)})
            return
        if not _STORE.submission_put(rec):
            self._send(409, {"ok": False, "reason": _act.DUPLICATE,
                             "error": _act.message(_act.DUPLICATE)})
            return
        _STORE.code_mark_used(code)
        self._send(200, {"ok": True, "receipt": rec["receipt"],
                         "within_session": _act.within_session(rec, act)})

    def _ask(self) -> None:
        """Search one course's released material for a student's question.

        Public, and deliberately so. Students have no accounts — a code is a scope, not an identity
        — and the materials this points at are already served unauthenticated at /m/<id>. Requiring
        a session here would mean giving every student an account, which is the one thing the whole
        design avoids.

        What it will NOT do is leak a draft: an unreleased activity is the teacher's private working
        copy, and its brief describes an assignment nobody has been set yet. `search.rank` drops
        them rather than trusting this to remember.
        """
        course = (self._q("course") or "").strip()
        query = (self._q("q") or "").strip()[:_search.MAX_QUERY]
        if not course:
            return self._send(400, {"ok": False, "reason": "no_course",
                                    "error": "No course was given."})
        if _STORE.course(course) is None:
            # Named plainly, because the fix is in the student's own Settings and nowhere else.
            return self._send(404, {
                "ok": False, "reason": "no_such_course",
                "error": f"This server has no course '{course}'. Check the Course in "
                         f"Settings → Teaching Center."})
        if not query:
            return self._send(200, {"ok": True, "course": course, "hits": []})
        hits = _search.rank(query, _STORE.activities(course), _STORE.materials(course))
        self._send(200, {"ok": True, "course": course, "hits": hits})

    def _accept(self, me: dict, b: dict) -> dict:
        """Take a late submission by hand, from the proof file the student still has.

        The failure this exists for: a student finishes, their code lapses before the upload lands,
        and the proof becomes unacceptable FOR EVER — `expired` is deliberately not in
        `outbox.SETTLED`, so gBuilder keeps retrying something the server will refuse until the end
        of time. They hold a correct receipt for work the Teaching Center has never heard of.

        Verified exactly as a student's submission is: the same `prepare`, the same chain check, the
        same binding of proof to code and topology to proof. Only the CLOCK is waived, and only
        because a member of staff decided to waive it — which is recorded, so the report shows the
        deadline was overridden and by whom. Nothing here is a way to launder a tampered proof
        through a kindness.
        """
        proof = b.get("proof")
        if not isinstance(proof, dict):
            return {"ok": False, "reason": _act.BAD_PROOF,
                    "error": "That file carried no proof."}
        code = _act.normalize(str(proof.get("ticket") or ""))
        row = _STORE.code(code)
        act = _STORE.activity(row["activity"]) if row else None
        if not act:
            # Not a late submission — a proof from somewhere else entirely.
            return {"ok": False, "reason": _act.UNKNOWN_CODE,
                    "error": _act.message(_act.UNKNOWN_CODE)}
        if not self._may(act.get("course", "")):
            return {"ok": False, "error": "That is not your course."}
        try:
            rec = _act.prepare({"proof": proof, "topology": b.get("topology")},
                               row, act, accepted_by=me["who"])
        except _act.Rejected as e:
            return {"ok": False, "reason": e.reason, "error": str(e)}
        if not _STORE.submission_put(rec):
            return {"ok": False, "reason": _act.DUPLICATE,
                    "error": _act.message(_act.DUPLICATE)}
        _STORE.code_mark_used(rec["code"])
        return {"ok": True, "receipt": rec["receipt"], "activity": act["id"],
                "title": act.get("title", ""), "accepted_by": me["who"],
                "within_session": _act.within_session(rec, act)}

    def _serve_material(self, mid: str) -> None:
        m = _STORE.material(mid)
        if not m or m["kind"] != "file":
            self._send(404, {"error": "no such material"})
            return
        f = MATERIALS / m["course"] / m["filename"]
        if not f.exists():
            self._send(404, {"error": "file is missing"})
            return
        ctype = mimetypes.guess_type(m["filename"])[0] or "application/octet-stream"
        self._send(200, raw=f.read_bytes(), ctype=ctype)

    # ===================================================================== #
    # the console API — a STAFF session is required for everything here
    # ===================================================================== #
    def _console_routes(self) -> bool:
        p = self.path.split("?")[0]
        if not p.startswith("/api/"):
            return False
        me = self._who()
        if me is None:
            self._send(401, {"error": "Sign in required."})
            return True

        if self.command == "GET":
            self._console_get(p, me)
        elif self.command == "POST":
            self._console_post(p, me, self._body())
        else:
            self._send(405, {"error": "method not allowed"})
        return True

    def _console_get(self, p: str, me: dict) -> None:
        if p == "/api/staff":
            if not self._is_admin():
                return self._send(403, {"error": "Admins only."})
            return self._send(200, _ACCTS.staff())

        if p == "/api/site":
            if not self._is_admin():
                return self._send(403, {"error": "Admins only."})
            return self._send(200, _STORE.site_stats())

        if p == "/api/courses":
            rows = _STORE.courses(me["who"], me["role"])
            return self._send(200, [{**c, "staff": _STORE.course_staff(c["id"]),
                                     "activities": len(_STORE.activities(c["id"]))}
                                    for c in rows])

        course = self._q("course")
        if p in ("/api/activities", "/api/materials", "/api/submissions"):
            if not self._may(course):
                return self._send(403, {"error": "That is not your course."})
            if p == "/api/activities":
                out = []
                for a in _STORE.activities(course):
                    out.append({**a, "vended": len(_STORE.codes_for(a["id"])),
                                "submitted": len(_STORE.activity_submissions(a["id"]))})
                return self._send(200, out)
            if p == "/api/materials":
                return self._send(200, _STORE.materials(course))
            # Never `code`: the list is for reading, and a code in a response is a code that can
            # be replayed.
            return self._send(200, [{k: s.get(k) for k in
                                     ("receipt", "activity", "ts", "verdict", "artifact_hash",
                                      "student_id", "claimed_at")}
                                    for s in _STORE.course_submissions(course)])

        if p == "/api/submissions/topology":
            return self._download_topology()
        if p == "/api/receipt":
            row = _STORE.submission_by_receipt(self._q("receipt").strip().upper())
            if not row:
                return self._send(404, {"error": _NOT_HERE_YET})
            act = _STORE.activity(row["activity"]) or {}
            if not self._may(act.get("course", "")):
                return self._send(403, {"error": "That is not your course."})
            twins = _STORE.artifact_twins(row.get("artifact_hash", ""), exclude_code=row["code"])
            return self._send(200, _act.report(row, act, twins,
                                              _STORE.claim_attempts(row["receipt"])))

        self._send(404, {"error": f"No endpoint at {p} for {self.command}."})

    def _console_post(self, p: str, me: dict, b: dict) -> None:
        # --- the site as a whole (admin only). Its own branch, NOT under the /api/staff prefix:
        # nesting it there made it unreachable, and an endpoint that silently 404s is worse than
        # one that refuses.
        if p == "/api/site/reset":
            if not self._is_admin():
                return self._send(403, {"error": "Admins only."})
            return self._send(200, self._site_reset(me, b))

        # --- staff (admin only) ---
        if p.startswith("/api/staff"):
            if not self._is_admin():
                return self._send(403, {"error": "Admins only."})
            if p == "/api/staff":
                return self._send(200, _ACCTS.add_staff(b.get("username", ""),
                                                        b.get("role", "teacher")))
            if p == "/api/staff/delete":
                return self._send(200, _ACCTS.remove_staff(b.get("username", "")))
            if p == "/api/staff/role":
                return self._send(200, _ACCTS.set_role(b.get("username", ""),
                                                       b.get("role", "teacher")))

        # --- courses: creating one is the admin's; staffing is too ---
        if p == "/api/courses":
            if not self._is_admin():
                return self._send(403, {"error": "Admins only."})
            cid = (b.get("id") or "").strip().lower()
            if not _ID_RE.match(cid):
                return self._send(200, {"ok": False, "error": "Use letters, digits, . _ - for an "
                                                              "id."})
            if _STORE.course(cid):
                return self._send(200, {"ok": False, "error": "That course already exists."})
            _STORE.put_course({"id": cid, "title": b.get("title", ""), "created": time.time()})
            _STORE.add_staff(cid, me["who"])       # the creator staffs it, or nobody can use it
            return self._send(200, {"ok": True, "id": cid})

        if p in ("/api/courses/staff", "/api/courses/unstaff", "/api/courses/archive"):
            if not self._is_admin():
                return self._send(403, {"error": "Admins only."})
            cid = b.get("course", "")
            if not _STORE.course(cid):
                return self._send(200, {"ok": False, "error": "No such course."})
            if p == "/api/courses/staff":
                _STORE.add_staff(cid, (b.get("username") or "").strip().lower())
            elif p == "/api/courses/unstaff":
                _STORE.remove_staff(cid, (b.get("username") or "").strip().lower())
            else:
                c = dict(_STORE.course(cid))
                c["archived"] = 0 if int(c.get("archived", 0)) else 1
                _STORE.put_course(c)
            return self._send(200, {"ok": True})

        # --- everything below is course-scoped ---
        course = b.get("course", "")
        if not self._may(course):
            return self._send(403, {"error": "That is not your course."})

        if p == "/api/activities/save":
            return self._send(200, self._save_activity(course, b))
        if p == "/api/activities/release":
            return self._send(200, self._set_released(course, b, True))
        if p == "/api/activities/unrelease":
            return self._send(200, self._set_released(course, b, False))
        if p == "/api/materials":
            return self._send(200, self._add_material(course, b))
        if p == "/api/activities/delete":
            return self._send(200, self._delete_activity(course, b))
        if p == "/api/submissions/accept":
            return self._send(200, self._accept(me, b))
        if p == "/api/submissions/claim":
            return self._send(200, self._claim(course, b))
        if p == "/api/materials/delete":
            m = _STORE.material(b.get("id", ""))
            if m and m["course"] == course:
                if m["kind"] == "file":
                    (MATERIALS / m["course"] / m["filename"]).unlink(missing_ok=True)
                _STORE.material_delete(m["id"])
            return self._send(200, {"ok": True})

        self._send(404, {"error": f"No endpoint at {p} for {self.command}."})

    # -- activity lifecycle ------------------------------------------------ #
    def _save_activity(self, course: str, b: dict) -> dict:
        lab = (b.get("lab") or "").strip().lower()
        if not _ID_RE.match(lab):
            return {"ok": False, "error": "Give the lab an id like 'lab1'."}
        aid = _act.activity_id(course, lab)
        prev = _STORE.activity(aid) or {}
        # A browser that doesn't do `datetime-local` sends the raw typed text, and `float()` then
        # raises — which used to take the connection down with it and cost the teacher a lab with
        # no explanation. A field the server cannot read is a thing to say out loud, not to crash on.
        try:
            vend = _number(b.get("vend_until"), prev.get("vend_until"), 0, float)
        except ValueError:
            return {"ok": False, "error": "That deadline could not be read. Pick a date and time."}
        try:
            mins = _number(b.get("session_minutes"), prev.get("session_minutes"), 60, int)
        except ValueError:
            return {"ok": False, "error": "Minutes per attempt must be a number."}
        if mins <= 0:
            return {"ok": False, "error": "Minutes per attempt must be more than zero."}
        _STORE.activity_put({
            "id": aid, "course": course, "lab": lab,
            "title": b.get("title") or prev.get("title") or lab,
            "brief": b.get("brief", prev.get("brief", "")),
            "status": prev.get("status", "draft"),
            "vend_until": vend, "session_minutes": mins,
            "created": prev.get("created") or time.time(),
            "released": prev.get("released", 0)})
        return {"ok": True, "activity": aid, "status": prev.get("status", "draft")}

    def _set_released(self, course: str, b: dict, on: bool) -> dict:
        aid = _act.activity_id(course, (b.get("lab") or "").strip().lower())
        row = _STORE.activity(aid)
        if not row:
            return {"ok": False, "error": "No such activity."}
        row = dict(row)
        if on and not float(row.get("vend_until") or 0):
            # Without it nothing ever closes the activity, and the vending deadline IS the
            # late-submission control.
            return {"ok": False, "error": "Set when codes stop being issued first."}
        row["status"] = "released" if on else "draft"
        row["released"] = time.time() if on else row.get("released", 0)
        _STORE.activity_put(row)
        return {"ok": True, "status": row["status"]}

    def _delete_activity(self, course: str, b: dict) -> dict:
        """Remove a lab. Two gates, for two different mistakes.

        **Typed confirmation** catches the slip: the lab id has to be typed again, so a delete is
        something a teacher decided rather than something their mouse did.

        **Submissions are a hard refusal, not a scarier warning.** Work handed in is the one thing
        in here that cannot be recreated — a student cannot re-run a lab whose deadline has passed,
        and the proof chain is the record they are marked on. No confirmation dialog is worth that,
        so a lab with submissions can only be CLOSED. If it truly has to go, that is a decision to
        make deliberately with the database in front of you, not at 2am in a web form.
        """
        lab = (b.get("lab") or "").strip().lower()
        aid = _act.activity_id(course, lab)
        row = _STORE.activity(aid)
        if not row:
            return {"ok": False, "error": "No such activity."}
        if (b.get("confirm") or "").strip().lower() != lab:
            return {"ok": False, "error": f"Type the lab id ({lab}) to confirm."}

        subs = len(_STORE.activity_submissions(aid))
        if subs:
            return {"ok": False, "error": f"{lab} has {subs} submission"
                                          f"{'' if subs == 1 else 's'} and cannot be deleted — "
                                          f"that is student work you could not get back. Close it "
                                          f"instead to stop new codes."}
        codes = _STORE.codes_delete_for(aid)
        _STORE.activity_delete(aid)
        return {"ok": True, "lab": lab, "codes": codes}

    def _download_topology(self) -> None:
        """Hand the teacher a project file they can open and RUN.

        Written in gBuilder's own project format (`persistence.save_project`), so it opens with no
        conversion step — a report you cannot run is half a report.
        """
        receipt = self._q("receipt").strip().upper()
        row = _STORE.submission_by_receipt(receipt)
        if not row:
            return self._send(404, {"error": _NOT_HERE_YET})
        act = _STORE.activity(row.get("activity", "")) or {}
        if not self._may(act.get("course", "")):
            return self._send(403, {"error": "That is not your course."})
        payload = json.loads(row.get("data") or "{}")
        topo = payload.get("topology")
        if not topo:
            return self._send(404, {"error": "This submission carried no runnable copy — it came "
                                             "from a gBuilder that only sent the proof."})
        # Constants from the module that DEFINES the format, never restated here: a project file
        # written to a slightly wrong shape would open nowhere and blame the teacher. From
        # gini.domain — the package this server actually has. It used to import them from
        # gini.services.persistence, which lives in gini-toolkit and is deliberately never
        # installed beside a Teaching Center, so this endpoint failed on every real deployment.
        from gini.domain.project import FORMAT, PROJECT_EXT, VERSION
        body = json.dumps({"format": FORMAT, "version": VERSION, "topology": topo}, indent=2)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{receipt}-{act.get("lab", "lab")}{PROJECT_EXT}"')
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    # -- resetting the site -------------------------------------------------- #
    def _site_reset(self, me: dict, b: dict) -> dict:
        """Clear the site after a testing phase. The most destructive thing here, so: three gates.

        1. **Admin only** — enforced by the caller.
        2. **The password again.** Not paranoia: a signed-in console left open on a shared desk is
           the exact scenario, and re-authenticating is the only gate a passer-by cannot pass.
        3. **A typed phrase.** Deliberately not the site name or the admin's username, both of
           which are on screen; it has to be typed from the instruction, not copied from the page.

        And before anything is deleted, a snapshot goes to disk. This is meant for test data, but
        the person running it is one wrong browser tab away from a live term.
        """
        if str(b.get("confirm", "")).strip() != "RESET":
            return {"ok": False, "error": 'Type RESET to confirm.'}
        who = me["who"]
        if not _ACCTS.login(who, str(b.get("password", ""))).get("ok"):
            return {"ok": False, "error": "That is not your password."}

        stats = _STORE.site_stats()
        try:
            snap = ROOT / "backups"
            snap.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = snap / f"before-reset-{stamp}.json"
            path.write_text(json.dumps(_STORE.site_snapshot(), indent=2), encoding="utf-8")
        except Exception as e:                                        # noqa: BLE001
            # Refuse rather than proceed. A reset whose safety net failed is exactly the reset
            # that should not happen.
            return {"ok": False, "error": f"Could not write the backup, so nothing was "
                                          f"changed: {e}"}

        drop_courses = bool(b.get("courses"))
        drop_staff = bool(b.get("staff"))
        removed = _STORE.site_reset(courses=drop_courses, staff=drop_staff, keep_account=who)
        if drop_courses:
            # The material FILES, not just their rows.
            import shutil
            for d in (MATERIALS.iterdir() if MATERIALS.exists() else []):
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
        return {"ok": True, "removed": removed, "before": stats, "backup": str(path)}

    # -- claiming ---------------------------------------------------------- #
    def _claim(self, course: str, b: dict) -> dict:
        """Record whose work a receipt is. A STAFF action: the student hands over the receipt, the
        instructor types it in with the student id.

        A second claim under a different id is refused rather than overwritten — the first claim
        stands until a person decides otherwise — but the refusal NAMES the existing claimant,
        because the whole reason to record an id was so a contested receipt can be taken up with
        both students instead of one of them silently losing their evening.
        """
        receipt = str(b.get("receipt") or "").strip().upper()
        student = " ".join(str(b.get("student_id") or "").split())[:64]
        if not receipt or not student:
            return {"ok": False, "error": "Enter both a receipt and a student ID."}
        row = _STORE.submission_by_receipt(receipt)
        if not row:
            return {"ok": False, "reason": _act.NO_SUCH_RECEIPT,
                    "error": _act.message(_act.NO_SUCH_RECEIPT)}
        act = _STORE.activity(row.get("activity", "")) or {}
        if act.get("course") != course:
            return {"ok": False, "error": "That receipt is not from this course."}
        held = (row.get("student_id") or "").strip()
        if held == student:
            return {"ok": True, "receipt": receipt, "student_id": student}   # retyping a done row

        # Go through the store even when the answer is knowable here, because the store is what
        # RECORDS the attempt. Returning early would refuse the second student and forget them,
        # throwing away the one fact that makes the refusal actionable.
        ok, outcome = _STORE.claim(receipt, student, time.time())
        if ok:
            return {"ok": True, "receipt": receipt, "student_id": student}
        if outcome == _act.ALREADY_CLAIMED:
            return {"ok": False, "reason": outcome, "held_by": held,
                    "error": f"That receipt is already recorded as {held}'s work. "
                             f"Both claims are kept, so you can take it up with them."}
        return {"ok": False, "reason": outcome, "error": _act.message(outcome)}

    # -- materials --------------------------------------------------------- #
    def _add_material(self, course: str, b: dict) -> dict:
        kind = "link" if b.get("url") else "file"
        mid = secrets.token_urlsafe(9)
        rec = {"id": mid, "course": course, "kind": kind, "title": b.get("title", ""),
               "uploaded": time.time()}
        if kind == "link":
            rec["url"] = b.get("url", "")
        else:
            import base64
            name = Path(b.get("filename", "file")).name          # never trust a client path
            try:
                blob = base64.b64decode(b.get("data", ""), validate=True)
            except Exception:                                    # noqa: BLE001
                return {"ok": False, "error": "That file could not be read."}
            if len(blob) > _MAX_UPLOAD:
                return {"ok": False, "error": f"Files are limited to "
                                              f"{_MAX_UPLOAD // (1024 * 1024)} MB."}
            d = MATERIALS / course
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{mid}-{name}").write_bytes(blob)
            rec.update(filename=f"{mid}-{name}", size=len(blob), title=rec["title"] or name)
        _STORE.material_put(rec)
        return {"ok": True, "id": mid}

    # -- dispatch ---------------------------------------------------------- #
    def _dispatch(self) -> None:
        """One entry point, and it always answers.

        An exception escaping a handler makes `BaseHTTPRequestHandler` drop the connection, so the
        browser sees a network failure with no status and no body — the console can only say
        "something went wrong", which is useless to the teacher AND useless to whoever has to fix
        it. Answering with the exception text costs nothing here: every caller is authenticated
        staff or a code holder, and the traceback still goes to the server's own console.
        """
        try:
            if self._open_routes() or self._console_routes():
                return
            self._send(404, {"error": f"No endpoint at {self.path.split('?')[0]}."})
        except Exception as e:                                            # noqa: BLE001
            traceback.print_exc()
            try:
                self._send(500, {"ok": False,
                                 "error": f"The server hit an error handling that request: "
                                          f"{type(e).__name__}: {e}"})
            except Exception:                                             # noqa: BLE001
                pass          # the socket is already gone; the traceback above is the record

    def do_GET(self):        # noqa: N802
        self._dispatch()

    def do_POST(self):       # noqa: N802
        self._dispatch()


def _tls_context(cert: str, key: str) -> "ssl.SSLContext":
    """A TLS context for the certificate pair, or a refusal that says which half is wrong.

    TLS 1.2 is the floor. Everything below it is broken in ways that matter for a login form, and
    the only clients here are current browsers and gBuilder's own urllib — nothing that needs 1.0.
    """
    import ssl

    for label, path in (("certificate", cert), ("private key", key)):
        if not Path(path).exists():
            raise SystemExit(f"TLS {label} not found: {path}")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.load_cert_chain(certfile=cert, keyfile=key)
    except ssl.SSLError as e:
        raise SystemExit(f"That certificate and key do not load together: {e}") from e
    except PermissionError as e:
        raise SystemExit(
            f"Cannot read the TLS key ({e}). It is usually root-owned and mode 600 — either run "
            f"as a user that can read it, or copy it somewhere the service account can.") from e
    return ctx


def serve(host: str = "0.0.0.0", port: int = PORT,
          tls_cert: str = "", tls_key: str = "") -> None:
    MATERIALS.mkdir(parents=True, exist_ok=True)

    # TLS is not optional. It used to be, with a printed warning for the reachable case — and a
    # warning is not a control: the server still came up, staff still typed passwords into it, and
    # the 12-hour session token that came back rode every subsequent request in clear text.
    #
    # There is no longer an excuse to skip it, because loopback can have a certificate like
    # anything else: bind 127.0.0.1 with a self-signed cert and the transport is encrypted and
    # authenticated exactly as it is on a public name. The old carve-out existed on the assumption
    # that it could not.
    if bool(tls_cert) != bool(tls_key):
        raise SystemExit("TLS needs BOTH --tls-cert and --tls-key; only one was given.")
    if not tls_cert:
        raise SystemExit(
            "The Teaching Center only serves HTTPS. Pass --tls-cert and --tls-key.\n\n"
            "Staff sign in with a password and get a session token that is good for twelve hours;\n"
            "on plain HTTP both are readable by anyone on the same network, and so is every\n"
            "student's assignment code and submitted work.\n\n"
            "For a local or loopback server, make one (the subjectAltName is required — a bare\n"
            "CN is rejected by OpenSSL 3 and by macOS):\n\n"
            "    printf '[req]\\ndistinguished_name=dn\\nx509_extensions=v3\\nprompt=no\\n"
            "[dn]\\nCN=localhost\\n[v3]\\nsubjectAltName=DNS:localhost,IP:127.0.0.1\\n' > tls.cnf\n"
            "    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \\\n"
            "        -keyout key.pem -out cert.pem -config tls.cnf\n\n"
            "Then trust it on the machines running gBuilder (mkcert does both steps for you, and\n"
            "installs into the system trust store: `mkcert -install && mkcert localhost 127.0.0.1`).\n\n"
            "Terminating TLS in a proxy? Give the backend a loopback certificate too and point the\n"
            "proxy at https://127.0.0.1 — nginx does not verify an upstream certificate by default.")

    ctx = _tls_context(tls_cert, tls_key)
    scheme = "https"

    token = _ACCTS.ensure_admin()
    who = os.environ.get("ADMIN_ID", "admin")
    if host in ("0.0.0.0", "::", ""):
        # `https://0.0.0.0:8443/` is not an address anybody can open. Name the machine instead and
        # say plainly that it is listening everywhere.
        import socket
        shown = socket.getfqdn() or socket.gethostname() or host
        print(f"GINI Teaching Center  ·  {scheme}://{shown}:{port}/   (all interfaces)")
    else:
        shown = "127.0.0.1" if host in ("127.0.0.1", "localhost") else host
        print(f"GINI Teaching Center  ·  {scheme}://{shown}:{port}/")
    print(f"  data     {ROOT}")
    print(f"  tls      {tls_cert}")
    if token:
        print(f"\n  FIRST RUN — claim the admin account:")
        print(f"    username     {who}")
        print(f"    claim token  {token}")
        print(f"  (or set ADMIN_PASSWORD and restart)\n")
    else:
        print(f"  admin    {who}\n")

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # A busy port is an ordinary thing — usually the last copy of this server, still running.
        # A traceback here reads as a bug in the Teaching Center and buries the one useful fact.
        import errno
        if e.errno != errno.EADDRINUSE:
            raise
        raise SystemExit(
            f"Port {port} is already in use, so the Teaching Center cannot start.\n\n"
            f"Usually this is an earlier copy of it that is still running. Find out what has the "
            f"port:\n\n"
            f"    lsof -nP -iTCP:{port} -sTCP:LISTEN\n\n"
            f"Then stop that process, or start this one on a different port with --port.") from e
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
