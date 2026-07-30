"""Phase A — identity. These tests are written as ATTACKS, because that's what they defend against.

Before this, a Bearer token was "any non-empty string". That meant: read any student's profile,
submit results as anyone, and — the real one — GET /api/roster, which serves the enrolment tokens for
the entire class. An open teacher console isn't an oversight, it's a master key on an open port.
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
sys.path.insert(0, str(_TC))

import accounts as A                                     # noqa: E402
from store import Store                                   # noqa: E402

from gini.agent import teaching_center as TC             # noqa: E402


def _seed(root, rows):
    """Seed the roster through the store (the real enrolment path), not a raw JSON file."""
    s = Store(root)
    for r in rows:
        s.upsert_enrolment(r["id"], name=r.get("name", r["id"]), sis_id=r.get("sis_id", ""),
                           token=r.get("token", ""), group=r.get("group", ""),
                           ai_hosted=r.get("ai_hosted", False))
    return s


@pytest.fixture()
def course(tmp_path):
    _seed(tmp_path, [{"id": "mahesh", "name": "Mahesh", "token": "TOK-mahesh"},
                     {"id": "ana", "name": "Ana", "token": "TOK-ana"}])
    return A.Accounts(tmp_path)


# -- claiming ---------------------------------------------------------------- #
def test_a_classmate_cannot_claim_your_account(course):
    """THE hole the enrolment token exists to close. Student ids are guessable, so
    first-password-wins would let anyone claim anyone — and then BE them, in a system with chat."""
    bad = course.claim("mahesh", "TOK-ana", "hunter2hunter2")     # Ana's token, Mahesh's id
    assert not bad["ok"] and "token" in bad["error"].lower()
    assert "mahesh" not in course.accounts()                      # nothing was created

    guess = course.claim("mahesh", "", "hunter2hunter2")
    assert not guess["ok"]
    assert "mahesh" not in course.accounts()


def test_only_an_enrolled_student_can_claim(course):
    r = course.claim("stranger", "whatever", "hunter2hunter2")
    assert not r["ok"] and "no account" in r["error"].lower()


def test_the_token_is_spent_after_the_claim(course):
    assert course.claim("mahesh", "TOK-mahesh", "correct horse")["ok"]
    again = course.claim("mahesh", "TOK-mahesh", "a different password")
    assert not again["ok"] and "already been set up" in again["error"]


def test_a_claim_returns_a_session_and_never_stores_the_password(course):
    r = course.claim("mahesh", "TOK-mahesh", "correct horse")
    assert r["ok"] and r["session"]
    rec = course.accounts()["mahesh"]
    blob = json.dumps(rec)
    assert "correct horse" not in blob                            # not the password…
    assert rec["hash"] != "correct horse" and len(rec["salt"]) == 32
    assert course.whoami(r["session"]) == {"who": "mahesh", "role": "student"}


def test_weak_passwords_are_refused(course):
    assert not course.claim("mahesh", "TOK-mahesh", "short")["ok"]


# -- login ------------------------------------------------------------------- #
def test_wrong_password_is_refused_and_right_one_is_not(course):
    course.claim("mahesh", "TOK-mahesh", "correct horse")
    assert not course.login("mahesh", "correcthorse")["ok"]
    assert not course.login("mahesh", "")["ok"]
    ok = course.login("mahesh", "correct horse")
    assert ok["ok"] and course.whoami(ok["session"])["who"] == "mahesh"


def test_an_unclaimed_student_is_told_to_claim_rather_than_just_refused(course):
    r = course.login("ana", "anything")
    assert not r["ok"] and "enrolment token" in r["error"]        # actionable, not a dead end


def test_sessions_expire_and_expired_tokens_stop_working(course, monkeypatch):
    s = course.claim("mahesh", "TOK-mahesh", "correct horse")["session"]
    assert course.whoami(s)

    real_time = A.time.time                                       # capture BEFORE patching
    monkeypatch.setattr(A.time, "time", lambda: real_time() + A.SESSION_TTL + 10)
    assert course.whoami(s) is None                               # a stale session is no session


def test_logout_kills_the_session(course):
    s = course.login  # (silence linters)
    s = course.claim("mahesh", "TOK-mahesh", "correct horse")["session"]
    course.logout(s)
    assert course.whoami(s) is None


def test_teacher_reset_revokes_live_sessions(course):
    """Resetting an account must not leave the old session usable — otherwise 'reset' is theatre."""
    s = course.claim("mahesh", "TOK-mahesh", "correct horse")["session"]
    assert course.whoami(s)
    course.reset("mahesh")
    assert course.whoami(s) is None
    assert course.claim("mahesh", "TOK-mahesh", "a new password")["ok"]   # can claim again


# -- the teacher console is a master key ------------------------------------- #
def test_the_teacher_account_cannot_be_claimed_by_whoever_arrives_first(course, monkeypatch):
    monkeypatch.delenv("TEACHER_PASSWORD", raising=False)
    setup = course.ensure_teacher()
    assert setup                                    # a one-time setup token is printed at boot
    assert not course.claim_teacher("teacher", "guess", "hunter2hunter2")["ok"]
    ok = course.claim_teacher("teacher", setup, "hunter2hunter2")
    assert ok["ok"] and course.whoami(ok["session"]) == {"who": "teacher", "role": "teacher"}
    # spent
    assert not course.claim_teacher("teacher", setup, "another one")["ok"]


# -- the client refuses to leak your password -------------------------------- #
def test_client_refuses_to_send_a_password_over_plaintext_http():
    """Campus wifi is the same wifi whether or not the class is small."""
    with pytest.raises(TC.InsecureTransport):
        TC.refuse_plaintext_password("http://gini.myuni.edu:8080")

    TC.refuse_plaintext_password("https://gini.myuni.edu:8080")        # fine
    TC.refuse_plaintext_password("http://localhost:8080")              # never leaves the machine
    TC.refuse_plaintext_password("http://127.0.0.1:8080")
    TC.refuse_plaintext_password("http://gini.myuni.edu:8080", allow_insecure=True)   # conscious


def test_the_client_stores_a_session_not_a_password(tmp_path):
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if path == "/auth/login":
            return 200, {"ok": True, "session": "SESSION-XYZ", "role": "student"}
        return 200, []

    c = TC.TeachingCenterClient("http://localhost:8080", course="c1", student_id="mahesh",
                                cache_dir=tmp_path, transport=fake)
    assert not c.signed_in()
    assert c.login("correct horse")["ok"]
    assert c.signed_in() and c.session == "SESSION-XYZ"

    on_disk = " ".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert "SESSION-XYZ" in on_disk
    assert "correct horse" not in on_disk               # the password touched nothing durable

    # a fresh client picks the session back up — you don't retype your password every launch
    c2 = TC.TeachingCenterClient("http://localhost:8080", course="c1", student_id="mahesh",
                                 cache_dir=tmp_path, transport=fake)
    assert c2.signed_in() and c2.session == "SESSION-XYZ"


def test_an_expired_session_reads_as_sign_in_again_not_as_offline(tmp_path):
    """Sending a student to debug the network when they just need to re-enter a password is a small
    betrayal — so we distinguish 'the server rejected us' from 'the server isn't there'."""
    def rejects(method, path, body=None):
        return 401, None

    c = TC.TeachingCenterClient("http://localhost:8080", course="c1", student_id="mahesh",
                                session="STALE", cache_dir=tmp_path, transport=rejects)
    assert c.online() is False
    assert c.session_expired() is True

    def unreachable(method, path, body=None):
        return 0, None

    c2 = TC.TeachingCenterClient("http://localhost:8080", course="c1", student_id="mahesh",
                                 session="FINE", cache_dir=tmp_path, transport=unreachable)
    assert c2.online() is False
    assert c2.session_expired() is False                 # genuinely offline — do NOT ask for a password


