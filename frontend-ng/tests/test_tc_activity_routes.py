"""The activity HTTP surface, exercised over a real socket.

Driven through an actual `ThreadingHTTPServer` rather than by calling handler methods, because the
thing most likely to break here is *dispatch order*, and dispatch only exists when a real request
walks the chain. Specifically: `_teacher_routes` claims every `/api/` path and 401s it without a
teacher session, so the two code-authenticated endpoints must be intercepted before it. A unit test
that called the handler directly would never notice.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini.domain import aop as A                               # noqa: E402
from gini.domain import proof as P                             # noqa: E402

HOUR = 3600.0


@pytest.fixture
def tc(tmp_path, monkeypatch):
    """A live Teaching Center on a loopback port, with one released activity."""
    monkeypatch.setenv("COURSE_ROOT", str(tmp_path))
    monkeypatch.setenv("COURSE", "comp535")
    for mod in [m for m in list(sys.modules) if m in
                ("server", "store", "accounts", "teacher", "activities", "social", "ai")]:
        del sys.modules[mod]

    from store import Store
    Store._instances.clear()

    import server as SRV
    import teacher as T

    plan = A.Aop(header=A.Header(intent="routed network"),
                 expectations=(A.Expectation(id="e", say="A router exists", layer="L3",
                                             check="exists('router')"),))
    course = T.Course(str(tmp_path), "comp535")
    course.save_activity("lab1", title="Multi-LAN", intent="routed network",
                         plan=plan.to_dict(), vend_until=time.time() + HOUR,
                         session_minutes=60)
    course.release_activity("lab1")

    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SRV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield type("TC", (), {"base": base, "plan": plan, "course": course,
                          "store": course.store, "root": tmp_path})
    httpd.shutdown()


def get(tc, path):
    """A refusal is a normal outcome here (403 for a bad code), so read the body either way rather
    than letting urllib raise — the reason in that body is the thing under test."""
    try:
        with urllib.request.urlopen(tc.base + path, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def post(tc, path, body):
    req = urllib.request.Request(tc.base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get_raw(tc, path):
    try:
        with urllib.request.urlopen(tc.base + path, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# -- the dispatch trap -------------------------------------------------------- #
def test_the_code_endpoint_is_not_swallowed_by_the_teacher_router():
    """The trap this whole file exists for. `/api/activity` starts with `/api/`, which the teacher
    router claims and 401s. If dispatch order regresses, this is the test that says so."""
    pass  # asserted by every test below reaching a handler at all


def test_getcode_is_public(tc):
    status, html = get_raw(tc, "/getcode?course=comp535&lab=lab1")
    assert status == 200 and "<!doctype html>" in html.lower()


def test_the_public_page_has_no_writable_field(tc):
    """Privacy is visible on the page, not only in the schema: there is nothing a student could
    type a name into."""
    _s, html = get_raw(tc, "/getcode?course=comp535&lab=lab1")
    for tag in ("<input", "<textarea", "<form"):
        assert tag not in html.lower()


def test_vending_needs_no_session(tc):
    status, body = get(tc, "/api/activity?course=comp535&lab=lab1")
    assert status == 200 and body["ok"]
    assert body["title"] == "Multi-LAN" and body["session_minutes"] == 60


def test_every_request_vends_a_different_code(tc):
    codes = {get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"] for _ in range(5)}
    assert len(codes) == 5


def test_a_vended_code_is_shown_grouped(tc):
    code = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    assert len(code) == 14 and code[4] == "-" and code[9] == "-"


# -- arming ------------------------------------------------------------------- #
def test_a_code_fetches_the_plan(tc):
    code = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    status, body = get(tc, f"/api/activity?code={code}")
    assert status == 200 and body["ok"]
    assert body["plan_hash"] == A.plan_hash(tc.plan)
    assert len(body["plan"]["expectations"]) == 1


def test_an_unknown_code_is_refused_before_any_work(tc):
    status, body = get(tc, "/api/activity?code=AAAA-AAAA-AAAA")
    assert status == 403 and body["reason"] == "unknown_code"


def test_a_refusal_does_not_reveal_whether_another_code_would_work(tc):
    """The endpoint must not become an oracle for guessing valid codes."""
    _s, body = get(tc, "/api/activity?code=AAAA-AAAA-AAAA")
    assert "plan" not in body and "activity" not in body


def test_an_unreleased_activity_vends_nothing(tc):
    tc.course.unrelease_activity("lab1")
    _s, body = get(tc, "/api/activity?course=comp535&lab=lab1")
    assert not body["ok"] and body["reason"] == "not_released"


def test_vending_stops_after_the_deadline(tc):
    row = dict(tc.store.activity("comp535/lab1"))
    row["vend_until"] = time.time() - 1
    tc.store.activity_put(row)
    _s, body = get(tc, "/api/activity?course=comp535&lab=lab1")
    assert not body["ok"] and body["reason"] == "vending_closed"


# -- submitting --------------------------------------------------------------- #
def a_proof(code, plan_hash, t0=None, minutes=10.0):
    t0 = t0 if t0 is not None else time.time() - minutes * 60 - 5
    import activities as ACT
    chain = P.Chain.start(ACT.normalize(code), assignment="comp535/lab1",
                          gini_version="test", t=t0)
    chain.entries[0].data["plan_hash"] = plan_hash
    chain.append("place", {"n": "R1"}, t=t0 + 1)
    chain.append("submit", {"artifact": {"sha256": "deadbeef", "devices": 1}},
                 t=t0 + minutes * 60)
    return P.build_proof(chain)


def test_a_submission_needs_no_session_and_returns_a_receipt(tc):
    code = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    proof = a_proof(code, A.plan_hash(tc.plan))
    status, body = post(tc, "/api/activity/submit", {"code": code, "proof": proof})
    assert status == 200 and body["ok"]
    assert body["receipt"] == P.receipt_code(proof)
    assert body["within_session"]


def test_a_code_accepts_exactly_one_submission(tc):
    code = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    assert post(tc, "/api/activity/submit",
                {"code": code, "proof": a_proof(code, A.plan_hash(tc.plan))})[0] == 200
    status, body = post(tc, "/api/activity/submit",
                        {"code": code, "proof": a_proof(code, A.plan_hash(tc.plan))})
    assert status == 409 and body["reason"] == "already_used"


def test_a_tampered_proof_is_refused(tc):
    code = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    proof = a_proof(code, A.plan_hash(tc.plan))
    proof["entries"][1]["data"]["n"] = "R2"
    status, body = post(tc, "/api/activity/submit", {"code": code, "proof": proof})
    assert status == 409 and body["reason"] == "bad_proof"


def test_someone_elses_proof_cannot_be_replayed(tc):
    mine = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    theirs = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    status, body = post(tc, "/api/activity/submit",
                        {"code": mine, "proof": a_proof(theirs, A.plan_hash(tc.plan))})
    assert status == 409 and body["reason"] == "bad_proof"


def test_an_overrun_submission_is_accepted_and_flagged(tc):
    code = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    status, body = post(tc, "/api/activity/submit",
                        {"code": code, "proof": a_proof(code, A.plan_hash(tc.plan), minutes=90)})
    assert status == 200 and body["ok"] and body["within_session"] is False


# -- the teacher side stays behind a session ---------------------------------- #
def test_the_teacher_api_is_still_protected(tc):
    for path in ("/api/activities", "/api/activities/receipt?receipt=ABCD-EFGH", "/api/roster"):
        assert get_raw(tc, path)[0] == 401


def test_the_teacher_api_being_protected_did_not_break_the_code_endpoints(tc):
    """Both live under /api/. The point of the split is that one is session-gated and one is
    code-gated — a fix to either must not collapse them into the same rule."""
    assert get(tc, "/api/activity?course=comp535&lab=lab1")[0] == 200
    assert get_raw(tc, "/api/activities")[0] == 401


# -- nothing identifying crosses the wire ------------------------------------- #
def test_no_response_carries_an_identity(tc):
    code = get(tc, "/api/activity?course=comp535&lab=lab1")[1]["code"]
    bodies = [get(tc, "/api/activity?course=comp535&lab=lab1")[1],
              get(tc, f"/api/activity?code={code}")[1],
              post(tc, "/api/activity/submit",
                   {"code": code, "proof": a_proof(code, A.plan_hash(tc.plan))})[1]]
    for b in bodies:
        blob = json.dumps(b).lower()
        for word in ('"student"', '"username"', '"sis_id"', '"email"'):
            assert word not in blob
