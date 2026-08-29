"""Authentication for the GINI server: a flat user file + signed session tokens.

The user file is JSON: ``{"<username>": {"salt": "<hex>", "hash": "<hex>"}}``. Passwords
are PBKDF2-HMAC-SHA256 — the server never stores (or sees, after login) a plaintext
password. Tokens are HMAC-signed with the server secret and carry an expiry, so the
server stays stateless: no session table, nothing to leak.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

_ITER = 200_000        # PBKDF2 iterations


def hash_password(password: str, salt: bytes | None = None) -> dict:
    """Make a {salt, hash} record for the user file (used by the add-user admin tool)."""
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return {"salt": salt.hex(), "hash": h.hex()}


def _check(password: str, rec: dict) -> bool:
    try:
        salt = bytes.fromhex(rec["salt"])
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
        return hmac.compare_digest(h.hex(), rec["hash"])
    except (KeyError, ValueError, TypeError):
        return False


class UserStore:
    """A flat username -> {salt, hash} table."""

    def __init__(self, users: dict | None = None) -> None:
        self._users = dict(users or {})

    @classmethod
    def from_file(cls, path: str | Path) -> "UserStore":
        p = Path(path)
        return cls(json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})

    def verify(self, username: str, password: str) -> bool:
        rec = self._users.get(username or "")
        return bool(rec) and _check(password or "", rec)


class Tokens:
    """Mint / verify HMAC-signed bearer tokens (`<payload>.<sig>`), so auth is stateless."""

    def __init__(self, secret: bytes, ttl: int = 8 * 3600) -> None:
        self._secret = secret
        self._ttl = ttl

    def mint(self, username: str) -> str:
        payload = {"u": username, "exp": int(time.time()) + self._ttl}
        body = base64.urlsafe_b64encode(json.dumps(payload).encode())
        sig = hmac.new(self._secret, body, hashlib.sha256).digest()
        return body.decode() + "." + base64.urlsafe_b64encode(sig).decode()

    def verify(self, token: str) -> str | None:
        """Return the username if the token is valid + unexpired, else None."""
        try:
            body_s, sig_s = (token or "").split(".", 1)
            body = body_s.encode()
            # compare the base64 signature strings directly (b64decode is lenient about
            # trailing junk, which would let a tampered token slip through).
            expect = base64.urlsafe_b64encode(
                hmac.new(self._secret, body, hashlib.sha256).digest()).decode()
            if not hmac.compare_digest(sig_s, expect):
                return None
            payload = json.loads(base64.urlsafe_b64decode(body))
            if int(payload.get("exp", 0)) < time.time():
                return None
            return payload.get("u")
        except (ValueError, KeyError, json.JSONDecodeError):
            return None
