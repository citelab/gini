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

**The body is gzipped, and a dropped connection is tried again at once.** Both came from the same
field report: "Broken pipe" on hand-in for 15–20 students in a class of 200. A proof is a chain
of every meaningful action, and a long OS lab's chain runs to megabytes — over a VPN that is a
long upload, long enough for a flaky link to drop it and for a proxy's body limit to refuse it.
JSON of repeated events compresses about ten to one, so the same work now crosses the wire as a
few hundred kilobytes. A Teaching Center that predates gzip answers a compressed body with a 500
(its JSON parser cannot read the bytes), and that exact answer is the cue to send it plain, so a
new gBuilder still hands in to an old server. And a connection that was reset or closed while
the body was in flight is retried straight away, twice, with a short wait — the outbox would
retry at the next launch, but a student who has just finished is still sitting there now.
"""
from __future__ import annotations

import gzip
import http.client
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 20.0          # generous for a big topology on bad wifi, short enough not to look hung

#: Seconds to wait before each immediate retry of a dropped upload. Two retries: enough to ride
#: out a VPN re-key or a proxy reload, short enough that a dead link is reported in seconds.
RETRY_WAITS = (1.0, 3.0)


class Insecure(Exception):
    """The course server was named over plain HTTP. Refused rather than sent.

    Not a subclass of `Unreachable`: retrying cannot fix it and the server may be perfectly up, so
    the advice is neither "check the network" nor "wait". It is a wrong address, and the person who
    can fix it is the instructor who published it.
    """


def _require_tls(url: str) -> None:
    """Refuse to speak anything but HTTPS.

    This carried the assignment code in a query string and the whole proof and topology in a body,
    over whatever scheme happened to be configured — and `agent/teaching_center.py` had guarded
    passwords this way since the beginning while this path, which replaced it for everything a
    student does, guarded nothing.

    No exemption for localhost. Loopback can hold a certificate like any other name, so "it never
    leaves the machine" is not a reason to drop the guarantee — and an exemption is exactly the
    thing that quietly becomes the deployment.
    """
    if not str(url).lower().startswith("https://"):
        raise Insecure(
            f"the course server address is not HTTPS ({url}). GINI will not send your assignment "
            f"code or your work over an unencrypted connection, where anyone on the same network "
            f"could read them. Ask your instructor for the https:// address.")


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


#: The operating system's verifier (gini.app.trust) reports its own codes, and on Windows its
#: message is in the user's language — so the codes are what is matched there, not the words.
#: OpenSSL's codes are small positive integers, so none of these can be mistaken for one.
_OS_HOSTNAME = {-67602,                                  # macOS errSecHostNameMismatch
                0x800B010F}                              # Windows CERT_E_CN_NO_MATCH
_OS_EXPIRED = {-67818,                                   # macOS errSecCertificateExpired
               0x800B0101}                               # Windows CERT_E_EXPIRED
_OS_UNTRUSTED = {-67843,                                 # macOS errSecNotTrusted
                 0x800B0109,                             # Windows CERT_E_UNTRUSTEDROOT
                 0x800B010A}                             # Windows CERT_E_CHAINING


def _os_code(cause: object) -> int | None:
    """The verifier's code, with a Windows HRESULT folded to its unsigned form either way."""
    code = getattr(cause, "verify_code", None)
    if not isinstance(code, int):
        return None
    return code & 0xFFFFFFFF if code < -0x80000 else code


def _wrap(e: Exception, url: str = "") -> Unreachable:
    """Classify a transport failure. A certificate problem is NOT an outage.

    The message has to name WHICH certificate problem. The first version said only "not trusted by
    this machine", and in the field that cost two rounds of investigation: an instructor reading it
    reasonably concluded the students' trust stores were at fault, when the actual failure was a
    hostname mismatch — a URL with the wrong port, pointing at a certificate issued for a different
    name. Those two have nothing in common except the exception class. Python hands us the specific
    reason; throwing it away turns a five-second diagnosis into a hunt.

    **"Self-signed certificate IN CERTIFICATE CHAIN" is not "the server's certificate is
    self-signed".** This said it was, to a student whose Python had no certificate list at all:
    the course server sends its chain right up to its (self-signed) root, so a client that trusts
    nothing reaches that root and reports it. The server's certificate was fine. That case, and
    OpenSSL's two "unable to get … issuer" refusals, all mean the same thing — the list this
    machine checked against does not hold the authority at the top of the chain — and say so.

    And the message names the list (`trust.describe()`): an operating system that distrusts a
    certificate and an interpreter with an empty file can produce the same words.
    """
    cause = getattr(e, "reason", e)
    verify = getattr(cause, "verify_message", "") or ""
    if isinstance(cause, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
        from ..app import trust
        where = f" ({url})" if url else ""
        low = (verify or str(e)).lower().replace("self signed", "self-signed")
        code = _os_code(cause)
        if "hostname mismatch" in low or "does not match" in low or code in _OS_HOSTNAME:
            detail = (f"the address you are connecting to{where} does not match the name on the "
                      f"server's certificate — check the address in Settings, especially the port, "
                      f"since a different port can serve a different certificate")
        elif "expired" in low or code in _OS_EXPIRED:
            detail = ("the server's certificate has expired — check your computer's clock first, "
                      "since a wrong date makes a good certificate look expired")
        elif ("self-signed certificate in certificate chain" in low
              or "unable to get local issuer" in low or "unable to get issuer" in low):
            detail = ("this computer's certificate list does not include the authority at the top "
                      "of the server's certificate chain — on a Mac, usually a Python from "
                      "python.org whose certificates were never installed; otherwise an "
                      "out-of-date list, or security software inspecting secure connections")
        elif "self-signed" in low:
            detail = ("the server's certificate is self-signed, or something on your network is "
                      "intercepting secure connections")
        elif code in _OS_UNTRUSTED or "not trusted" in low:
            detail = ("this computer does not trust the authority that issued the server's "
                      "certificate — the server may be using a self-signed certificate, or "
                      "something on your network is intercepting secure connections")
        else:
            detail = f"the server's certificate was rejected by this machine ({verify or cause})"
        return Untrusted(
            f"{detail}. Checked against {trust.describe()}. The server is running — this is a "
            f"certificate problem, so nothing you do in gBuilder will fix it. Tell your instructor "
            f"(teaching-center/tools/tc_check.py in the GINI repo prints the details they will "
            f"ask for), and keep your proof file.")
    if _dropped(cause):
        # Name what happened in words a student can act on. "[Errno 32] Broken pipe" is the
        # whole of what twenty of them were shown, and none of them could tell whether the server
        # was down, their network was, or their work was gone.
        return Unreachable(
            f"the connection was closed while your work was being sent ({cause}). The server "
            f"was reached, so this is usually the network between you and it — a VPN that "
            f"dropped, or a proxy in front of the course server refusing a large upload.")
    return Unreachable(str(e))


def _dropped(e: object) -> bool:
    """Did the far end close on us mid-request? Those are the failures a second try fixes —
    unlike a timeout (nothing is listening, or the link is dead) or a refused connection, where
    trying again in a second just doubles the wait for the same answer.

    Takes the raw exception or urllib's `URLError` around it: the request body is sent inside
    urllib's wrapper, the response is read outside it, so the same dropped connection arrives
    either way depending on how far it got.
    """
    cause = getattr(e, "reason", e)
    return isinstance(cause, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                              http.client.RemoteDisconnected, http.client.IncompleteRead))


