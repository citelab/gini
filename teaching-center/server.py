#!/usr/bin/env python3
"""GINI Teaching Center — reference server (skeleton).

The single service both teachers and students talk to: system of record for **Lessons** and
student **Profiles**, and the collector for Mission **submissions**. This is a minimal stdlib
reference implementation of the GINI Learning Protocol (GLP) so the client can be exercised
end-to-end; a production deployment would add a real datastore, a roster, role-based auth, and
(Phase 6) a Docker-capable re-grader for the hybrid grading authority.

Layout it serves from (COURSE_ROOT):
    courses/<course>/manifest.json          # released lessons
    lessons/<lesson_id>/lesson.yaml         # a Lesson Pack (Phase 5 = yaml text)
    data/profiles/<student>.json            # authoritative profiles (monotonic-merged on PUT)
    data/submissions.jsonl                  # appended Mission results

Run:  COURSE_ROOT=./example PORT=8080 python teaching-center/server.py
Auth: a Bearer token is required but accepted as any non-empty string in this skeleton
      (replace with a real enrollment roster).
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("COURSE_ROOT", "./example")).resolve()
PORT = int(os.environ.get("PORT", "8080"))

# Uploaded (teacher-authored) fragments land in the course's own content layer, so the TC composes
# from built-ins + these. Point GINI_HOME at the course root (before any gini.domain import) so the
# content is COURSE-SCOPED, not in the server operator's ~/.gini.
os.environ.setdefault("GINI_HOME_DIR", str(ROOT))

_MANIFEST = re.compile(r"^/courses/([\w-]+)/manifest$")
_PACK = re.compile(r"^/lessons/([\w-]+)/pack$")
_PROFILE = re.compile(r"^/students/([\w-]+)/profile$")
_SUBMIT = re.compile(r"^/courses/([\w-]+)/submissions$")

# --- auth (Phase A) --------------------------------------------------------- #
# Until now a Bearer token was accepted as "any non-empty string", which meant anyone could read any
# student's profile, submit as anyone, and — worst — GET /api/roster, which hands out the enrolment
# tokens for the WHOLE CLASS. That isn't a mild oversight, it's a master key sitting on an open port.
import accounts as _accounts                                        # noqa: E402
from store import Store as _Store                                   # noqa: E402

_ACCTS = _accounts.Accounts(ROOT)
_STORE = _Store(ROOT)
COURSE = os.environ.get("COURSE", "cs4480-fall26")
TEACHER_ID = os.environ.get("TEACHER_ID", "teacher")

_SOCIAL_RE = {
    "presence": re.compile(r"^/courses/([\w-]+)/presence$"),
    "group": re.compile(r"^/courses/([\w-]+)/group$"),
    "channels": re.compile(r"^/courses/([\w-]+)/channels$"),
    "messages": re.compile(r"^/courses/([\w-]+)/messages$"),
    "report": re.compile(r"^/courses/([\w-]+)/messages/report$"),
    "delete": re.compile(r"^/courses/([\w-]+)/messages/delete$"),
    "aipref": re.compile(r"^/courses/([\w-]+)/ai/pref$"),
    "photo": re.compile(r"^/courses/([\w-]+)/photo$"),
    "content": re.compile(r"^/courses/([\w-]+)/content$"),   # OTA: authored fragments to pull
}

_LAZY: dict = {}


def _stack():
    """course + social + ProfAI + StudentAI, built once. Imported lazily so the student endpoints
    keep working even if the AI extras aren't importable."""
    if not _LAZY:
        import ai as _ai
        import social as _social
        import teacher as _teacher
        c = _teacher.Course(ROOT, COURSE)
        s = _social.Social(ROOT, c)
        cap = _ai.Capacity()
        llm = _ai.Ollama(os.environ.get("AI_URL", _ai.OLLAMA_URL),
                         os.environ.get("AI_MODEL", _ai.MODEL))
        _LAZY.update(course=c, social=s,
                     prof=_ai.ProfAI(ROOT, c, s, llm=llm, capacity=cap),
                     student_ai=_ai.StudentAI(ROOT, c, s, llm=llm, capacity=cap))
    return _LAZY


def _bearer(handler) -> str:
    return handler.headers.get("Authorization", "").removeprefix("Bearer ").strip()

_BAND_RANK = {"": 0, "incomplete": 1, "partial": 2, "pass": 3, "gold": 4}


