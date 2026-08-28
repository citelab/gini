"""The v1 Teaching Center over a real socket.

Driven through an actual `ThreadingHTTPServer` rather than by calling handler methods, because the
thing most likely to break here is *dispatch order*, and dispatch only exists when a real request
walks the chain. Specifically: `_console_routes` claims every `/api/` path and 401s it without a
staff session, so the two code-authenticated endpoints must be intercepted before it. A unit test
that called the handler directly would never notice — and the symptom in the field is that no
student can submit anything, at deadline time.

The rest of what is covered here is the acceptance list: roles, course isolation, deadlines,
duplicates, and the promise that no student identity is stored anywhere.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

HOUR = 3600.0


@pytest.fixture
def tc(tmp_path, monkeypatch):
    """A whole Teaching Center on a loopback port, with its own database."""
    monkeypatch.setenv("COURSE_ROOT", str(tmp_path))
    monkeypatch.setenv("ADMIN_ID", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    for mod in ("server", "accounts", "activities", "store"):
        sys.modules.pop(mod, None)
    from store import Store
    Store._instances.clear()

    import server                                                   # noqa: PLC0415
    server.ROOT = tmp_path
    server.MATERIALS = tmp_path / "materials"
    server.MATERIALS.mkdir(parents=True, exist_ok=True)
    import accounts as A                                            # noqa: PLC0415
    server._ACCTS = A.Accounts(tmp_path)
    server._STORE = Store(tmp_path)
    server._ACCTS.ensure_admin()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield _Client(base, server)
    finally:
        httpd.shutdown()
        httpd.server_close()


class _Client:
    def __init__(self, base, server):
        self.base, self.server, self.token = base, server, ""

    def call(self, path, body=None, *, token=None, method=None):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + (self.token if token is None else token)},
            method=method or ("POST" if body is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"null")
            except json.JSONDecodeError:
                return e.code, {"raw": raw.decode(errors="replace")}

    def page(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.read().decode()

    def signin(self, who, pw):
        _, r = self.call("/auth/login", {"id": who, "password": pw})
        assert r.get("ok"), r
        self.token = r["session"]
        return r


def _released_lab(tc, course="comp535", lab="lab1", *, vend_until=None, minutes=60):
    tc.call("/api/courses", {"id": course, "title": course.upper()})
    tc.call("/api/activities/save",
            {"course": course, "lab": lab, "title": f"{lab} title",
             "brief": "Join two LANs.", "session_minutes": minutes,
             "vend_until": vend_until if vend_until is not None else time.time() + HOUR})
    s, r = tc.call("/api/activities/release", {"course": course, "lab": lab})
    assert r.get("ok"), r


# -- the dispatch trap -------------------------------------------------------- #
def test_the_student_endpoints_are_reachable_without_a_session(tc):
    """THE regression. `/api/` is claimed by the console router; these two must be intercepted
    first or no student can start or submit anything."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    status, r = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    assert status == 200 and r["ok"], r
    assert r["code"]


def test_a_console_endpoint_still_needs_a_session(tc):
    status, r = tc.call("/api/courses", token="")
    assert status == 401


def test_a_bad_session_is_not_a_session(tc):
    assert tc.call("/api/courses", token="not-a-real-token")[0] == 401


# -- roles -------------------------------------------------------------------- #
def test_the_initial_password_signs_in_an_admin(tc):
    assert tc.signin("boss", "correct-horse")["role"] == "admin"


def test_a_teacher_cannot_reach_the_staff_routes(tc):
    """The acceptance criterion. A teacher who could add teachers is an admin."""
    tc.signin("boss", "correct-horse")
    _, added = tc.call("/api/staff", {"username": "ada", "role": "teacher"})
    assert added["ok"], added
    _, claimed = tc.call("/auth/claim", {"id": "ada", "claim_token": added["claim_token"],
                                         "password": "a-good-password"})
    assert claimed["ok"] and claimed["role"] == "teacher"
    assert tc.call("/api/staff", token=claimed["session"])[0] == 403
    assert tc.call("/api/staff", {"username": "eve"}, token=claimed["session"])[0] == 403
    assert tc.call("/api/courses", {"id": "x"}, token=claimed["session"])[0] == 403