def _post(url: str, path: str, body: dict, *, compress: bool = False) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if compress:
        data = gzip.compress(data, compresslevel=6)
        headers["Content-Encoding"] = "gzip"
    req = urllib.request.Request(url.rstrip("/") + path, data=data, headers=headers,
                                 method="POST")
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
        raise _wrap(e, url) from e


def _post_retrying(url: str, path: str, body: dict, *, compress: bool,
                   sleep=time.sleep) -> tuple[int, dict]:
    """`_post`, tried again at once when the connection was dropped mid-send.

    Only for a DROPPED connection (`_dropped`): a certificate refusal or a timeout is the same
    answer every time, and waiting to hear it twice helps nobody. Safe to repeat because the
    server keys a submission by its receipt — a retry of work that did land is answered
    `duplicate`, which the outbox treats as delivered.
    """
    for wait in RETRY_WAITS + (None,):
        try:
            return _post(url, path, body, compress=compress)
        except Unreachable as e:
            if wait is None or isinstance(e, Untrusted) or not _dropped(e.__cause__):
                raise
            sleep(wait)
    raise AssertionError("unreachable")                      # the loop always returns or raises


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
        raise _wrap(e, url) from e


def check_code(url: str, code: str) -> dict:
    """What this code is for — or why it cannot be used.

    Called at ARM time, so a dead code costs a student a moment instead of an evening. Without it
    gBuilder accepts any well-formed code, because the check symbol is self-verifying: a code the
    course never issued still arms, and the student only finds out when they try to hand in.

    Returns `{ok, title, brief, session_minutes, grace_minutes, valid_until}` or
    `{ok: False, error}`. `grace_minutes` is how long after `valid_until` the server still takes
    the work, tagged LATE; zero means the deadline is a hard stop.
    """
    if not url:
        return {"ok": False, "error": "No course server is configured."}
    _require_tls(url)
    _, r = _get(url, "/api/activity?code=" + urllib.parse.quote(code))
    return r if isinstance(r, dict) else {"ok": False, "error": "Unexpected reply."}


def submit(url: str, code: str, proof: dict, topology: dict | None = None,
           shadows: dict | None = None, *, sleep=time.sleep) -> dict:
    """Hand in the work. Returns `{ok, receipt, within_session}` or `{ok: False, error}`.

    `topology` is what makes the submission runnable for the teacher. It is optional here only so
    the failure is graceful if a caller has none — the server refuses a topology that does not
    match the proof, so sending a wrong one is never quietly accepted.

    Sent gzipped first. A 500 is what a Teaching Center from before gzip answers — its JSON
    parser throws on the bytes, and the handler's catch-all reports that as a server error — so
    on a 500 the same body goes again uncompressed. A current server never answers a well-formed
    submission with a 500, so the cost of the fallback on a server that is genuinely broken is
    one extra request before the same answer is reported. `sleep` is the retry wait, injectable
    so the tests do not.
    """
    if not url:
        return {"ok": False, "error": "No course server is configured."}
    _require_tls(url)
    body: dict = {"code": code, "proof": proof}
    if topology:
        body["topology"] = topology
    if shadows:
        # An OS lab's deliverable. Sent only when there IS one, so a networking submission is
        # exactly the shape it has always been.
        body["shadows"] = shadows
    status, r = _post_retrying(url, "/api/activity/submit", body, compress=True, sleep=sleep)
    if status == 500:
        status, r = _post_retrying(url, "/api/activity/submit", body, compress=False,
                                   sleep=sleep)
    return r if isinstance(r, dict) else {"ok": False, "error": "Unexpected reply."}