def _merge_profiles(a: dict, b: dict) -> dict:
    """Monotonic union merge (same rule as the client's domain.profile.merge): best-band max,
    attempts max, completed OR, best_time min. Conflict-free because the data is monotonic."""
    out = {"student_id": a.get("student_id") or b.get("student_id"), "lessons": {}}
    la, lb = a.get("lessons", {}), b.get("lessons", {})
    for lid in set(la) | set(lb):
        ra, rb = la.get(lid), lb.get(lid)
        if not ra or not rb:
            out["lessons"][lid] = ra or rb
            continue
        times = [t for t in (ra.get("best_time_s"), rb.get("best_time_s")) if t is not None]
        out["lessons"][lid] = {
            "lesson_id": lid, "concept": ra.get("concept") or rb.get("concept"),
            "best_band": max((ra.get("best_band", ""), rb.get("best_band", "")), key=lambda x: _BAND_RANK.get(x, 0)),
            "attempts_used": max(ra.get("attempts_used", 0), rb.get("attempts_used", 0)),
            "best_time_s": min(times) if times else None,
            "completed": ra.get("completed", False) or rb.get("completed", False),
            "last_played": max(ra.get("last_played", 0), rb.get("last_played", 0)),
            "snapshot": ra.get("snapshot") if ra.get("last_played", 0) >= rb.get("last_played", 0) else rb.get("snapshot", ""),
        }
    return out


def _ticket_pretty(code: str) -> str:
    """A vended code in the grouped form a student reads off the screen."""
    from gini.domain.ticket import Ticket
    return Ticket(code).pretty


def _course_api():
    """The teacher console's API. Imported lazily so the student endpoints keep working even if the
    GINI package isn't importable (e.g. a bare relay deployment)."""
    import teacher
    return teacher