def test_an_account_cannot_be_claimed_without_its_token(tc):
    """Usernames are guessable; without this, whoever reaches the portal first becomes a teacher."""
    tc.signin("boss", "correct-horse")
    tc.call("/api/staff", {"username": "ada"})
    _, r = tc.call("/auth/claim", {"id": "ada", "claim_token": "guess", "password": "12345678"})
    assert not r["ok"]


def test_the_last_admin_cannot_be_removed(tc):
    """Otherwise the portal is left with no way in, and no recovery procedure exists."""
    tc.signin("boss", "correct-horse")
    _, r = tc.call("/api/staff/delete", {"username": "boss"})
    assert not r["ok"] and "only admin" in r["error"]


# -- courses ------------------------------------------------------------------ #
def test_two_courses_coexist_and_do_not_see_each_other(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc, "comp535", "lab1")
    _released_lab(tc, "comp557", "lab1")
    _, a = tc.call("/api/activities?course=comp535")
    assert [x["id"] for x in a] == ["comp535/lab1"]


def test_a_teacher_cannot_read_a_course_they_do_not_run(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc, "comp535", "lab1")
    _, added = tc.call("/api/staff", {"username": "ada"})
    _, ada = tc.call("/auth/claim", {"id": "ada", "claim_token": added["claim_token"],
                                     "password": "a-good-password"})
    assert tc.call("/api/activities?course=comp535", token=ada["session"])[0] == 403


def test_staffing_a_course_grants_access(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc, "comp535", "lab1")
    _, added = tc.call("/api/staff", {"username": "ada"})
    _, ada = tc.call("/auth/claim", {"id": "ada", "claim_token": added["claim_token"],
                                     "password": "a-good-password"})
    tc.call("/api/courses/staff", {"course": "comp535", "username": "ada"})
    status, rows = tc.call("/api/activities?course=comp535", token=ada["session"])
    assert status == 200 and len(rows) == 1


# -- vending ------------------------------------------------------------------ #
def test_a_draft_lab_vends_nothing(tc):
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    tc.call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "t",
                                     "vend_until": time.time() + HOUR})
    _, r = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    assert not r["ok"] and r["reason"] == "not_released"


def test_a_lab_cannot_be_released_without_a_vending_deadline(tc):
    """The deadline IS the late-submission control; releasing without one opens a door that never
    shuts."""
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    tc.call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "t"})
    _, r = tc.call("/api/activities/release", {"course": "comp535", "lab": "lab1"})
    assert not r["ok"]


def test_nothing_vends_after_the_deadline(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc, vend_until=time.time() - 1)
    _, r = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    assert not r["ok"] and r["reason"] == "vending_closed"


