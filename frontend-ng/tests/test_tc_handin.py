"""Handing in: gBuilder uploads the runnable work, and a stolen file is worthless.

The whole point of the code is provenance. Everything here is one property, tested from both
sides:

    the chain is bound to the CODE          (verify_proof(expect_ticket=...))
    the chain commits to the TOPOLOGY       (submit entry carries sha256 of it)
    therefore the topology is bound to the code, and a classmate's project file
    cannot be handed in under your own.

Driven against a real server over a real socket, because that binding is only worth anything if
the server actually enforces it.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini.domain import proof as P                            # noqa: E402
from gini.services import tc_submit                           # noqa: E402

HOUR = 3600.0


@pytest.fixture
def tc(tmp_path, monkeypatch, tls_pair, trust_tls):
    monkeypatch.setenv("COURSE_ROOT", str(tmp_path))
    monkeypatch.setenv("ADMIN_ID", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    for mod in [m for m in list(sys.modules) if m.startswith("gini_teaching_center")]:
        sys.modules.pop(mod, None)
    from gini_teaching_center.store import Store
    Store._instances.clear()
    from gini_teaching_center import server
    from gini_teaching_center import accounts as A
    server.ROOT = tmp_path
    server.MATERIALS = tmp_path / "materials"
    server.MATERIALS.mkdir(parents=True, exist_ok=True)
    server._ACCTS = A.Accounts(tmp_path)
    server._STORE = Store(tmp_path)
    server._ACCTS.ensure_admin()
    # HTTPS, because that is the only thing GINI speaks now — including on loopback, which can
    # hold a certificate like any other name.
    cert, key = tls_pair
    ctx = server._tls_context(str(cert), str(key))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{httpd.server_address[1]}"

    tok = _call(url, "/auth/login", {"id": "boss", "password": "correct-horse"})["session"]
    _call(url, "/api/courses", {"id": "comp535", "title": "Networks"}, tok)
    _call(url, "/api/activities/save",
          {"course": "comp535", "lab": "lab1", "title": "Multi-LAN", "brief": "Join two LANs.",
           "vend_until": time.time() + HOUR, "session_minutes": 60}, tok)
    _call(url, "/api/activities/release", {"course": "comp535", "lab": "lab1"}, tok)
    try:
        yield _Course(url, tok)
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(url, path, body=None, tok=""):
    req = urllib.request.Request(
        url + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
        method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"null")


class _Course:
    def __init__(self, url, token):
        self.url, self.token = url, token

    def code(self) -> str:
        return _call(self.url, "/api/activity?course=comp535&lab=lab1")["code"]

    def staff(self, path):
        return _call(self.url, path, None, self.token)


def a_topology(kinds=("router", "host")):
    """A runnable project, built by the REAL domain model.

    Hand-writing this dict was the first thing I tried, and `Topology.from_dict` rejected it — a
    device has `type_key`, not `type`. A fixture that only resembles the real shape would have
    tested the digest binding perfectly while proving nothing about whether a teacher can actually
    open the file.
    """
    from gini.domain.topology import Topology
    t = Topology("lab")
    made = [t.add_device(k) for k in kinds]
    for d in made[1:]:
        t.add_link(d.id, made[0].id)
    return t.to_dict()


def a_chain(code, topology, *, t0=None):
    """A chain recorded under `code` that ends by committing to `topology`."""
    from gini_teaching_center import activities as ACT
    t0 = t0 if t0 is not None else time.time()
    chain = P.Chain.start(ACT.normalize(code), assignment="comp535/lab1",
                          gini_version="test", t=t0)
    for d in topology["devices"]:
        chain.append("place", {"id": d["id"], "type": d["type_key"], "name": d["name"]}, t=t0 + 1)
    chain.append("submit", {"artifact": P.artifact_summary(topology)}, t=t0 + 300)
    return P.build_proof(chain)


# -- handing in ---------------------------------------------------------------- #
def test_gbuilder_hands_in_the_work_and_gets_a_receipt(tc):
    code = tc.code()
    topo = a_topology()
    r = tc_submit.submit(tc.url, code, a_chain(code, topo), topo)
    assert r["ok"], r
    assert r["receipt"]


def test_the_teacher_can_download_something_gbuilder_can_open(tc):
    """A report you cannot run is half a report."""
    from gini.domain.topology import Topology
    from gini.services import persistence
    code = tc.code()
    topo = a_topology(("router", "host", "host"))
    r = tc_submit.submit(tc.url, code, a_chain(code, topo), topo)

    req = urllib.request.Request(
        tc.url + "/api/submissions/topology?receipt=" + r["receipt"],
        headers={"Authorization": "Bearer " + tc.token})
    with urllib.request.urlopen(req, timeout=10) as res:
        assert "attachment" in res.headers.get("Content-Disposition", "")
        data = json.loads(res.read())

    assert data["format"] == persistence.FORMAT and data["version"] == persistence.VERSION
    reopened = Topology.from_dict(data["topology"])          # the real loader, not a lookalike
    assert sorted(d["name"] for d in data["topology"]["devices"]) == ["M1", "M2", "R1"]
    assert len(reopened.devices) == 3 and len(reopened.links) == 2


def test_the_report_says_the_work_is_runnable(tc):
    code = tc.code()
    topo = a_topology()
    r = tc_submit.submit(tc.url, code, a_chain(code, topo), topo)
    assert tc.staff("/api/receipt?receipt=" + r["receipt"])["runnable"] is True


# -- provenance: a stolen file is worthless ------------------------------------ #
def test_someone_elses_topology_under_your_own_code_is_refused(tc):
    """THE property. Ravi builds a network; Paul takes the project file and submits it under his
    own code. Paul's chain never placed those elements, so it commits to a different digest."""
    ravi_code, paul_code = tc.code(), tc.code()
    ravi_topo = a_topology(("router", "host", "host"))
    paul_topo = a_topology(("switch",))

    tc_submit.submit(tc.url, ravi_code, a_chain(ravi_code, ravi_topo), ravi_topo)
    stolen = tc_submit.submit(tc.url, paul_code, a_chain(paul_code, paul_topo), ravi_topo)
    assert not stolen["ok"]
    assert stolen["reason"] == "wrong_topology"


