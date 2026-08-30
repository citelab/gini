"""gBuilder's link to the Teaching Center as STAFF: find a submission, and open it.

The teacher half of the v1 protocol, and deliberately small. A marker needs three things — sign in,
read the report for a receipt, and get the student's topology onto the canvas — and this is those
three and nothing else.

Deliberately NOT `agent/teaching_center.py`, for the same reason `tc_submit` is not: that client
was built against the v0 server, twenty-four of its twenty-seven calls now hit endpoints that no
longer exist, and it is parked (see `app/features.py`). Marking is the path a teacher uses at the
end of term with a deadline behind them; dragging a class of dead methods into it would make it
harder to see whether *this* works.

**A session, never a stored password.** Sign-in exchanges the password for a token that the server
expires after twelve hours. The password is not written anywhere, and the token lives in memory for
the life of the window — a marker on a shared machine leaves nothing behind.

HTTPS is required, as everywhere else in GINI: this carries a staff password and then a session
token that is worth more.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .tc_submit import TIMEOUT, Insecure, Unreachable, _require_tls, _wrap


class Refused(Exception):
    """The server answered and said no — bad password, not your course, no such receipt.

    Distinct from `Unreachable` because the advice is the opposite: nothing about the network needs
    fixing, and retrying will produce the same answer.
    """


def _call(url: str, path: str, *, session: str = "", body: dict | None = None,
          raw: bool = False):
    _require_tls(url)
    req = urllib.request.Request(url.rstrip("/") + path, method="POST" if body is not None else "GET")
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if session:
        req.add_header("Authorization", f"Bearer {session}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            payload = r.read()
            return payload if raw else json.loads(payload or b"null")
    except urllib.error.HTTPError as e:
        # The server explains itself in the body; a bare status code tells a marker nothing.
        try:
            detail = json.loads(e.read() or b"{}").get("error", "")
        except Exception:                                        # noqa: BLE001
            detail = ""
        raise Refused(detail or f"The course server replied with {e.code}.") from e
    except Exception as e:                                       # noqa: BLE001
        raise _wrap(e) from e


def sign_in(url: str, who: str, password: str, claim_token: str = "") -> dict:
    """Exchange a password for a session. Returns `{session, role, who}`.

    `claim_token` is the first-time-only path: an account the admin created but nobody has claimed
    has no password yet, and the token is what proves you are the person it was made for.
    """
    path, body = "/auth/login", {"id": who, "password": password}
    if claim_token:
        path, body = "/auth/claim", {"id": who, "claim_token": claim_token, "password": password}
    answer = _call(url, path, body=body) or {}
    if not answer.get("ok"):
        raise Refused(answer.get("error") or "That sign-in was refused.")
    return {"session": answer.get("session", ""), "role": answer.get("role", ""),
            "who": answer.get("who", who)}


def sign_out(url: str, session: str) -> None:
    """Best effort: a session left behind expires on its own, and failing to log out must never be
    the thing that stops somebody closing the window."""
    try:
        _call(url, "/auth/logout", session=session, body={})
    except Exception:                                            # noqa: BLE001
        pass


def whoami(url: str, session: str) -> dict:
    """Who this session belongs to, or `{}` if it has expired. Never raises for an expired session —
    that is an ordinary end to a working day, not an error."""
    try:
        return _call(url, "/auth/whoami", session=session) or {}
    except Refused:
        return {}


def report(url: str, session: str, receipt: str) -> dict:
    """The marking view for one receipt: integrity, the account of what happened, whether it fitted
    the session window, duplicate flags, and whether a runnable copy came with it.

    No score. v1 describes; the teacher judges.
    """
    return _call(url, "/api/receipt?receipt=" + urllib.parse.quote(receipt.strip().upper()),
                 session=session) or {}


def topology(url: str, session: str, receipt: str) -> dict:
    """The student's work as a gBuilder project, ready to open and run.

    The server writes it in gBuilder's own project format, so there is no conversion step here — a
    submission you can read about but not run is half a report.
    """
    body = _call(url, "/api/submissions/topology?receipt=" + urllib.parse.quote(receipt.strip().upper()),
                 session=session, raw=True)
    try:
        return json.loads(body or b"null") or {}
    except json.JSONDecodeError as e:
        raise Refused("The course server sent something that is not a project file.") from e


__all__ = ["Refused", "Unreachable", "Insecure", "sign_in", "sign_out", "whoami",
           "report", "topology"]
