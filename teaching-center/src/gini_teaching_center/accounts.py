"""Identity for the Teaching Center — staff accounts, passwords, sessions.

**Staff only.** A student never signs in: a vended activity code is the entire interaction. That is
not a convenience, it is the privacy property — the portal cannot leak who did the work if it never
learns it.

The rule for staff is that accounts are CREATED by the admin and CLAIMED by the teacher:

    1. The admin adds `mahesh`, which mints a one-time claim token. That token is what gets handed
       over (in person, by email — it is not a secret worth much on its own).
    2. First sign-in = username + claim token + a new password. The account is claimed, the token
       spent.
    3. Thereafter = username + password → a session token.

A forgotten password is recovered the same way it was set: the admin RESETS the account, which
puts it back to step 1 with a fresh token. There is no self-service reset — this server has no
address to send mail from and no second factor, so an emailed link would be a way in for whoever
controls the mailbox rather than a recovery.

Why the claim token matters: without it, first-password-wins means whoever reaches the portal first
claims the account. Usernames are guessable, so a stranger could become a teacher — and a teacher
can vend codes and read submissions.

Passwords: `hashlib.scrypt` (stdlib, no dependency), per-user random salt. The password itself is
never stored and never logged. Sessions: a random bearer token with an expiry, held server-side; the
client stores the session token, never the password.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from .store import Store

SESSION_TTL = 12 * 3600          # a working day, not forever
_N, _R, _P = 2 ** 14, 8, 1       # scrypt cost — interactive-login tier
_DKLEN = 32

ADMIN, TEACHER = "admin", "teacher"


def _hash(password: str, salt: bytes, *, n=_N, r=_R, p=_P) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=_DKLEN).hex()


class Accounts:
    def __init__(self, root) -> None:
        self.store = Store(root)

    # -- sign-in ---------------------------------------------------------- #
    def login(self, who: str, password: str) -> dict:
        rec = self.store.account(who)
        if rec is None:
            return {"ok": False, "error": "No such account."}
        if not rec.get("claimed_at"):
            return {"ok": False, "error": "That account has not been claimed yet — sign in with "
                                          "the claim token your admin gave you."}
        if not self._verify(rec, password):
            return {"ok": False, "error": "Wrong password."}
        return {"ok": True, "session": self._new_session(who, rec["role"]), "role": rec["role"],
                "who": who}

    def claim(self, who: str, claim_token: str, password: str) -> dict:
        """First sign-in for a staff account the admin created."""
        if len(password or "") < 8:
            return {"ok": False, "error": "Choose a password of at least 8 characters."}
        rec = self.store.account(who)
        if rec is None:
            return {"ok": False, "error": "No such account."}
        if rec.get("claimed_at"):
            return {"ok": False, "error": "That account has already been claimed."}
        pending = self.store.kv_get(f"claim:{who}") or {}
        if not pending or not hmac.compare_digest(str(pending.get("token", "")),
                                                  str(claim_token or "")):
            return {"ok": False, "error": "That claim token doesn't match."}
        salt = secrets.token_bytes(16)
        self.store.put_account(who, role=rec["role"], salt=salt.hex(), hash=_hash(password, salt),
                               n=_N, r=_R, p=_P, claimed_at=int(time.time()))
        self.store.kv_delete(f"claim:{who}")      # one-time: spent on use
        return {"ok": True, "session": self._new_session(who, rec["role"]), "role": rec["role"],
                "who": who}

    @staticmethod
    def _verify(rec: dict, password: str) -> bool:
        if not rec.get("salt") or not rec.get("hash"):
            return False
        got = _hash(password, bytes.fromhex(rec["salt"]),
                    n=rec.get("n") or _N, r=rec.get("r") or _R, p=rec.get("p") or _P)
        return hmac.compare_digest(got, rec["hash"])

    def _new_session(self, who: str, role: str) -> str:
        token = secrets.token_urlsafe(24)
        self.store.put_session(token, who, role, int(time.time()) + SESSION_TTL)
        return token

    def whoami(self, session: str) -> dict | None:
        rec = self.store.session(session or "")
        if rec is None:
            return None
        if int(rec.get("expires") or 0) < int(time.time()):
            self.store.drop_session(session)
            return None
        return {"who": rec["who"], "role": rec["role"]}

    def logout(self, session: str) -> None:
        self.store.drop_session(session or "")

    # -- staff management (admin only; the server enforces that) ---------- #
    def staff(self) -> list[dict]:
        out = []
        for a in self.store.accounts():
            pending = self.store.kv_get(f"claim:{a['username']}")
            out.append({**a, "claimed": bool(a.get("claimed_at")),
                        "claim_token": (pending or {}).get("token", "")})
        return out

    def add_staff(self, username: str, role: str = TEACHER) -> dict:
        """Create an unclaimed account and mint its one-time claim token."""
        username = (username or "").strip().lower()
        if not username.isidentifier():
            return {"ok": False, "error": "Use letters, digits and underscores for a username."}
        if self.store.account(username) is not None:
            return {"ok": False, "error": "That account already exists."}
        role = ADMIN if role == ADMIN else TEACHER
        self.store.put_account(username, role=role, salt=None, hash=None, n=None, r=None, p=None,
                               claimed_at=None)
        token = secrets.token_urlsafe(9)
        self.store.kv_put(f"claim:{username}", {"token": token})
        return {"ok": True, "username": username, "role": role, "claim_token": token}

    def remove_staff(self, username: str) -> dict:
        rec = self.store.account(username)
        if rec is None:
            return {"ok": False, "error": "No such account."}
        # The portal must never be left with no way in. Refusing here is cheaper than a recovery
        # procedure, and an admin who wants out can promote someone else first.
        if rec["role"] == ADMIN and len([a for a in self.store.accounts()
                                         if a["role"] == ADMIN]) <= 1:
            return {"ok": False, "error": "That is the only admin — promote someone else first."}
        self.store.delete_account(username)
        self.store.kv_delete(f"claim:{username}")
        return {"ok": True}

    def reset_staff(self, username: str, *, by: str = "") -> dict:
        """Un-claim an account so its holder can set a new password.

        This is the whole of password recovery, and it is deliberately the whole of it. There is no
        "forgot my password" link, because a Teaching Center has no address it can send mail from
        and no second factor to fall back on — a self-service reset over email would be a way in
        for whoever controls the mailbox, not a recovery. The admin knows their teachers; handing
        over a fresh claim token in person or over a channel they already trust is a stronger check
        than any this server could make on its own.

        What it does: clears the password, mints a NEW claim token (so a lost one stops working),
        and ends every live session for the account. The role and the courses they staff are kept —
        this is a forgotten password, not a departure.

        Not yourself. If you have forgotten your own password you cannot be signed in to press it,
        so self-reset is never the recovery path; all it can do is sign you out and hand you a
        token you must not lose. For the last admin that is a locked-out portal, and the honest
        route back is another admin, or ADMIN_PASSWORD on the next boot (see `ensure_admin`).
        """
        rec = self.store.account(username)
        if rec is None:
            return {"ok": False, "error": "No such account."}
        if by and by == username:
            return {"ok": False,
                    "error": "You cannot reset your own account — it would sign you out and leave "
                             "you holding a token you must not lose. Ask another admin, or set "
                             "ADMIN_PASSWORD and restart the server."}
        self.store.put_account(username, role=rec["role"], salt=None, hash=None,
                               n=None, r=None, p=None, claimed_at=None)
        token = secrets.token_urlsafe(9)
        self.store.kv_put(f"claim:{username}", {"token": token})
        ended = self.store.drop_sessions_for(username)
        return {"ok": True, "username": username, "role": rec["role"], "claim_token": token,
                "sessions_ended": ended}

    def set_role(self, username: str, role: str) -> dict:
        rec = self.store.account(username)
        if rec is None:
            return {"ok": False, "error": "No such account."}
        if (rec["role"] == ADMIN and role != ADMIN
                and len([a for a in self.store.accounts() if a["role"] == ADMIN]) <= 1):
            return {"ok": False, "error": "That is the only admin."}
        self.store.put_account(username, role=(ADMIN if role == ADMIN else TEACHER),
                               salt=rec.get("salt"), hash=rec.get("hash"), n=rec.get("n"),
                               r=rec.get("r"), p=rec.get("p"), claimed_at=rec.get("claimed_at"))
        return {"ok": True, "username": username, "role": role}

    # -- bootstrap -------------------------------------------------------- #
    def ensure_admin(self) -> str | None:
        """Make sure the portal has an admin. Returns a one-time claim token to print, or None.

        `ADMIN_PASSWORD` is AUTHORITATIVE on every boot: setting it creates the account or
        reconciles its password. Without it, an unclaimed admin is created and a claim token
        printed, so the portal cannot be taken over by whoever reaches the port first — the console
        vends codes and reads every submission, which is not a thing to leave open.
        """
        who = os.environ.get("ADMIN_ID", "admin")
        pw = os.environ.get("ADMIN_PASSWORD", "")
        existing = self.store.account(who)
        if pw:
            if existing is None or not self._verify(existing, pw):
                salt = secrets.token_bytes(16)
                self.store.put_account(who, role=ADMIN, salt=salt.hex(), hash=_hash(pw, salt),
                                       n=_N, r=_R, p=_P, claimed_at=int(time.time()))
            elif existing["role"] != ADMIN:
                self.set_role(who, ADMIN)
            self.store.kv_delete(f"claim:{who}")
            return None
        if existing is not None:
            # An account that exists but was never claimed still has a token waiting for it.
            # Printing it only on the boot that CREATED the account made it a one-time sighting as
            # well as a one-time secret: miss that line in a scrollback — on a run you were doing
            # for some other reason — and the legitimate admin is locked out, with no route back
            # except an environment variable they would have to already know about, or a SQLite
            # query. Reprinting while it is still unclaimed gives up nothing: the moment it IS
            # claimed the token is deleted, and this returns None again.
            if not existing["hash"]:
                pending = self.store.kv_get(f"claim:{who}") or {}
                return pending.get("token") or None
            return None
        self.store.put_account(who, role=ADMIN, salt=None, hash=None, n=None, r=None, p=None,
                               claimed_at=None)
        token = secrets.token_urlsafe(12)
        self.store.kv_put(f"claim:{who}", {"token": token})
        return token
