"""What happens when the upload does not land.

The scenario this exists for: a student finishes, gBuilder shows a receipt, the wifi drops, and the
student walks away holding a receipt the Teaching Center has never heard of. Their teacher types it
in and is told there is no such submission. Neither of them can tell who is wrong, and the natural
student reaction — redo the lab under a new code — is the one thing that actually loses work.

The property that makes recovery safe is that **the receipt is computed from the proof, not issued
by the server**, so it is correct from the moment the student finishes. Everything else here is
about making sure the work eventually arrives on its own.
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
from gini.services import outbox, tc_submit                   # noqa: E402

HOUR = 3600.0


# --------------------------------------------------------------------------- #
# the outbox on its own — no server needed
# --------------------------------------------------------------------------- #
@pytest.fixture
def box(tmp_path):
    return tmp_path / "outbox"


def a_proof(code="ABCD1234EFGH", *, topo=None, t0=1000.0):
    topo = topo if topo is not None else {"devices": [], "links": []}
    chain = P.Chain.start(code, assignment="comp535/lab1", gini_version="test", t=t0)
    chain.append("place", {"id": "1", "type": "router", "name": "R1"}, t=t0 + 1)
    chain.append("submit", {"artifact": P.artifact_summary(topo)}, t=t0 + 600)
    return P.build_proof(chain, "test")


def test_the_receipt_is_the_same_before_and_after_it_reaches_the_server(box):
    """THE property the whole recovery rests on. gBuilder computes the receipt from the proof's
    MAC; the Teaching Center computes it with the same function. So a receipt handed to an
    instructor while the work is still stuck is the receipt they will find later."""
    from gini_teaching_center import activities as ACT
    proof = a_proof()
    outbox.queue(proof, root=box)
    assert outbox.pending(box)[0]["receipt"] == P.receipt_code(proof)
    # what the server would independently derive from the same proof
    assert ACT._proof.receipt_code(proof) == P.receipt_code(proof)


def test_a_submission_is_queued_before_it_is_attempted(box):
    outbox.queue(a_proof(), {"devices": [], "links": []}, root=box)
    assert len(outbox.pending(box)) == 1


def test_the_runnable_topology_is_kept_too(box):
    """Otherwise a retry could only send the proof, and the teacher would get an unrunnable
    submission for a lab the student actually built."""
    topo = {"devices": [{"id": "1", "name": "R1", "type_key": "router"}], "links": []}
    outbox.queue(a_proof(topo=topo), topo, root=box)
    assert outbox.pending(box)[0]["topology"] == topo


def test_a_network_failure_keeps_the_entry(box):
    outbox.queue(a_proof(), root=box)
    r = outbox.flush("http://x", lambda *a: {"ok": False, "error": "network down"}, root=box)
    assert r["kept"] and len(outbox.pending(box)) == 1
    assert outbox.pending(box)[0]["last_error"] == "network down"


def test_attempts_are_counted_so_a_stuck_submission_is_visible(box):
    outbox.queue(a_proof(), root=box)
    for _ in range(3):
        outbox.flush("http://x", lambda *a: {"ok": False, "error": "nope"}, root=box)
    assert outbox.pending(box)[0]["attempts"] == 3


def test_the_queue_time_survives_retries(box):
    """How long something has been stuck is the thing a teacher actually wants to know."""
    outbox.queue(a_proof(), root=box, now=1000.0)
    outbox.flush("http://x", lambda *a: {"ok": False, "error": "no"}, root=box)
    outbox.queue(a_proof(), root=box, now=9999.0)          # re-queued by a second Generate
    assert outbox.pending(box)[0]["queued"] == 1000.0


def test_success_clears_it(box):
    outbox.queue(a_proof(), root=box)
    r = outbox.flush("http://x", lambda *a: {"ok": True, "receipt": "R"}, root=box)
    assert r["sent"] and outbox.pending(box) == []


def test_a_duplicate_clears_it_too(box):
    """"Already submitted" means an earlier attempt landed. Keeping it would retry forever."""
    outbox.queue(a_proof(), root=box)
    outbox.flush("http://x", lambda *a: {"ok": False, "reason": "duplicate", "error": "dup"},
                 root=box)
    assert outbox.pending(box) == []


def test_a_refusal_NEVER_deletes_the_students_work(box):
    """An expired code, a deleted lab, a server bug — none of them are a reason to throw away the
    only copy of an evening's work. It stays, with the reason, for a human to deal with."""
    outbox.queue(a_proof(), root=box)
    outbox.flush("http://x", lambda *a: {"ok": False, "reason": "expired", "error": "too late"},
                 root=box)
    kept = outbox.pending(box)
    assert len(kept) == 1 and kept[0]["last_error"] == "too late"
    assert kept[0]["proof"]["entries"]                     # the whole chain is still there


