"""Identity for the Teaching Center — accounts, passwords, sessions.

The rule: **accounts are CREATED by the teacher and CLAIMED by the student.** Nobody self-registers,
because a course roster is not a sign-up sheet.

    1. The teacher enrols `mahesh`. `Course.enrol()` already mints a one-time enrolment token.
       That token is the thing the student is handed.
    2. First login = **student id + enrolment token + a new password** → the account is claimed and
       the token is spent.
    3. Thereafter = student id + password → a session token.

Why the enrolment token matters: without it, first-password-wins means whoever gets there first
claims the account. Student ids are guessable, so a classmate could claim someone else's identity —
and then *be* them, in a system that now has chat. The token closes that for free, because the
teacher already has to hand each student something.

Passwords: `hashlib.scrypt` (stdlib — no new dependency), per-user random salt. The password itself
is never stored and never logged. Sessions: a random bearer token with an expiry, held server-side;
the CLIENT stores the session token, never the password.

Storage (COURSE_ROOT/data/):
    accounts.json   {student_id: {role, salt, hash, n, r, p, claimed_at}}
    sessions.json   {token: {who, role, expires}}

Deliberately boring: JSON files, one course, classroom scale. Swap for a real datastore when this
outgrows a laptop — the API here is the seam.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from store import Store

SESSION_TTL = 12 * 3600          # a session lasts a working day, not forever
_N, _R, _P = 2 ** 14, 8, 1       # scrypt cost — interactive-login tier
_DKLEN = 32


def _hash(password: str, salt: bytes, *, n=_N, r=_R, p=_P) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=_DKLEN).hex()


def _username_hint(roster: list, typed: str) -> str:
    """If someone signs in with their display NAME or their SCHOOL ID instead of their username,
    point them at the username rather than a dead 'no such account'. This is the exact mistake a
    teacher makes the first time: usernames, names and school ids look interchangeable but only one
    is the login."""
    t = (typed or "").strip().lower()
    hit = next((r for r in roster
                if (r.get("name") or "").lower() == t or str(r.get("sis_id") or "").lower() == t),
               None)
    if hit:
        return f"Sign in with your username — yours is “{hit['id']}”, not “{typed}”."
    return ""


class Accounts:
    def __init__(self, root) -> None:
        self.store = Store(root)

    def accounts(self) -> dict:
        return self.store.accounts()

    def _roster(self) -> list:
        return self.store.roster()

    # -- claiming (first login) --------------------------------------------- #
    def claim(self, student_id: str, enrolment_token: str, password: str) -> dict:
        """Turn an enrolled-but-unclaimed roster row into a real account.

        Fails closed: unknown student, wrong token, already claimed, or a weak password all refuse.
        The token comparison is constant-time — a timing oracle on an enrolment token is a silly way
        to lose an account."""
        if len(password or "") < 8:
            return {"ok": False, "error": "Choose a password of at least 8 characters."}
        roster = self._roster()
        row = next((r for r in roster if r.get("id") == student_id), None)
        if row is None:
            hint = _username_hint(roster, student_id)
            return {"ok": False, "error": hint or
                    f"No account “{student_id}” in this course. Sign in with the username your "
                    f"instructor gave you."}
        if self.store.account(student_id) is not None:
            return {"ok": False, "error": "This account has already been set up. Sign in with your "
                                          "password, or ask your instructor to reset it."}
        if not hmac.compare_digest(str(row.get("token", "")), str(enrolment_token or "")):
            return {"ok": False, "error": "That enrolment token doesn't match."}

        salt = secrets.token_bytes(16)
        self.store.put_account(student_id, role="student", salt=salt.hex(),
                               hash=_hash(password, salt), n=_N, r=_R, p=_P,
                               claimed_at=int(time.time()))
        return {"ok": True, "session": self._new_session(student_id, "student")}

    # -- login / sessions ---------------------------------------------------- #
    def login(self, who: str, password: str) -> dict:
        rec = self.store.account(who)
        if rec is None:
            # Don't leak whether the id exists to an unauthenticated caller… except that here it's a
            # class roster the student is already on, and "you haven't set up your account yet" is
            # genuinely the help they need. Usability wins over a non-secret.
            roster = self._roster()
            if any(r.get("id") == who for r in roster):
                return {"ok": False, "error": "You haven't set up a password yet — tick “First "
                                              "time” and use your enrolment token."}
            hint = _username_hint(roster, who)
            return {"ok": False, "error": hint or "No such username in this course."}
        if not self._verify(rec, password):
            return {"ok": False, "error": "Wrong password."}
        return {"ok": True, "session": self._new_session(who, rec.get("role", "student")),
                "role": rec.get("role", "student")}

    @staticmethod
    def _verify(rec: dict, password: str) -> bool:
        salt = bytes.fromhex(rec["salt"])
        got = _hash(password or "", salt, n=rec.get("n", _N), r=rec.get("r", _R), p=rec.get("p", _P))
        return hmac.compare_digest(got, rec["hash"])

    def _new_session(self, who: str, role: str) -> str:
        tok = secrets.token_urlsafe(24)
        self.store.gc_sessions(time.time())
        self.store.put_session(tok, who, role, int(time.time()) + SESSION_TTL)
        return tok

    def whoami(self, session: str) -> dict | None:
        """Resolve a bearer token → {who, role}, or None. Expired tokens are treated as absent."""
        if not session:
            return None
        rec = self.store.session(session)
        if rec is None:
            return None
        if rec.get("expires", 0) < time.time():
            self.store.delete_session(session)
            return None
        return {"who": rec["who"], "role": rec.get("role", "student")}

    def logout(self, session: str) -> None:
        self.store.delete_session(session)

    # -- photos -------------------------------------------------------------- #
    def set_photo(self, username: str, photo: str) -> dict:
        """Store a student's photo (a small data-URL, capped). Only a claimed account may have one —
        a photo is tied to an identity, not to a roster row waiting to be claimed."""
        if self.store.account(username) is None:
            return {"ok": False, "error": "No such account."}
        if photo and len(photo) > 400_000:               # ~300 KB image; keep the DB lean
            return {"ok": False, "error": "That image is too large — please use a smaller one."}
        self.store.set_photo(username, photo or "")
        return {"ok": True}

    def photo(self, username: str) -> str:
        return self.store.photo(username)

    # -- teacher ------------------------------------------------------------- #
    def ensure_teacher(self) -> str | None:
        """Make sure a teacher account exists. On a fresh server, mint one from TEACHER_ID +
        TEACHER_PASSWORD; if no password is set, print a one-time claim token to the console so the
        console can't be opened by whoever reaches it first.

        This matters more than it looks: the teacher API serves `/api/roster`, which hands out the
        enrolment tokens for the WHOLE CLASS. An open teacher console is not a mild oversight — it
        is a master key."""
        tid = os.environ.get("TEACHER_ID", "teacher")
        pw = os.environ.get("TEACHER_PASSWORD", "")
        existing = self.store.account(tid)
        if pw:
            # TEACHER_PASSWORD is AUTHORITATIVE each boot. It used to only apply when creating the
            # account, so a teacher who set it on a later run (the account already existing from an
            # earlier one) got "wrong password" — the env var looked authoritative but was ignored.
            # Now: create the account, or reconcile its password to match, so setting the env var and
            # signing in with it always works.
            if existing is None or not self._verify(existing, pw):
                salt = secrets.token_bytes(16)
                self.store.put_account(tid, role="teacher", salt=salt.hex(), hash=_hash(pw, salt),
                                       n=_N, r=_R, p=_P, claimed_at=int(time.time()))
            return None
        if existing is not None:
            return None
        # no password configured → issue a claim token, exactly like a student's enrolment token
        setup = self.store.kv_get("teacher_setup") or {}
        tok = setup.get("token") or secrets.token_urlsafe(12)
        self.store.kv_put("teacher_setup", {"id": tid, "token": tok})
        return tok

    def claim_teacher(self, teacher_id: str, setup_token: str, password: str) -> dict:
        if len(password or "") < 8:
            return {"ok": False, "error": "Choose a password of at least 8 characters."}
        setup = self.store.kv_get("teacher_setup")
        if not setup or setup.get("id") != teacher_id:
            return {"ok": False, "error": "No teacher setup is pending."}
        if not hmac.compare_digest(str(setup.get("token", "")), str(setup_token or "")):
            return {"ok": False, "error": "That setup token doesn't match."}
        if self.store.account(teacher_id) is not None:
            return {"ok": False, "error": "The teacher account already exists."}
        salt = secrets.token_bytes(16)
        self.store.put_account(teacher_id, role="teacher", salt=salt.hex(),
                               hash=_hash(password, salt), n=_N, r=_R, p=_P,
                               claimed_at=int(time.time()))
        self.store.kv_delete("teacher_setup")            # the token is spent
        return {"ok": True, "role": "teacher",
                "session": self._new_session(teacher_id, "teacher")}

    def reset(self, student_id: str) -> dict:
        """Teacher-initiated reset: drop the account and re-issue an enrolment token so the student
        can claim it again. (The roster row keeps its token; we just un-claim.)"""
        if self.store.account(student_id) is None:
            return {"ok": False, "error": "No such account."}
        self.store.delete_account(student_id)
        self.store.delete_sessions_of(student_id)        # kill their live sessions too
        return {"ok": True}