# -- username vs school-id vs name (a real user hit this) -------------------- #
def test_username_is_the_login_and_school_id_is_just_bookkeeping(tmp_path):
    """The teacher's mental model: 'ravi' is the handle, '2511' is the registrar's number. So the
    USERNAME (id) is the login; the school id never is. Signing in with the school id — or the full
    name — must point you at your username, not dead-end."""
    _seed(tmp_path, [{"id": "ravi", "name": "Ravi Kumar", "sis_id": "2511", "token": "TOK-r"},
                     {"id": "surya", "name": "Surya P", "sis_id": "2512", "token": "TOK-s"}])
    c = A.Accounts(tmp_path)

    # sign in with the SCHOOL ID → told the username
    r = c.claim("2511", "TOK-r", "password123")
    assert not r["ok"] and "username" in r["error"] and "ravi" in r["error"]

    # sign in with the FULL NAME → told the username
    r = c.login("Ravi Kumar", "whatever")
    assert not r["ok"] and "ravi" in r["error"]

    # the username works, and the school id is preserved for records
    assert c.claim("ravi", "TOK-r", "password123")["ok"]
    row = next(x for x in c._roster() if x["id"] == "ravi")
    assert row["sis_id"] == "2511"


def test_a_genuinely_unknown_username_says_so_plainly(course):
    r = course.claim("nobody", "x", "password123")
    assert not r["ok"] and "no account" in r["error"].lower() and "username" in r["error"].lower()


def test_TEACHER_PASSWORD_is_authoritative_each_boot(tmp_path, monkeypatch):
    """The real trap: TEACHER_PASSWORD only applied at account creation, so a teacher who set it on a
    LATER boot (the account already existing) got 'wrong password'. It must be authoritative each
    boot — set it, restart, sign in with it."""
    monkeypatch.setenv("TEACHER_ID", "prof")
    monkeypatch.setenv("TEACHER_PASSWORD", "firstpass1")
    a = A.Accounts(tmp_path); a.ensure_teacher()
    assert a.login("prof", "firstpass1")["ok"]

    # reboot with a NEW env password → it becomes authoritative
    monkeypatch.setenv("TEACHER_PASSWORD", "mahesh2511")
    a2 = A.Accounts(tmp_path); a2.ensure_teacher()
    assert a2.login("prof", "mahesh2511")["ok"]
    assert not a2.login("prof", "firstpass1")["ok"]

    # and a stable password across boots doesn't churn the hash needlessly (still logs in)
    a3 = A.Accounts(tmp_path); a3.ensure_teacher()
    assert a3.login("prof", "mahesh2511")["ok"]