def test_every_visit_vends_a_different_code(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    codes = {tc.call("/api/activity?course=comp535&lab=lab1", token="")[1]["code"]
             for _ in range(5)}
    assert len(codes) == 5


def test_an_unknown_code_is_refused_without_saying_more(tc):
    """A refusal must not become an oracle for guessing valid codes."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    status, r = tc.call("/api/activity?code=AAAA-AAAA", token="")
    assert status == 403 and set(r) == {"ok", "reason", "error"}


def test_a_vended_code_describes_the_activity_and_no_plan(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, armed = tc.call("/api/activity?code=" + v["code"], token="")
    assert armed["ok"] and armed["title"] == "lab1 title"
    assert armed["session_minutes"] == 60
    assert "plan" not in armed and "plan_hash" not in armed


# -- submission --------------------------------------------------------------- #
def _chain(code, *, t0, devices=None):
    """A chain shaped the way the real recorder shapes one.

    The submit entry goes through `artifact_summary`, the same function `proof_recorder` calls, so
    a fixture cannot drift into a shape gBuilder never emits — a hand-written artifact dict has no
    `elements` map, and the narration then reports "Nothing was handed in." over a chain that
    plainly built something.
    """
    from gini.domain import proof as P
    import activities as ACT
    devices = devices or [{"id": "1", "name": "R1", "type": "Router"}]
    topo = {"devices": devices, "links": []}
    chain = P.Chain.start(ACT.normalize(code), assignment="comp535/lab1", gini_version="test", t=t0)
    for d in devices:
        chain.append("place", {"id": d["id"], "type": d["type"], "name": d["name"]}, t=t0 + 1)
    chain.append("submit", {"artifact": P.artifact_summary(topo)}, t=t0 + 300)
    return P.build_proof(chain)


def _submit(tc, code, *, devices=None, t0=None):
    proof = _chain(code, t0=t0 if t0 is not None else time.time(), devices=devices)
    return tc.call("/api/activity/submit", {"code": code, "proof": proof}, token="")


def test_a_student_submits_with_no_account_and_gets_a_receipt(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    status, r = _submit(tc, v["code"])
    assert status == 200 and r["ok"] and r["receipt"]


def test_someone_elses_proof_file_is_refused(tc):
    """David hands Paul his proof.json; Paul submits it under his own code.

    Refused because the chain BINDS the ticket it was recorded under, so a proof cannot be
    detached from the code that produced it. Note the weaker thing this does not need to be:
    the same *work* redone under another code is a different chain with a different receipt —
    that case is caught by the artifact-hash twin, not here.
    """
    import activities as ACT
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, david = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, paul = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    stolen = _chain(david["code"], t0=time.time())

    status, r = tc.call("/api/activity/submit", {"code": paul["code"], "proof": stolen}, token="")
    assert status == 409 and r["reason"] == ACT.BAD_PROOF


def test_a_proof_cannot_be_submitted_twice_under_its_own_code(tc):
    """The plain double-submit: the code is spent on first use."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    t0 = time.time()
    assert _submit(tc, v["code"], t0=t0)[1]["ok"]
    status, r = _submit(tc, v["code"], t0=t0)
    assert status == 409 and not r["ok"]


def test_a_spent_code_cannot_be_used_again(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    assert _submit(tc, v["code"])[1]["ok"]
    assert not _submit(tc, v["code"],
                       devices=[{"id": "9", "name": "S9", "type": "Switch"}])[1].get("ok")


def test_the_teacher_reads_a_narration_back_from_the_receipt(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, sub = _submit(tc, v["code"])
    status, rep = tc.call("/api/receipt?receipt=" + sub["receipt"])
    assert status == 200
    assert rep["narration"].strip() and "R1" in rep["narration"]
    assert rep["title"] == "lab1 title"


def test_the_same_topology_under_two_codes_is_flagged_not_refused(tc):
    """A shared starter topology is a legitimate reason for this, so it is shown to the teacher
    rather than acted on."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, a = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, b = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    t0 = time.time()
    _, one = _submit(tc, a["code"], t0=t0)
    _, two = _submit(tc, b["code"], t0=t0 + 900)          # same artifact, different chain
    assert one["ok"] and two["ok"]
    _, rep = tc.call("/api/receipt?receipt=" + two["receipt"])
    assert [t["receipt"] for t in rep["twins"]] == [one["receipt"]]


def test_a_receipt_from_another_course_is_not_readable(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc, "comp535", "lab1")
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, sub = _submit(tc, v["code"])
    _, added = tc.call("/api/staff", {"username": "ada"})
    _, ada = tc.call("/auth/claim", {"id": "ada", "claim_token": added["claim_token"],
                                     "password": "a-good-password"})
    assert tc.call("/api/receipt?receipt=" + sub["receipt"], token=ada["session"])[0] == 403


# -- content ------------------------------------------------------------------ #
def test_a_material_is_uploaded_and_served_publicly(tc):
    """Students have no account, so a handout must be reachable without one."""
    import base64
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    _, r = tc.call("/api/materials", {"course": "comp535", "title": "Handout",
                                      "filename": "h.txt",
                                      "data": base64.b64encode(b"read me").decode()})
    assert r["ok"]
    assert tc.page("/m/" + r["id"])[1] == "read me"


def test_a_material_cannot_escape_its_course_directory(tc):
    """A client-supplied filename is never a path."""
    import base64
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    _, r = tc.call("/api/materials", {"course": "comp535", "title": "x",
                                      "filename": "../../escaped.txt",
                                      "data": base64.b64encode(b"nope").decode()})
    assert r["ok"]
    assert not (tc.server.ROOT.parent / "escaped.txt").exists()
    assert list((tc.server.MATERIALS / "comp535").glob("*escaped.txt"))


# -- privacy and the shape of v1 ---------------------------------------------- #
def test_no_response_on_the_student_path_carries_an_identity(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, sub = _submit(tc, v["code"])
    blob = json.dumps([v, sub]).lower()
    for word in ("student", "email", "sis_id", "username"):
        assert word not in blob


def test_v1_runs_with_the_aop_modules_unimportable(tc, monkeypatch):
    """An acceptance criterion: v1 must not depend on the shelved plan machinery."""
    import builtins
    real = builtins.__import__

    def deny(name, *a, **k):
        if "aop" in name:
            raise ImportError("shelved in v1")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    assert v["ok"]
    assert _submit(tc, v["code"])[1]["ok"]


def test_the_console_and_getcode_pages_are_served(tc):
    for path in ("/", "/getcode"):
        status, html = tc.page(path)
        assert status == 200 and "<script" in html


def test_signing_out_kills_the_session_on_the_server(tc):
    """Not just in the browser. A token forgotten locally but still valid is a live staff session
    left on a shared lab machine."""
    r = tc.signin("boss", "correct-horse")
    assert tc.call("/api/courses")[0] == 200
    tc.call("/auth/logout", {})
    assert tc.call("/api/courses", token=r["session"])[0] == 401


def test_the_submissions_list_shows_what_arrived_without_needing_a_receipt(tc):
    """Work submits itself; a teacher must be able to see it without a student telling them a
    code."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, sub = _submit(tc, v["code"])
    _, rows = tc.call("/api/submissions?course=comp535")
    assert [r["receipt"] for r in rows] == [sub["receipt"]]
    assert "code" not in rows[0]          # the list is for reading, not for redeeming


def test_a_course_can_be_archived_and_restored(tc):
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    tc.call("/api/courses/archive", {"course": "comp535"})
    assert next(c for c in tc.call("/api/courses")[1] if c["id"] == "comp535")["archived"] == 1
    tc.call("/api/courses/archive", {"course": "comp535"})
    assert next(c for c in tc.call("/api/courses")[1] if c["id"] == "comp535")["archived"] == 0


def test_unstaffing_takes_the_course_back(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc, "comp535", "lab1")
    _, added = tc.call("/api/staff", {"username": "ada"})
    _, ada = tc.call("/auth/claim", {"id": "ada", "claim_token": added["claim_token"],
                                     "password": "a-good-password"})
    tc.call("/api/courses/staff", {"course": "comp535", "username": "ada"})
    assert tc.call("/api/activities?course=comp535", token=ada["session"])[0] == 200
    tc.call("/api/courses/unstaff", {"course": "comp535", "username": "ada"})
    assert tc.call("/api/activities?course=comp535", token=ada["session"])[0] == 403


def test_promoting_a_teacher_to_admin_takes_effect_on_their_next_sign_in(tc):
    """The role is stamped on the session, so a live session keeps the role it was issued with.
    Worth stating: it is why a demotion is not instant, and why sign-out is the way to force it."""
    tc.signin("boss", "correct-horse")
    _, added = tc.call("/api/staff", {"username": "ada"})
    _, ada = tc.call("/auth/claim", {"id": "ada", "claim_token": added["claim_token"],
                                     "password": "a-good-password"})
    assert tc.call("/api/staff", token=ada["session"])[0] == 403
    tc.call("/api/staff/role", {"username": "ada", "role": "admin"})
    _, again = tc.call("/auth/login", {"id": "ada", "password": "a-good-password"})
    assert tc.call("/api/staff", token=again["session"])[0] == 200


# -- the server must always answer -------------------------------------------- #
def test_an_unhandled_error_comes_back_as_a_readable_reason(tc, monkeypatch):
    """Not a dropped connection. When a handler raises, `BaseHTTPRequestHandler` closes the socket,
    so the browser gets a network failure with no status and no body — and the console can only say
    "something went wrong", which helps neither the teacher nor whoever has to fix it."""
    import server
    monkeypatch.setattr(server.Handler, "_save_activity",
                        lambda self, c, b: (_ for _ in ()).throw(RuntimeError("the disk is gone")))
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    status, r = tc.call("/api/activities/save", {"course": "comp535", "lab": "lab1"})
    assert status == 500
    assert "the disk is gone" in r["error"]      # the actual reason, not a generic apology


def test_a_missing_endpoint_names_itself(tc):
    tc.signin("boss", "correct-horse")
    status, r = tc.call("/api/nonsense")
    assert status == 404 and "/api/nonsense" in r["error"]


def test_a_deadline_the_server_cannot_read_is_refused_not_a_crash(tc):
    """A browser without `datetime-local` sends the raw typed text. `float()` on that used to raise
    and take the connection down, so the teacher lost a lab and got no explanation."""
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    s, r = tc.call("/api/activities/save",
                   {"course": "comp535", "lab": "lab1", "title": "t",
                    "vend_until": "2026-08-29, 11:11 AM"})
    assert s == 200 and not r["ok"]
    assert "deadline" in r["error"]


def test_minutes_must_be_a_number_and_must_be_positive(tc):
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    base = {"course": "comp535", "lab": "lab1", "title": "t"}
    assert "number" in tc.call("/api/activities/save", {**base, "session_minutes": "sixty"})[1]["error"]
    assert "zero" in tc.call("/api/activities/save", {**base, "session_minutes": -5})[1]["error"]


def test_a_null_field_keeps_what_was_already_saved(tc):
    """The contract the console relies on: `null` means "leave it alone".

    Editing a lab to fix its title must not wipe the deadline that made it work — and the deadline
    is the entire late-submission control, so losing it silently turns a closed lab back into an
    open one.
    """
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    tc.call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "first",
                                     "vend_until": 2_000_000_000.0, "session_minutes": 90})
    tc.call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "second",
                                     "vend_until": None, "session_minutes": None})
    row = tc.call("/api/activities?course=comp535")[1][0]
    assert row["title"] == "second"
    assert row["vend_until"] == 2_000_000_000.0     # kept
    assert row["session_minutes"] == 90