def test_an_exception_in_transit_is_not_a_reason_to_drop_it(box):
    outbox.queue(a_proof(), root=box)

    def explode(*a):
        raise RuntimeError("connection reset")

    r = outbox.flush("http://x", explode, root=box)
    assert r["kept"] and len(outbox.pending(box)) == 1


def test_two_submissions_queue_independently_and_flush_together(box):
    outbox.queue(a_proof("ABCD1234EFGH"), root=box)
    outbox.queue(a_proof("MNPQ5678RSTV"), root=box)
    assert len(outbox.pending(box)) == 2
    r = outbox.flush("http://x", lambda *a: {"ok": True}, root=box)
    assert len(r["sent"]) == 2 and outbox.pending(box) == []


def test_pressing_generate_twice_does_not_queue_two_copies(box):
    proof = a_proof()
    outbox.queue(proof, root=box)
    outbox.queue(proof, root=box)
    assert len(outbox.pending(box)) == 1


def test_a_corrupt_entry_does_not_break_the_flush(box):
    """A half-written file after a crash must not stop the others from being sent."""
    outbox.queue(a_proof(), root=box)
    (box / "garbage.json").write_text("{ not json")
    r = outbox.flush("http://x", lambda *a: {"ok": True}, root=box)
    assert len(r["sent"]) == 1


def test_an_empty_outbox_is_a_no_op(box):
    assert outbox.pending(box) == []
    assert outbox.flush("http://x", lambda *a: {"ok": True}, root=box) == {
        "sent": [], "kept": [], "errors": []}


# --------------------------------------------------------------------------- #
# end to end: stuck, then caught up, against a real server
# --------------------------------------------------------------------------- #
@pytest.fixture
def tc(tmp_path, monkeypatch):
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
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    tok = _call(url, "/auth/login", {"id": "boss", "password": "correct-horse"})["session"]
    _call(url, "/api/courses", {"id": "comp535", "title": "Networks"}, tok)
    _call(url, "/api/activities/save",
          {"course": "comp535", "lab": "lab1", "title": "Multi-LAN",
           "vend_until": time.time() + HOUR, "session_minutes": 60}, tok)
    _call(url, "/api/activities/release", {"course": "comp535", "lab": "lab1"}, tok)
    try:
        yield url, tok
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


def _real_work(url, tmp_path):
    """A real code and a real chain, as gBuilder would produce them."""
    from gini_teaching_center import activities as ACT
    from gini.domain.topology import Topology
    code = _call(url, "/api/activity?course=comp535&lab=lab1")["code"]
    t = Topology("lab")
    r1 = t.add_device("router")
    t.add_link(t.add_device("host").id, r1.id)
    topo = t.to_dict()
    t0 = time.time()
    chain = P.Chain.start(ACT.normalize(code), assignment="comp535/lab1",
                          gini_version="test", t=t0)
    for d in topo["devices"]:
        chain.append("place", {"id": d["id"], "type": d["type_key"], "name": d["name"]}, t=t0 + 1)
    chain.append("submit", {"artifact": P.artifact_summary(topo)}, t=t0 + 300)
    return code, P.build_proof(chain, "test"), topo