def test_swapping_the_file_after_recording_is_refused(tc):
    """The subtler version: build something real, then quietly hand in a better topology. The
    chain already committed to the digest of what was actually built."""
    code = tc.code()
    built = a_topology(("router",))
    better = a_topology(("router", "host", "host"))
    r = tc_submit.submit(tc.url, code, a_chain(code, built), better)
    assert not r["ok"] and r["reason"] == "wrong_topology"


def test_a_classmates_whole_proof_is_still_refused(tc):
    """Taking the proof AND the topology — the chain is bound to the code it was recorded under."""
    ravi_code, paul_code = tc.code(), tc.code()
    topo = a_topology()
    ravi_proof = a_chain(ravi_code, topo)
    r = tc_submit.submit(tc.url, paul_code, ravi_proof, topo)
    assert not r["ok"] and r["reason"] == "bad_proof"


def test_an_edited_topology_changes_the_digest(tc):
    """Renaming one device is enough. This is what makes the digest a binding and not a formality."""
    code = tc.code()
    topo = a_topology()
    edited = json.loads(json.dumps(topo))
    edited["devices"][0]["name"] = "R99"
    r = tc_submit.submit(tc.url, code, a_chain(code, topo), edited)
    assert not r["ok"] and r["reason"] == "wrong_topology"


# -- arming -------------------------------------------------------------------- #
def test_a_code_the_course_never_issued_is_caught_at_arm_time(tc):
    """GINI codes are self-checking, so a made-up code arms fine offline. Asking the server is the
    difference between costing a student a moment and costing them an evening."""
    from gini.domain import ticket as T
    assert not tc_submit.check_code(tc.url, T.mint().pretty)["ok"]


def test_a_real_code_arms(tc):
    r = tc_submit.check_code(tc.url, tc.code())
    assert r["ok"] and r["title"] == "Multi-LAN"
    assert r["session_minutes"] == 60


def test_an_unreachable_server_is_distinguishable_from_a_refusal(tc):
    """Different advice: a refusal is about the code, this is about the network — and a student
    whose wifi dropped must not be told their code is bad."""
    with pytest.raises(tc_submit.Unreachable):
        tc_submit.check_code("https://127.0.0.1:9", "AAAA-AAAA")
    with pytest.raises(tc_submit.Unreachable):
        tc_submit.submit("https://127.0.0.1:9", "AAAA-AAAA", {})