def test_an_explicit_zero_clears_the_deadline(tc):
    """The other half of the contract, and the reason `null` had to be distinct: 0 is a real value
    meaning "no deadline", so a teacher CAN remove one deliberately."""
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    tc.call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "t",
                                     "vend_until": 2_000_000_000.0})
    tc.call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "t",
                                     "vend_until": 0})
    assert tc.call("/api/activities?course=comp535")[1][0]["vend_until"] == 0


# -- claiming: a STAFF action ------------------------------------------------- #
#
# The student hands their instructor a receipt out of band. The instructor types it into the
# Submissions panel with the student id, which both opens the work and records whose it is.
# There is no public claim page: the Teaching Center never talks to a student except to vend a code.


def _finished_work(tc):
    """A released lab, a code taken, work submitted. Returns the receipt."""
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, sub = _submit(tc, v["code"])
    return sub["receipt"]


def test_work_arrives_with_no_name_on_it(tc):
    """gBuilder pushes the work; nothing about the student comes with it."""
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    _, rep = tc.call("/api/receipt?receipt=" + receipt)
    assert rep["student_id"] == ""


def test_the_teacher_records_a_receipt_against_a_student(tc):
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    _, r = tc.call("/api/submissions/claim",
                   {"course": "comp535", "receipt": receipt, "student_id": "260123456"})
    assert r["ok"], r
    _, rep = tc.call("/api/receipt?receipt=" + receipt)
    assert rep["student_id"] == "260123456"