def test_a_stuck_submission_catches_up_on_the_next_flush(tc, tmp_path):
    """The whole point. The student finished offline; gBuilder retries and it lands."""
    url, tok = tc
    box = tmp_path / "outbox"
    code, proof, topo = _real_work(url, tmp_path)
    receipt = P.receipt_code(proof)

    outbox.queue(proof, topo, root=box)
    outbox.flush("http://127.0.0.1:9", tc_submit.submit, root=box)      # offline: nothing lands
    assert _call(url, "/api/receipt?receipt=" + receipt, None, tok).get("error")
    assert len(outbox.pending(box)) == 1

    outbox.flush(url, tc_submit.submit, root=box)                       # back on the network
    assert outbox.pending(box) == []
    rep = _call(url, "/api/receipt?receipt=" + receipt, None, tok)
    assert rep["receipt"] == receipt and rep["runnable"] is True


def test_the_receipt_the_student_already_gave_away_is_the_one_that_turns_up(tc, tmp_path):
    """A student hands their instructor a receipt on Tuesday; the work syncs on Wednesday. The
    instructor must find it under the receipt they were given, not a new one."""
    url, tok = tc
    box = tmp_path / "outbox"
    code, proof, topo = _real_work(url, tmp_path)
    told_to_the_teacher = P.receipt_code(proof)

    outbox.queue(proof, topo, root=box)
    outbox.flush(url, tc_submit.submit, root=box)
    assert _call(url, "/api/receipt?receipt=" + told_to_the_teacher, None, tok)["receipt"] == \
        told_to_the_teacher


def test_flushing_twice_is_harmless(tc, tmp_path):
    """gBuilder flushes on every launch and every arm. A second flush of work already sent must
    not produce an error the student has to interpret."""
    url, tok = tc
    box = tmp_path / "outbox"
    _, proof, topo = _real_work(url, tmp_path)
    outbox.queue(proof, topo, root=box)
    outbox.flush(url, tc_submit.submit, root=box)
    outbox.queue(proof, topo, root=box)                 # as if it were re-queued
    r = outbox.flush(url, tc_submit.submit, root=box)
    assert r["sent"] and not r["kept"]                  # refused as a duplicate == already there


def test_the_teacher_is_told_the_work_may_simply_be_in_transit(tc, tmp_path):
    """"No such receipt" reads as "your student is lying". It is usually a sync that has not
    happened, and the message has to start the right conversation."""
    url, tok = tc
    _, proof, _ = _real_work(url, tmp_path)
    r = _call(url, "/api/receipt?receipt=" + P.receipt_code(proof), None, tok)
    msg = r["error"]
    assert "yet" in msg                       # not "there is no such thing"
    assert "locally" in msg                   # says WHY the student's receipt is still valid
    assert "retries" in msg                   # says what will happen without anyone doing work


