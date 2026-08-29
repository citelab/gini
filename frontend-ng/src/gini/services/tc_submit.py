"""gBuilder's link to the Teaching Center: check a code, and hand in the work.

Two calls, no account, no session. A student never signs in to the Teaching Center — a vended code
is the whole interaction — so this needs nothing but a URL.

Deliberately NOT `agent/teaching_center.py`. That client is the v0 shape: enrolment tokens, lesson
packs, profiles, DM channels. Every one of those endpoints is gone, and dragging a class full of
dead methods into the one path a student depends on at deadline time would make it harder to see
whether *this* works.

**What gets sent, and why it cannot be stolen.** The proof is the tamper-evident chain, recorded
under the student's code. The topology is the runnable package. They are bound together already:
the chain's `submit` entry carries `sha256` of the topology it was generated from, and the chain
itself is bound to the code. So a student who obtains a classmate's project file gains nothing —
their own chain commits to a different digest, and the server checks it.

Timeouts are short and failure is always a sentence, never an exception: this runs at the end of a
lab, on campus wifi, and a student staring at a frozen dialog cannot tell whether their work is
safe.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 20.0          # generous for a big topology on bad wifi, short enough not to look hung


class Unreachable(Exception):
    """The server could not be reached at all. Distinct from a refusal, because the advice differs:
    a refusal is about the code, this is about the network."""


class Untrusted(Unreachable):
    """The server answered, but its TLS certificate was not trusted.

    A subclass so existing `except Unreachable` still catches it, but distinguishable because the
    advice is completely different — "is it still running?" is wrong and sends a student chasing a
    server that is up. In practice this means a self-signed certificate: real ones from the school
    CA verify without anyone doing anything.
    """


def _wrap(e: Exception) -> Unreachable:
    """Classify a transport failure. A certificate problem is NOT an outage."""
    cause = getattr(e, "reason", e)
    if isinstance(cause, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
        return Untrusted(
            "the course server's security certificate is not trusted by this machine. The server "
            "is running — this is a certificate problem, so nothing you do in gBuilder will fix "
            "it. Tell your instructor, and keep your proof file.")
    return Unreachable(str(e))


def _post(url: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url.rstrip("/") + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:                      # a refusal still carries a reason
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except json.JSONDecodeError:
            return e.code, {"error": f"The course server replied with {e.code}."}
    except Exception as e:                                   # noqa: BLE001
        raise _wrap(e) from e


def _get(url: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + path, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except json.JSONDecodeError:
            return e.code, {"error": f"The course server replied with {e.code}."}
    except Exception as e:                                   # noqa: BLE001
        raise _wrap(e) from e


def check_code(url: str, code: str) -> dict:
    """What this code is for — or why it cannot be used.

    Called at ARM time, so a dead code costs a student a moment instead of an evening. Without it
    gBuilder accepts any well-formed code, because the check symbol is self-verifying: a code the
    course never issued still arms, and the student only finds out when they try to hand in.

    Returns `{ok, title, brief, session_minutes, valid_until}` or `{ok: False, error}`.
    """
    if not url:
        return {"ok": False, "error": "No course server is configured."}
    _, r = _get(url, "/api/activity?code=" + urllib.parse.quote(code))
    return r if isinstance(r, dict) else {"ok": False, "error": "Unexpected reply."}


def submit(url: str, code: str, proof: dict, topology: dict | None = None) -> dict:
    """Hand in the work. Returns `{ok, receipt, within_session}` or `{ok: False, error}`.

    `topology` is what makes the submission runnable for the teacher. It is optional here only so
    the failure is graceful if a caller has none — the server refuses a topology that does not
    match the proof, so sending a wrong one is never quietly accepted.
    """
    if not url:
        return {"ok": False, "error": "No course server is configured."}
    body: dict = {"code": code, "proof": proof}
    if topology:
        body["topology"] = topology
    _, r = _post(url, "/api/activity/submit", body)
    return r if isinstance(r, dict) else {"ok": False, "error": "Unexpected reply."}