def test_claiming_needs_a_staff_session(tc):
    """It is the one write that attaches an identity. A public one would let anyone name anyone."""
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    assert tc.call("/api/submissions/claim",
                   {"course": "comp535", "receipt": receipt, "student_id": "x"}, token="")[0] == 401


def test_a_teacher_cannot_claim_into_a_course_they_do_not_run(tc):
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    _, added = tc.call("/api/staff", {"username": "ada"})
    _, ada = tc.call("/auth/claim", {"id": "ada", "claim_token": added["claim_token"],
                                     "password": "a-good-password"})
    assert tc.call("/api/submissions/claim",
                   {"course": "comp535", "receipt": receipt, "student_id": "x"},
                   token=ada["session"])[0] == 403


def test_a_receipt_from_another_course_is_refused(tc):
    """Two courses in one portal: a receipt must not be recordable from the wrong one."""
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    tc.call("/api/courses", {"id": "comp557", "title": "Other"})
    _, r = tc.call("/api/submissions/claim",
                   {"course": "comp557", "receipt": receipt, "student_id": "260123456"})
    assert not r["ok"] and "not from this course" in r["error"]


def test_a_second_student_on_one_receipt_is_refused_and_NAMES_the_first(tc):
    """The reason an id is recorded at all. The first claim stands — it is not overwritten — and
    the teacher is told who holds it, so a contested receipt can be taken up with both students
    rather than one of them silently losing their evening."""
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    tc.call("/api/submissions/claim",
            {"course": "comp535", "receipt": receipt, "student_id": "260111111"})
    _, r = tc.call("/api/submissions/claim",
                   {"course": "comp535", "receipt": receipt, "student_id": "260222222"})
    assert not r["ok"] and r["reason"] == "already_claimed"
    assert r["held_by"] == "260111111"
    assert "260111111" in r["error"]