class Handler(BaseHTTPRequestHandler):
    # -- identity ------------------------------------------------------------ #
    def _who(self) -> dict | None:
        """Resolve the caller from their session token, or None."""
        return _ACCTS.whoami(_bearer(self))

    def _authed(self) -> bool:
        return self._who() is not None

    def _is(self, who: str) -> bool:
        """The caller IS this student (or is the teacher, who may act across the course). Without
        this, a valid session for student A could read student B's profile — authentication without
        authorization is just a nicer-looking hole."""
        me = self._who()
        if me is None:
            return False
        return me["role"] == "teacher" or me["who"] == who

    def _teacher(self) -> bool:
        me = self._who()
        return me is not None and me["role"] == "teacher"

    def _send(self, status: int, obj=None, *, text: str | None = None,
              ctype: str = "application/json") -> None:
        body = (text if text is not None else json.dumps(obj) if obj is not None else "").encode()
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

    # -- auth (open by necessity: you can't authenticate before you have a session) ------------- #
    def _auth_routes(self) -> bool:
        p = self.path.split("?")[0]
        if not p.startswith("/auth/"):
            return False
        if self.command == "POST":
            b = self._body()
            if p == "/auth/claim":                  # first login: id + enrolment token + new password
                self._send(200, _ACCTS.claim(b.get("id", ""), b.get("enrolment_token", ""),
                                             b.get("password", "")))
            elif p == "/auth/claim-teacher":
                self._send(200, _ACCTS.claim_teacher(b.get("id", ""), b.get("setup_token", ""),
                                                     b.get("password", "")))
            elif p == "/auth/login":
                self._send(200, _ACCTS.login(b.get("id", ""), b.get("password", "")))
            elif p == "/auth/logout":
                _ACCTS.logout(_bearer(self))
                self._send(200, {"ok": True})
            else:
                self._send(404)
            return True
        if self.command == "GET" and p == "/auth/whoami":
            me = self._who()
            # The course travels with identity because a Teaching Center serves exactly ONE course
            # — it is deployment configuration, not something a teacher picks per activity. The
            # console shows it as a label; making it an input would invite mixing courses in a
            # store that has no notion of doing so.
            return self._send(200, {**me, "course": COURSE}) if me else self._send(401)
        self._send(404)
        return True

    # -- the social plane: presence, groups, chat (Phases B–E) ----------------- #
    def _social_routes(self) -> bool:
        p = self.path.split("?")[0]
        if not any(rx.match(p) for rx in _SOCIAL_RE.values()):
            return False
        me = self._who()
        if me is None:
            self._send(401)
            return True
        S = _stack()
        soc, course, prof, sai = S["social"], S["course"], S["prof"], S["student_ai"]
        who, role = me["who"], me["role"]

        if self.command == "GET":
            if _SOCIAL_RE["group"].match(p):
                self._send(200, soc.my_group(who))
            elif _SOCIAL_RE["channels"].match(p):
                self._send(200, soc.channels(who, role))
            elif _SOCIAL_RE["messages"].match(p):
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                since = float((q.get("since") or ["0"])[0] or 0)
                self._send(200, soc.inbox(who, role, since=since))
            elif _SOCIAL_RE["content"].match(p):        # OTA channel — authored fragments to pull
                self._send(200, _course_api().authored_content())
            else:
                self._send(404)
            return True

        if self.command == "POST":
            b = self._body()
            if _SOCIAL_RE["presence"].match(p):
                self._send(200, soc.heartbeat(who, b.get("progress")))
            elif _SOCIAL_RE["report"].match(p):
                self._send(200, soc.report(who, b.get("message_id", ""), b.get("note", "")))
            elif _SOCIAL_RE["aipref"].match(p):
                if not sai.granted(who):
                    self._send(200, {"ok": False, "error": "Your instructor hasn't enabled a hosted "
                                                           "AI for you."})
                else:
                    self._send(200, sai.set_pref(who, bool(b.get("on")), b.get("blurb", "")))
            elif _SOCIAL_RE["delete"].match(p):
                self._send(200, soc.set_deleted(who, role, b.get("message_id", ""),
                                                bool(b.get("deleted", True))))
            elif _SOCIAL_RE["photo"].match(p):
                self._send(200, _ACCTS.set_photo(who, b.get("photo", "")))
            elif _SOCIAL_RE["messages"].match(p):
                self._send(200, self._post_message(soc, course, prof, sai, who, role, b))
            else:
                self._send(404)
            return True
        return False

    def _post_message(self, soc, course, prof, sai, who, role, b) -> dict:
        """Send a message — and run THE REPLY LADDER, which is the design:

            1. the human is present and answers        (a present human is never pre-empted)
            2. the human is away and allows a proxy    → ProfAI / StudentAI answers, LABELLED
            3. away, no proxy                          → it queues, and says so honestly

        The AI reply is generated inline here for simplicity; it is bounded by the capacity queue, so
        a lab full of students degrades into a line rather than a meltdown."""
        to, body = b.get("to", ""), b.get("body", "")
        res = soc.send(who, to, body, kind="human")
        if not res.get("ok"):
            return res
        if role == "teacher":
            return res                                  # the teacher speaking IS the human reply

        # --- to the instructor -------------------------------------------------
        if to == "teacher":
            if not prof.should_answer(TEACHER_ID):
                return {**res, "ai": None, "note": "Your instructor will see this."}
            reply = prof.answer(who, body)
            posted = soc.reply_to_student(who, reply["body"], from_label="ProfAI", kind="ai",
                                          persona_version=reply.get("persona_version", ""))
            prof.log_answer(who, body, reply, posted["message"]["id"])   # guardrail 3: always logged
            return {**res, "ai": posted["message"]}

        # --- to a groupmate ----------------------------------------------------
        if to not in ("group",) and sai.should_answer(to):
            reply = sai.answer(to, who, body)
            posted = soc.send(to, who, reply["body"], kind="ai", from_label=f"{to}AI")
            return {**res, "ai": posted.get("message")}
        return res

    # -- activities: the public page + the two CODE-authenticated endpoints ---- #
    def _activity_routes(self) -> bool:
        """Everything about activities that is NOT behind a teacher session.

        **This must be dispatched before `_teacher_routes`.** That method claims every path under
        `/api/` and 401s it without a teacher session, so `/api/activity` — which authenticates by
        the student's *code*, not a session — would never reach a handler otherwise.

        These are the only unauthenticated write surfaces in the server, so both are deliberately
        narrow: one reads a released plan for a code the TC itself issued, one accepts a submission
        for that same code. Neither can name a student, and neither reveals whether some *other*
        code would have worked.
        """
        p = self.path.split("?")[0]
        if p not in ("/getcode", "/api/activity", "/api/activity/submit"):
            return False

        import activities as _act
        course = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        one = lambda k: (course.get(k) or [""])[0]                        # noqa: E731

        if p == "/getcode":
            if self.command != "GET":
                return self._send(405, {"error": "GET only"}) or True
            html = (Path(__file__).parent / "static" / "getcode.html").read_text()
            return self._send(200, text=html, ctype="text/html; charset=utf-8") or True

        if p == "/api/activity" and self.command == "GET":
            # Two shapes: without a code it describes the activity and vends one (what the public
            # page calls); with a code it returns the PLAN (what gBuilder calls at arming).
            code = _act.normalize(one("code"))
            if code:
                row = _STORE.code(code)
                act = _STORE.activity(row["activity"]) if row else None
                ok, why = _act.check_code(row, act)
                if not ok:
                    return self._send(403, {"ok": False, "reason": why,
                                            "error": _act.message(why)}) or True
                return self._send(200, {
                    "ok": True, "activity": act["id"], "title": act["title"],
                    "plan": json.loads(act["plan"]), "plan_hash": act["plan_hash"],
                    "session_minutes": act["session_minutes"],
                    "valid_until": row["valid_until"]}) or True

            act = _STORE.activity(_act.activity_id(one("course"), one("lab")))
            ok, why = _act.vending_open(act)
            if not ok:
                return self._send(200, {"ok": False, "reason": why,
                                        "error": _act.message(why),
                                        "title": (act or {}).get("title", "")}) or True
            issued = _act.mint_code(act)
            _STORE.code_put(issued)
            return self._send(200, {
                "ok": True, "activity": act["id"], "title": act["title"],
                "code": _ticket_pretty(issued["code"]),
                "vend_until": act["vend_until"], "valid_until": issued["valid_until"],
                "session_minutes": act["session_minutes"]}) or True

        if p == "/api/activity/submit" and self.command == "POST":
            body = self._body()
            code = _act.normalize(str(body.get("code") or ""))
            row = _STORE.code(code)
            act = _STORE.activity(row["activity"]) if row else None
            try:
                rec = _act.prepare(body, row, act)
            except _act.Rejected as e:
                return self._send(409, {"ok": False, "reason": e.reason, "error": str(e)}) or True
            if not _STORE.submission_put(rec):
                # Uniqueness is the schema's, not a prior read's: two submissions racing would both
                # pass a check-then-insert, and the loser must be told rather than silently lost.
                return self._send(409, {"ok": False, "reason": _act.DUPLICATE,
                                        "error": _act.message(_act.DUPLICATE)}) or True
            _STORE.code_mark_used(code)
            return self._send(200, {"ok": True, "receipt": rec["receipt"],
                                    "within_session": _act.within_session(rec, act)}) or True

        return self._send(405, {"error": "method not allowed here"}) or True

    # -- drafting an observation plan (the one model-touching route) ---------- #
    def _draft_activity(self, b: dict) -> None:
        """Draft a plan, streaming progress as newline-delimited JSON.

        The model streams into the SERVER, so without this the browser posts once and waits in
        silence — and a model producing beautifully looks exactly like a hung one. Progress is
        therefore driven by REAL tokens: if generation stalls, the indicator stalls. A CSS spinner
        would keep spinning, which is worse than no indicator because it is confidently wrong.

        NDJSON rather than SSE: both ends are ours, `event:` framing buys nothing, and the Ollama
        reader upstream already speaks NDJSON, so the whole path uses one shape.

        Line kinds:
          {"t":"phase","n":1,"label":…}  a new model call started
          {"t":"tick","chars":N}         tokens arrived — the liveness signal
          {"t":"say","text":…}           human-readable prose, streamed as it is written
          {"t":"done","result":{…}}      the payload the non-streaming route would have returned
        """
        import ai as _ai
        from gini.agent import aop_selector as _sel

        streaming = bool(b.get("stream"))
        if streaming:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

        def emit(**line) -> None:
            if not streaming:
                return
            try:
                self.wfile.write((json.dumps(line) + "\n").encode())
                self.wfile.flush()          # or the browser sees nothing until the end
            except (BrokenPipeError, ConnectionResetError):
                raise                        # the teacher navigated away; stop working

        _llm = _stack()["prof"].llm if _stack().get("prof") else _ai.Ollama()

        # Phase labels by call index. The selector may call the model up to three times — draft,
        # repair an invalid selection, reconsider after the Twin objects — and "reconsidering its
        # choice" is far more reassuring to watch than an unlabelled spinner.
        LABELS = {1: "Reading that and choosing what to watch",
                  2: "Reconsidering — something did not add up",
                  3: "One more pass"}
        calls = {"n": 0}

        def _json_call(prompt: str) -> str:
            calls["n"] += 1
            emit(t="phase", n=calls["n"],
                 label=LABELS.get(calls["n"], "Still working on the plan"))
            return _llm.chat("", prompt, json_mode=True, num_predict=800,
                             on_chunk=lambda c: emit(t="tick", chars=len(c)))

        def _prose_call(prompt: str) -> str:
            emit(t="phase", n=0, label="Writing you a plain-English summary")
            # The ONLY call whose output is meant for a human, so it is the only one worth showing
            # verbatim. Streaming raw JSON tokens from the selection call would be noise.
            return _llm.chat("", prompt, num_predict=400,
                             on_chunk=lambda c: emit(t="say", text=c))

        try:
            d = _sel.draft(b.get("intent", ""), _json_call,
                           params={"starting_point": "blank",
                                   "guidance": bool(b.get("guidance"))},
                           answers=tuple(b.get("answers") or ()),
                           feedback=tuple(b.get("feedback") or ()),
                           deadline_s=b.get("deadline_s"))
            if not d.ok:
                result = {"ok": False, "error": d.error, "questions": d.questions, "note": d.note}
            else:
                from gini.domain import aop_assemble as _asm
                plan = _asm.assemble(d.selection, gini_version="tc")
                result = {"ok": True, "note": d.note, "questions": d.questions,
                          "objections": [{"question": o.question,
                                          "concern": {"statement": o.concern.statement,
                                                      "evidence": o.concern.evidence}}
                                         for o in d.objections],
                          "coverage_silent": d.coverage_silent,
                          "selection": d.selection.to_dict(), "plan": plan.to_dict(),
                          "describe": _asm.describe(plan),
                          "prose": _sel.back_translate(plan, _prose_call)}
        except _ai.ModelTooSlow as e:
            result = {"ok": False, "error": str(e)}
        except TimeoutError:
            result = {"ok": False, "error": ("The model went silent. Nothing arrived at all — "
                                             "check that Ollama is running and the model is "
                                             "pulled.")}
        except Exception as e:                       # noqa: BLE001 — never 500 at a teacher
            result = {"ok": False, "error": f"The model could not be reached: {e}"}

        if streaming:
            emit(t="done", result=result)
        else:
            self._send(200, result)

    # -- teacher console (UI + API) — TEACHER SESSION REQUIRED ---------------- #
    def _teacher_routes(self) -> bool:
        p = self.path.split("?")[0]
        if p in ("/", "/teacher", "/teacher/"):
            html = (Path(__file__).parent / "static" / "teacher.html").read_text()
            self._send(200, text=html, ctype="text/html; charset=utf-8")
            return True
        if not p.startswith("/api/"):
            return False
        # The console's API is the master key (it serves the class's enrolment tokens). Nothing here
        # is readable without a TEACHER session — not even a listing.
        if not self._teacher():
            self._send(401, {"error": "Teacher sign-in required."})
            return True
        t = _course_api()
        course = os.environ.get("COURSE", "cs4480-fall26")
        C = t.Course(ROOT, course)
        if self.command == "GET":
            if p == "/api/fragments":
                self._send(200, t.fragment_library())
            elif p == "/api/vocabulary":            # the discovery protocol (asset manifest)
                self._send(200, t.vocabulary())
            elif p == "/api/lessons":
                self._send(200, C.lessons())
            elif p == "/api/activities":
                self._send(200, C.activities())
            elif p == "/api/activities/receipt":
                # The teacher's lookup: a student hands over a receipt, this returns everything
                # recorded under it — plus any OTHER submission built from the same topology, which
                # is the collusion signal a receipt alone cannot see.
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                got = _STORE.submission_by_receipt((q.get("receipt") or [""])[0].strip().upper())
                if not got:
                    self._send(404, {"error": "No submission with that receipt."})
                else:
                    self._send(200, {**got, "data": json.loads(got.get("data") or "{}"),
                                     "twins": _STORE.artifact_twins(got.get("artifact_hash", ""),
                                                                    exclude_code=got["code"])})
            elif p == "/api/roster":
                # enrich each row with a live-status + photo so the console can show real faces and
                # who's on right now. Read-only join over presence + accounts.
                S = _stack()
                rows = []
                for r in C.roster():
                    pr = S["social"].presence_of(r["id"])
                    rows.append({**r, "online": pr["online"], "last_seen": pr["last_seen"],
                                 "progress": pr["progress"], "photo": _ACCTS.photo(r["id"]),
                                 "claimed": _ACCTS.store.account(r["id"]) is not None})
                self._send(200, rows)
            elif p == "/api/progress":
                self._send(200, C.progress())
            elif p == "/api/insights":
                self._send(200, C.insights())
            elif p == "/api/groups":
                self._send(200, C.groups())
            elif p == "/api/review":                    # guardrail 3: every ProfAI answer, reviewable
                self._send(200, _stack()["prof"].review_queue())
            elif p == "/api/persona":
                self._send(200, _stack()["prof"].persona.get())
            elif p == "/api/digest":                    # the situation report, not a transcript
                self._send(200, _stack()["prof"].digest())
            elif p == "/api/reports":                   # messages students CHOSE to show you
                self._send(200, _stack()["social"].reports())
            elif p == "/api/messages":                  # the Gmail-style mailbox (incl. trash)
                self._send(200, _stack()["social"].inbox(TEACHER_ID, "teacher", include_deleted=True))
            else:
                self._send(404)
            return True
        if self.command == "POST":
            b = self._body()
            S = _stack()
            if p == "/api/preview":
                self._send(200, t.preview(b.get("spec") or {}))
            elif p == "/api/fragments":             # author → TC upload (Build 3)
                self._send(200, t.register_fragment(b.get("yaml", "")))
            elif p == "/api/fragments/delete":      # remove an authored fragment from the library
                self._send(200, t.delete_fragment(b.get("id", "")))
            elif p == "/api/lessons":
                # save as a DRAFT by default (the approval gate); release_now skips it
                self._send(200, C.save_lesson(b.get("spec") or {}, release=b.get("release", ""),
                                              due=b.get("due", ""), attempts=b.get("attempts", 3),
                                              release_now=bool(b.get("release_now", False))))
            elif p == "/api/activities/draft":
                self._draft_activity(b)
            elif p == "/api/activities/save":
                self._send(200, C.save_activity(
                    b.get("lab", ""), title=b.get("title", ""), intent=b.get("intent", ""),
                    selection=b.get("selection"), plan=b.get("plan"),
                    vend_until=float(b.get("vend_until") or 0),
                    session_minutes=int(b.get("session_minutes") or 60)))
            elif p == "/api/activities/release":
                self._send(200, C.release_activity(
                    b.get("lab", ""), vend_until=float(b.get("vend_until") or 0),
                    session_minutes=int(b.get("session_minutes") or 0)))
            elif p == "/api/activities/unrelease":
                self._send(200, C.unrelease_activity(b.get("lab", "")))
            elif p == "/api/lessons/playtest":       # teacher confirmed a canvas playtest
                self._send(200, C.mark_playtested(b.get("id", "")))
            elif p == "/api/lessons/approve":
                self._send(200, C.approve_lesson(b.get("id", ""), release=b.get("release", ""),
                                                 due=b.get("due", ""), attempts=b.get("attempts")))
            elif p == "/api/lessons/unrelease":
                self._send(200, C.unrelease_lesson(b.get("id", "")))
            elif p == "/api/lessons/delete":
                self._send(200, C.delete_lesson(b.get("id", "")))
            elif p == "/api/roster":
                self._send(200, C.enrol(b.get("id", ""), b.get("name", ""),
                                        group=b.get("group", ""),
                                        ai_hosted=bool(b.get("ai_hosted", False)),
                                        sis_id=b.get("sis_id", "")))
            elif p == "/api/roster/delete":
                self._send(200, C.unenrol(b.get("id", "")))
            elif p == "/api/roster/group":
                self._send(200, C.set_group(b.get("id", ""), b.get("group", "")))
            elif p == "/api/roster/ai":                 # Phase E: grant hosted-AI capacity
                self._send(200, C.set_ai_hosted(b.get("id", ""), bool(b.get("on"))))
            elif p == "/api/roster/reset":              # un-claim an account (student forgot password)
                self._send(200, _ACCTS.reset(b.get("id", "")))
            elif p == "/api/persona":
                self._send(200, S["prof"].persona.save(b))
            elif p == "/api/review/correct":
                self._send(200, self._correct(S, b))
            elif p == "/api/reply":                     # a plain instructor reply from the mailbox
                to = b.get("to", "") or f"teacher:{b.get('student', '')}"
                self._send(200, S["social"].post_to_channel(to, b.get("body", ""),
                                                            from_label="Prof", kind="human"))
            elif p == "/api/messages/delete":
                self._send(200, S["social"].set_deleted(TEACHER_ID, "teacher",
                                                        b.get("message_id", ""),
                                                        bool(b.get("deleted", True))))
            else:
                self._send(404)
            return True
        return False

    def _correct(self, S, b) -> dict:
        """THE CORRECTION LOOP — how the persona improves without prompt engineering.

        The teacher rewrites a ProfAI answer. The correction is posted to the student's thread **as
        Prof** (so the student gets the real answer from the real authority), the AI answer is marked
        reviewed, and — if asked — the correction is promoted into the persona as a standing answer,
        so the next student who asks the same thing gets the teacher's words, not a guess."""
        student = b.get("student", "")
        correction = (b.get("correction") or "").strip()
        if not (student and correction):
            return {"ok": False, "error": "Need a student and a correction."}
        # posted as HUMAN: this is the instructor speaking for themselves, and the student must be
        # able to see that the real authority has now answered.
        posted = S["social"].reply_to_student(student, correction, from_label="Prof", kind="human")
        S["prof"].mark_reviewed(b.get("message_id", ""))
        if b.get("promote") and b.get("question"):
            S["prof"].persona.add_standing_answer(b["question"], correction)
        return {"ok": True, "message": posted["message"]}

    def do_GET(self):  # noqa: N802
        # _activity_routes FIRST: _teacher_routes claims every /api/ path and 401s it without a
        # teacher session, which would swallow the code-authenticated endpoints.
        if (self._activity_routes() or self._auth_routes()
                or self._teacher_routes() or self._social_routes()):
            return
        if not self._authed():
            return self._send(401)
        m = _MANIFEST.match(self.path)
        if m:
            # STUDENT-facing manifest: released lessons only. A draft experiment (awaiting the
            # teacher's playtest + approval) is never sent to students.
            p = ROOT / "courses" / m.group(1) / "manifest.json"
            rows = json.loads(p.read_text()) if p.exists() else []
            return self._send(200, [r for r in rows if r.get("status", "released") != "draft"])
        m = _PACK.match(self.path)
        if m:
            p = ROOT / "lessons" / m.group(1) / "lesson.yaml"
            return self._send(200, text=p.read_text()) if p.exists() else self._send(404)
        m = _PROFILE.match(self.path)
        if m:
            if not self._is(m.group(1)):        # a session for A must not read B's profile
                return self._send(403, {"error": "That isn't your profile."})
            prof = _STORE.profile(m.group(1))
            return self._send(200, prof or {"student_id": m.group(1), "lessons": {}})
        self._send(404)

    def do_PUT(self):  # noqa: N802
        if not self._authed():
            return self._send(401)
        m = _PROFILE.match(self.path)
        if not m:
            return self._send(404)
        if not self._is(m.group(1)):            # …nor write it
            return self._send(403, {"error": "That isn't your profile."})
        incoming = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        existing = _STORE.profile(m.group(1)) or {"student_id": m.group(1), "lessons": {}}
        _STORE.put_profile(m.group(1), _merge_profiles(existing, incoming))
        self._send(204)

    def do_POST(self):  # noqa: N802
        if (self._activity_routes() or self._auth_routes()
                or self._teacher_routes() or self._social_routes()):
            return
        me = self._who()
        if me is None:
            return self._send(401)
        m = _SUBMIT.match(self.path)
        if not m:
            return self._send(404)
        rec = self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
        # A submission is a CLAIM ABOUT A PERSON, so the server decides who that person is — the
        # client doesn't get to say. Otherwise anyone could file results under someone else's name.
        try:
            obj = json.loads(rec.decode() or "{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "malformed submission"})
        obj["student"] = me["who"]
        _STORE.add_submission(obj)
        self._send(201, {"ok": True})

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    setup = _ACCTS.ensure_teacher()
    print(f"GINI Teaching Center (reference) on :{PORT}, serving {ROOT}")
    if setup:
        tid = os.environ.get("TEACHER_ID", "teacher")
        print("\n  ⚠  No teacher account yet. Claim it ONCE, from the console sign-in:")
        print(f"       teacher id : {tid}")
        print(f"       setup token: {setup}")
        print("     (or set TEACHER_ID / TEACHER_PASSWORD in the environment)\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