# --------------------------------------------------------------------------- #
# the gBuilder wiring, with Qt stubbed out
# --------------------------------------------------------------------------- #
def _load_strip(monkeypatch):
    """Import `proof_strip` with a stub Qt, so its logic can be exercised without a display.

    Loaded by file path rather than as `gini.ui.proof_strip`, because importing the package pulls
    in the whole main window.
    """
    import importlib.util
    import types
    pkg = types.ModuleType("PySide6")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "PySide6", pkg)

    class _Sig:
        def __init__(self, *a): self._fn = None
        def connect(self, fn): self._fn = fn
        def emit(self, *a):
            if self._fn:
                self._fn(*a)

    class _W:
        def __init__(self, *a, **k): pass
        def __getattr__(self, n): return lambda *a, **k: None

    core = types.ModuleType("PySide6.QtCore")
    core.Qt = _W()
    core.Signal = lambda *a: _Sig()
    widgets = types.ModuleType("PySide6.QtWidgets")
    for n in ("QHBoxLayout", "QLabel", "QLineEdit", "QPushButton", "QVBoxLayout", "QWidget"):
        setattr(widgets, n, _W)
    seen = []
    widgets.QMessageBox = type("QMessageBox", (), {
        "information": staticmethod(lambda *a: seen.append(("info",) + a[1:])),
        "warning": staticmethod(lambda *a: seen.append(("warn",) + a[1:]))})
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", core)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", widgets)

    spec = importlib.util.spec_from_file_location(
        "gini.ui.proof_strip", Path(__file__).resolve().parents[1]
        / "src/gini/ui/proof_strip.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "gini.ui.proof_strip", mod)
    spec.loader.exec_module(mod)
    return mod, seen


def test_the_strip_queues_the_work_BEFORE_it_tries_to_send(tmp_path, monkeypatch):
    """The ordering that makes the fallback real. Queue-after-send would lose exactly the case
    this exists for: the send that never returns."""
    mod, seen = _load_strip(monkeypatch)
    box = tmp_path / "outbox"
    monkeypatch.setattr(mod.outbox, "outbox_root", lambda: box)

    _, proof, topo = None, a_proof(), {"devices": [], "links": []}
    order = []
    monkeypatch.setattr(mod.tc_submit, "submit",
                        lambda *a, **k: (order.append("sent"), {"ok": False,
                                                                "error": "down"})[1])

    strip = object.__new__(mod.ProofStrip)
    strip.recorder = type("R", (), {"ctx": type("C", (), {
        "settings": type("S", (), {"tc_url": "http://127.0.0.1:9"})()})()})()
    strip._say = lambda *a, **k: None
    strip.handedIn = type("S", (), {"emit": staticmethod(lambda *a: order.append("emitted"))})()

    strip._hand_in({"ok": True, "proof": proof, "topology": topo,
                    "receipt": P.receipt_code(proof), "path": "/tmp/p.json"},
                   P.receipt_code(proof))
    # the send happens on a worker thread; the queue write does not
    assert len(outbox.pending(box)) == 1, "the work was not queued before sending"


def test_the_strip_tells_the_student_not_to_redo_the_lab(tmp_path, monkeypatch):
    """The message is the safety feature. A student who believes their work is lost will redo it
    under a new code, which is the one action that actually costs them the evening."""
    mod, seen = _load_strip(monkeypatch)
    box = tmp_path / "outbox"
    monkeypatch.setattr(mod.outbox, "outbox_root", lambda: box)
    proof = a_proof()
    receipt = P.receipt_code(proof)

    strip = object.__new__(mod.ProofStrip)
    strip._say = lambda *a, **k: None
    strip._on_handed_in({"receipt": receipt, "path": "/tmp/p.json", "proof": proof},
                        {"ok": False, "unreachable": True, "error": "network down"})

    kind, title, text = seen[-1]
    assert kind == "warn"
    assert "safe" in text and receipt in text
    assert "do not redo" in text.lower()
    assert "try again automatically" in text.lower()


def test_a_successful_hand_in_clears_the_outbox_entry(tmp_path, monkeypatch):
    mod, seen = _load_strip(monkeypatch)
    box = tmp_path / "outbox"
    monkeypatch.setattr(mod.outbox, "outbox_root", lambda: box)
    proof = a_proof()
    receipt = P.receipt_code(proof)
    outbox.queue(proof, root=box)

    strip = object.__new__(mod.ProofStrip)
    strip._say = lambda *a, **k: None
    strip._on_handed_in({"receipt": receipt, "path": "/tmp/p.json", "proof": proof},
                        {"ok": True, "receipt": receipt, "within_session": True})
    assert outbox.pending(box) == []
    assert seen[-1][0] == "info" and "sent to the course server" in seen[-1][2]