def test_a_refused_claim_leaves_the_first_owner_in_place(tc):
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    tc.call("/api/submissions/claim",
            {"course": "comp535", "receipt": receipt, "student_id": "260111111"})
    tc.call("/api/submissions/claim",
            {"course": "comp535", "receipt": receipt, "student_id": "260222222"})
    _, rep = tc.call("/api/receipt?receipt=" + receipt)
    assert rep["student_id"] == "260111111"
    assert rep["contested_by"] == ["260222222"]


def test_recording_the_same_student_twice_is_harmless(tc):
    """A teacher retyping a row they already did must not look like a conflict."""
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    body = {"course": "comp535", "receipt": receipt, "student_id": "260123456"}
    assert tc.call("/api/submissions/claim", body)[1]["ok"]
    assert tc.call("/api/submissions/claim", body)[1]["ok"]


def test_an_unknown_receipt_says_so(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, r = tc.call("/api/submissions/claim",
                   {"course": "comp535", "receipt": "ZZZZ-ZZZZ", "student_id": "260123456"})
    assert not r["ok"] and r["reason"] == "no_such_receipt"


def test_a_claim_needs_both_fields(tc):
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    for body in ({"course": "comp535", "receipt": receipt},
                 {"course": "comp535", "student_id": "260123456"}):
        assert not tc.call("/api/submissions/claim", body)[1]["ok"]


def test_the_submissions_list_shows_who_it_belongs_to(tc):
    tc.signin("boss", "correct-horse")
    receipt = _finished_work(tc)
    tc.call("/api/submissions/claim",
            {"course": "comp535", "receipt": receipt, "student_id": "260123456"})
    row = tc.call("/api/submissions?course=comp535")[1][0]
    assert row["student_id"] == "260123456"
    assert "code" not in row              # a code in a listing is a code that can be replayed


def test_there_is_no_public_claim_endpoint(tc):
    """Removed deliberately: the Teaching Center never talks to a student except to vend a code."""
    assert tc.call("/api/claim", {"receipt": "X", "student_id": "y"}, token="")[0] in (401, 404)
    try:
        tc.page("/claim")
        assert False, "the public claim page is still being served"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_the_student_page_never_mentions_claiming(tc):
    """It is an anonymous code-vending page and nothing else."""
    _, html = tc.page("/getcode")
    assert "/claim" not in html


# -- deleting a lab ------------------------------------------------------------ #
def test_a_lab_is_deleted_when_the_id_is_typed_back(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, r = tc.call("/api/activities/delete",
                   {"course": "comp535", "lab": "lab1", "confirm": "lab1"})
    assert r["ok"], r
    assert tc.call("/api/activities?course=comp535")[1] == []


def test_deleting_without_typing_the_id_does_nothing(tc):
    """The gate against a misplaced click. A confirm dialog you can dismiss with Enter is not
    one."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    for confirm in ("", "yes", "LAB2", None):
        body = {"course": "comp535", "lab": "lab1"}
        if confirm is not None:
            body["confirm"] = confirm
        assert not tc.call("/api/activities/delete", body)[1]["ok"]
    assert len(tc.call("/api/activities?course=comp535")[1]) == 1


def test_a_lab_with_submissions_CANNOT_be_deleted(tc):
    """The gate that matters. Submitted work is the one thing here that cannot be recreated — a
    student cannot re-run a lab whose deadline has passed — so this is a refusal, not a scarier
    warning. Closing is how you retire it."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _submit(tc, v["code"])
    _, r = tc.call("/api/activities/delete",
                   {"course": "comp535", "lab": "lab1", "confirm": "lab1"})
    assert not r["ok"]
    assert "1 submission" in r["error"] and "Close it" in r["error"]
    assert len(tc.call("/api/activities?course=comp535")[1]) == 1


def test_the_refusal_counts_the_submissions_it_is_protecting(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    for dev in ("A", "B", "C"):
        _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
        _submit(tc, v["code"], devices=[{"id": dev, "name": dev, "type": "Router"}])
    _, r = tc.call("/api/activities/delete",
                   {"course": "comp535", "lab": "lab1", "confirm": "lab1"})
    assert "3 submissions" in r["error"]


def test_deleting_a_lab_takes_its_vended_codes_with_it(tc):
    """Otherwise a re-created lab of the same name inherits a pile of dead codes and reports a
    vended count that is a lie."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    for _ in range(3):
        tc.call("/api/activity?course=comp535&lab=lab1", token="")
    _, r = tc.call("/api/activities/delete",
                   {"course": "comp535", "lab": "lab1", "confirm": "lab1"})
    assert r["codes"] == 3
    _released_lab(tc)                                  # same id, fresh
    assert tc.call("/api/activities?course=comp535")[1][0]["vended"] == 0


def test_an_outstanding_code_stops_working_once_its_lab_is_gone(tc):
    """A student holding a code for a deleted lab must be told plainly, not handed a traceback."""
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    _, v = tc.call("/api/activity?course=comp535&lab=lab1", token="")
    tc.call("/api/activities/delete", {"course": "comp535", "lab": "lab1", "confirm": "lab1"})
    status, r = tc.call("/api/activity?code=" + v["code"], token="")
    assert status == 403 and not r["ok"]


def test_deleting_needs_a_staff_session_and_the_right_course(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc)
    body = {"course": "comp535", "lab": "lab1", "confirm": "lab1"}
    assert tc.call("/api/activities/delete", body, token="")[0] == 401
    _, added = tc.call("/api/staff", {"username": "ada"})
    _, ada = tc.call("/auth/claim", {"id": "ada", "claim_token": added["claim_token"],
                                     "password": "a-good-password"})
    assert tc.call("/api/activities/delete", body, token=ada["session"])[0] == 403
    assert len(tc.call("/api/activities?course=comp535")[1]) == 1


def test_deleting_a_lab_that_is_not_there(tc):
    tc.signin("boss", "correct-horse")
    tc.call("/api/courses", {"id": "comp535", "title": "Networks"})
    _, r = tc.call("/api/activities/delete",
                   {"course": "comp535", "lab": "nope", "confirm": "nope"})
    assert not r["ok"] and "No such activity" in r["error"]


def test_deleting_one_lab_leaves_the_others_alone(tc):
    tc.signin("boss", "correct-horse")
    _released_lab(tc, "comp535", "lab1")
    _released_lab(tc, "comp535", "lab2")
    tc.call("/api/activities/delete", {"course": "comp535", "lab": "lab1", "confirm": "lab1"})
    assert [a["lab"] for a in tc.call("/api/activities?course=comp535")[1]] == ["lab2"]