# -- backwards compatibility ---------------------------------------------------- #
def test_a_proof_with_no_topology_is_still_accepted(tc):
    """An older gBuilder sends only the proof. Refusing it would lock out a student mid-term over
    a version mismatch; the report says plainly that no runnable copy came."""
    code = tc.code()
    r = tc_submit.submit(tc.url, code, a_chain(code, a_topology()))
    assert r["ok"]
    assert tc.staff("/api/receipt?receipt=" + r["receipt"])["runnable"] is False


def test_downloading_a_topology_that_was_never_sent_says_so(tc):
    code = tc.code()
    r = tc_submit.submit(tc.url, code, a_chain(code, a_topology()))
    out = tc.staff("/api/submissions/topology?receipt=" + r["receipt"])
    assert "no runnable copy" in out["error"]


def test_the_topology_download_needs_a_staff_session(tc):
    code = tc.code()
    topo = a_topology()
    r = tc_submit.submit(tc.url, code, a_chain(code, topo), topo)
    assert _call(tc.url, "/api/submissions/topology?receipt=" + r["receipt"])["error"]


# -- when it never made it up --------------------------------------------------- #
# The failure this exists for: a student finishes, the code lapses before the upload lands, and
# the proof becomes unacceptable FOR EVER — `expired` is deliberately not settled in the outbox,
# so gBuilder retries until the end of time something the server will refuse every time. They hold
# a correct receipt for work the Teaching Center has never heard of. Two ways out, both here: a
# grace period the server applies on its own, and a member of staff taking the file in by hand.
def _expire(code):
    """Backdate the code, the way an evening does."""
    from gini_teaching_center import activities as ACT
    from gini_teaching_center import server as S
    row = dict(S._STORE.code(ACT.normalize(code)))
    row["valid_until"] = time.time() - HOUR
    S._STORE.code_put(row)
    return row


def test_a_lapsed_code_is_refused_the_ordinary_way(tc):
    """The premise. Without this the rest of these tests would pass over a server that never
    refused anything."""
    code = tc.code()
    topo = a_topology()
    _expire(code)
    r = tc_submit.submit(tc.url, code, a_chain(code, topo), topo)
    assert not r["ok"] and r["reason"] == "expired"


def test_staff_can_take_in_what_the_server_refused(tc):
    code = tc.code()
    topo = a_topology()
    proof = a_chain(code, topo)
    _expire(code)
    r = _call(tc.url, "/api/submissions/accept", {"proof": proof, "topology": topo}, tc.token)
    assert r["ok"], r
    assert r["receipt"]
    rep = tc.staff("/api/receipt?receipt=" + r["receipt"])
    assert rep["late"] is True
    assert rep["accepted_by"] == "boss"
    assert rep["verdict"] == "pass"


def test_what_staff_took_in_is_runnable_like_any_other(tc):
    """Half a recovery would be accepting the proof and losing the work."""
    from gini.domain.topology import Topology
    from gini.services import persistence
    code = tc.code()
    topo = a_topology(("router", "host", "host"))
    proof = a_chain(code, topo)
    _expire(code)
    r = _call(tc.url, "/api/submissions/accept", {"proof": proof, "topology": topo}, tc.token)
    req = urllib.request.Request(
        tc.url + "/api/submissions/topology?receipt=" + r["receipt"],
        headers={"Authorization": "Bearer " + tc.token})
    with urllib.request.urlopen(req, timeout=10) as res:
        data = json.loads(res.read())
    assert data["format"] == persistence.FORMAT
    reopened = Topology.from_dict(data["topology"])          # the real loader, not a lookalike
    assert len(reopened.devices) == 3 and len(reopened.links) == 2


def test_a_tampered_proof_is_refused_however_it_arrives(tc):
    """Staff acceptance waives the clock. It is not a way to launder a bad proof through a
    kindness, and this is the test that keeps it that way."""
    code = tc.code()
    topo = a_topology()
    proof = a_chain(code, topo)
    proof["entries"][1]["data"]["name"] = "EDITED"
    _expire(code)
    r = _call(tc.url, "/api/submissions/accept", {"proof": proof, "topology": topo}, tc.token)
    assert not r["ok"] and r["reason"] == "bad_proof"


