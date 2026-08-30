"""Teacher marking: find a submission by receipt, and open the student's work.

Driven against the REAL Teaching Center over real TLS, because the parts worth defending are the
seams — a staff session, a course boundary, and a download that gBuilder can actually open. A fake
server would agree with whatever the client did.

The rule underneath all of it: v1 DESCRIBES and the teacher judges. Nothing here invents a score.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini.domain import proof as P                              # noqa: E402
from gini.domain.topology import Topology                       # noqa: E402
from gini.services import tc_staff, tc_submit                   # noqa: E402

HOUR = 3600.0


def _post(url, path, body, session=""):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    if session:
        req.add_header("Authorization", f"Bearer {session}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


@pytest.fixture
def course(tmp_path, monkeypatch, tls_pair, trust_tls):
    """A live TLS server with one released activity, and a submission already handed in."""
    monkeypatch.setenv("COURSE_ROOT", str(tmp_path))
    monkeypatch.setenv("ADMIN_ID", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    for mod in [m for m in list(sys.modules) if m.startswith("gini_teaching_center")]:
        sys.modules.pop(mod, None)
    from gini_teaching_center.store import Store
    Store._instances.clear()
    from gini_teaching_center import accounts as A
    from gini_teaching_center import server
    server.ROOT = tmp_path
    server.MATERIALS = tmp_path / "materials"
    server.MATERIALS.mkdir(parents=True, exist_ok=True)
    server._ACCTS = A.Accounts(tmp_path)
    server._STORE = Store(tmp_path)
    server._ACCTS.ensure_admin()

    cert, key = tls_pair
    ctx = server._tls_context(str(cert), str(key))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{httpd.server_address[1]}"

    tok = _post(url, "/auth/login", {"id": "boss", "password": "correct-horse"})["session"]
    _post(url, "/api/courses", {"id": "comp535", "title": "Networks"}, tok)
    _post(url, "/api/activities/save",
          {"course": "comp535", "lab": "lab1", "title": "Multi-LAN", "brief": "Join two LANs.",
           "vend_until": time.time() + HOUR, "session_minutes": 60}, tok)
    _post(url, "/api/activities/release", {"course": "comp535", "lab": "lab1"}, tok)

    # a student takes a code and hands work in, exactly as gBuilder does
    with urllib.request.urlopen(url + "/api/activity?course=comp535&lab=lab1", timeout=10) as r:
        code = json.loads(r.read())["code"]
    topo = Topology("submitted")
    r1 = topo.add_device("router"); s1 = topo.add_device("switch")
    topo.add_link(r1.id, s1.id)
    chain = P.Chain.start(code.replace("-", ""), assignment="comp535/lab1", gini_version="test")
    chain.append("place", {"id": r1.id, "type": "router", "name": r1.name})
    chain.append("submit", {"artifact": P.artifact_summary(topo.to_dict())})
    proof = P.build_proof(chain, "test")
    answer = tc_submit.submit(url, code, proof, topo.to_dict())
    assert answer.get("ok"), answer
    try:
        yield {"url": url, "receipt": answer["receipt"], "admin": tok, "topo": topo}
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- signing in ---------------------------------------------------------------- #
def test_a_staff_password_buys_a_session_and_is_not_kept(course):
    out = tc_staff.sign_in(course["url"], "boss", "correct-horse")
    assert out["session"] and out["role"] and out["who"] == "boss"
    assert tc_staff.whoami(course["url"], out["session"]).get("who") == "boss"


def test_a_wrong_password_is_refused_not_reported_as_an_outage(course):
    """`Refused` and `Unreachable` mean opposite things to whoever is standing there: one is a
    typo, the other is the network. Conflating them sends a marker to fix the wrong thing."""
    with pytest.raises(tc_staff.Refused) as e:
        tc_staff.sign_in(course["url"], "boss", "not-the-password")
    assert "password" in str(e.value).lower()
    assert not isinstance(e.value, tc_staff.Unreachable)


def test_signing_out_lets_the_session_go(course):
    s = tc_staff.sign_in(course["url"], "boss", "correct-horse")["session"]
    tc_staff.sign_out(course["url"], s)
    assert tc_staff.whoami(course["url"], s) == {}


def test_an_expired_session_reads_as_nobody_rather_than_raising(course):
    """A day ending is not an error. `whoami` answering {} is what lets the dialog put the sign-in
    form back instead of showing a stack trace."""
    assert tc_staff.whoami(course["url"], "not-a-session") == {}


# -- reading a submission ------------------------------------------------------- #
def test_the_report_describes_the_work_without_scoring_it(course):
    s = tc_staff.sign_in(course["url"], "boss", "correct-horse")["session"]
    rep = tc_staff.report(course["url"], s, course["receipt"])
    assert rep["receipt"] == course["receipt"]
    assert rep["activity"] == "comp535/lab1" and rep["title"] == "Multi-LAN"
    assert rep["verdict"]                       # integrity of the proof…
    assert rep["entries"] >= 1
    assert rep["runnable"] is True              # …and a copy the teacher can open
    assert "score" not in rep and "grade" not in rep and "mark" not in rep


def test_an_unknown_receipt_says_so_plainly(course):
    s = tc_staff.sign_in(course["url"], "boss", "correct-horse")["session"]
    with pytest.raises(tc_staff.Refused):
        tc_staff.report(course["url"], s, "ZZZZ-ZZZZ")


def test_marking_needs_a_session(course):
    """The student endpoints are code-authenticated; these are not. Reading somebody's submitted
    work is staff-only."""
    with pytest.raises(tc_staff.Refused):
        tc_staff.report(course["url"], "", course["receipt"])


# -- opening it ----------------------------------------------------------------- #
def test_the_download_is_a_project_gbuilder_can_open(course):
    """THE property that makes marking useful: it opens with no conversion step. A report you
    cannot run is half a report."""
    from gini.services.persistence import FORMAT
    s = tc_staff.sign_in(course["url"], "boss", "correct-horse")["session"]
    project = tc_staff.topology(course["url"], s, course["receipt"])
    assert project["format"] == FORMAT
    topo = Topology.from_dict(project["topology"])
    assert len(topo.devices) == len(course["topo"].devices)
    assert {d.type_key for d in topo.devices.values()} == {"router", "switch"}


def test_the_downloaded_work_is_the_work_that_was_submitted(course):
    """Not merely a valid project — the SAME one, or marking is fiction."""
    s = tc_staff.sign_in(course["url"], "boss", "correct-horse")["session"]
    got = Topology.from_dict(tc_staff.topology(course["url"], s, course["receipt"])["topology"])
    assert P.artifact_summary(got.to_dict()) == P.artifact_summary(course["topo"].to_dict())


def test_downloading_needs_a_session_too(course):
    with pytest.raises(tc_staff.Refused):
        tc_staff.topology(course["url"], "", course["receipt"])


# -- the transport rule applies here too ---------------------------------------- #
@pytest.mark.parametrize("call", ["sign_in", "report", "topology"])
def test_no_staff_call_will_speak_plain_http(call):
    """A staff password, and then a twelve-hour session token, are exactly what must not cross a
    campus network in the clear."""
    fn = getattr(tc_staff, call)
    args = {"sign_in": ("http://x", "boss", "pw"), "report": ("http://x", "s", "AAAA-AAAA"),
            "topology": ("http://x", "s", "AAAA-AAAA")}[call]
    with pytest.raises(tc_submit.Insecure):
        fn(*args)