def test_someone_elses_topology_is_refused_however_it_arrives(tc):
    code = tc.code()
    proof = a_chain(code, a_topology())
    _expire(code)
    r = _call(tc.url, "/api/submissions/accept",
              {"proof": proof, "topology": a_topology(("router", "host", "host"))}, tc.token)
    assert not r["ok"]


def test_taking_one_in_needs_a_staff_session(tc):
    """Otherwise the deadline waiver is available to whoever holds the proof — which is the
    student, which is everybody."""
    code = tc.code()
    topo = a_topology()
    proof = a_chain(code, topo)
    _expire(code)
    assert _call(tc.url, "/api/submissions/accept",
                 {"proof": proof, "topology": topo})["error"]


def test_the_same_work_cannot_be_taken_in_twice(tc):
    code = tc.code()
    topo = a_topology()
    proof = a_chain(code, topo)
    _expire(code)
    assert _call(tc.url, "/api/submissions/accept",
                 {"proof": proof, "topology": topo}, tc.token)["ok"]
    again = _call(tc.url, "/api/submissions/accept",
                  {"proof": proof, "topology": topo}, tc.token)
    assert not again["ok"] and again["reason"] in ("duplicate", "already_used")


def test_a_proof_for_a_code_this_course_never_issued_is_refused(tc):
    """Not a late submission — a proof from somewhere else entirely."""
    from gini.domain import proof as P
    from gini_teaching_center import activities as ACT
    chain = P.Chain.start(ACT.normalize("AAAA-AAAA"), assignment="x/y", gini_version="test")
    chain.append("submit", {"artifact": P.artifact_summary(a_topology())})
    r = _call(tc.url, "/api/submissions/accept",
              {"proof": P.build_proof(chain)}, tc.token)
    assert not r["ok"] and r["reason"] == "unknown_code"


def test_a_grace_period_takes_the_ordinary_late_hand_in_without_anybody_asked(tc):
    """The common case — finished at 23:58, wifi dropped, uploaded at 00:20 — should not need a
    teacher at all. The proof goes up on its own and arrives tagged."""
    _call(tc.url, "/api/activities/save",
          {"course": "comp535", "lab": "lab1", "title": "Multi-LAN", "brief": "Join two LANs.",
           "vend_until": time.time() + HOUR, "session_minutes": 60, "grace_minutes": 360},
          tc.token)
    code = tc.code()
    topo = a_topology()
    proof = a_chain(code, topo)
    _expire(code)                                     # an hour past the deadline, inside grace
    r = tc_submit.submit(tc.url, code, proof, topo)
    assert r["ok"], r
    rep = tc.staff("/api/receipt?receipt=" + r["receipt"])
    assert rep["late"] is True
    assert rep["accepted_by"] == "", "nobody waived anything — the server took it on its own"


def test_the_arm_reply_tells_gbuilder_about_the_grace_period(tc):
    _call(tc.url, "/api/activities/save",
          {"course": "comp535", "lab": "lab1", "title": "Multi-LAN", "brief": "Join two LANs.",
           "vend_until": time.time() + HOUR, "session_minutes": 60, "grace_minutes": 90},
          tc.token)
    assert tc_submit.check_code(tc.url, tc.code())["grace_minutes"] == 90


def test_the_submissions_list_shows_which_ones_were_late(tc):
    """A teacher scanning what arrived must see it without opening each one."""
    _call(tc.url, "/api/activities/save",
          {"course": "comp535", "lab": "lab1", "title": "Multi-LAN", "brief": "Join two LANs.",
           "vend_until": time.time() + HOUR, "session_minutes": 60, "grace_minutes": 360},
          tc.token)
    ontime = tc.code()
    topo = a_topology()
    tc_submit.submit(tc.url, ontime, a_chain(ontime, topo), topo)
    late = tc.code()
    late_proof = a_chain(late, topo)
    _expire(late)
    tc_submit.submit(tc.url, late, late_proof, topo)
    rows = {r["receipt"]: r["late"] for r in tc.staff("/api/submissions?course=comp535")}
    assert sorted(rows.values()) == [0, 1], rows
