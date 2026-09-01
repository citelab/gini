"""Lab questions: authored by the teacher, asked of the student, read by a marker.

The design in one line: the teacher writes several questions with the answers they expect, each
student is asked a random selection, the answers land in the proof chain, and a person marks them.

Everything below is a rule that design depends on, written as the thing that must not happen:

  * a student's machine receiving the answer key — from arming, or from Ask GINI;
  * the questions changing under a student between arming and finishing;
  * an unanswered question stopping them handing in;
  * a question a code was already asked disappearing when the teacher edits the set;
  * anything, anywhere, deciding that an answer is right or wrong.

That last one is the whole point. GINI does not mark; it records, and a teaching assistant reads.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini_teaching_center import activities as ACT               # noqa: E402
from gini_teaching_center.store import Store                     # noqa: E402

from gini.domain import proof_events as ev                       # noqa: E402

HOUR = 3600.0

#: Three, with keys — the shape the design is written around: author a few, ask some of them.
QS = [{"prompt": "What IP did you give M1?", "answer": "10.0.0.2"},
      {"prompt": "Which command showed the route?", "answer": "ip route"},
      {"prompt": "Why did the first ping fail?", "answer": "no default gateway"}]


# ---- the store: authoring, choosing, and what survives an edit ------------------- #
@pytest.fixture
def store(tmp_path):
    Store._instances.clear()          # the Store is keyed-singleton; isolate each test
    st = Store(str(tmp_path))
    st.activity_put({"id": "c/lab1", "course": "c", "lab": "lab1", "title": "Multi-LAN",
                     "status": "released", "session_minutes": 60, "show_n": 2,
                     "vend_until": time.time() + HOUR, "created": time.time()})
    st.questions_put("c/lab1", QS)
    return st


def test_a_lab_holds_the_questions_it_was_given(store):
    assert [q["prompt"] for q in store.questions("c/lab1")] == [q["prompt"] for q in QS]


def test_a_code_is_asked_the_number_the_teacher_chose(store):
    assert len(store.pick_questions("AAA", "c/lab1", 2)) == 2


def test_the_choice_does_not_move_under_the_student(store):
    """Re-arming a code RESUMES its chain, and gBuilder asks the server again when it does. If the
    selection were made per request, a student who reopened their lab would be shown a different
    question from the one already answered in their own transcript."""
    first = [q["id"] for q in store.pick_questions("AAA", "c/lab1", 2)]
    assert [q["id"] for q in store.pick_questions("AAA", "c/lab1", 2)] == first
    assert [q["id"] for q in store.questions_for_code("AAA")] == first


def test_different_codes_are_not_all_asked_the_same_pair(store):
    """The reason for authoring more than you ask. A selection that came out identical every time
    would be an elaborate way of writing two questions."""
    seen = {tuple(sorted(q["id"] for q in store.pick_questions(c, "c/lab1", 2)))
            for c in ("A", "B", "C", "D", "E", "F", "G", "H")}
    assert len(seen) > 1, "every code drew the same pair"


def test_asking_more_than_exist_asks_all_of_them(store):
    assert len(store.pick_questions("AAA", "c/lab1", 99)) == 3


def test_a_lab_that_asks_nothing_asks_nothing(store):
    assert store.pick_questions("AAA", "c/lab1", 0) == []


def test_editing_the_set_does_not_erase_a_question_already_asked(store):
    """A code names its questions by id. Deleting one out from under an issued code would leave
    its report unable to say what the student was answering."""
    asked = store.questions_for_code("AAA") or store.pick_questions("AAA", "c/lab1", 2)
    store.questions_put("c/lab1", [{"prompt": "A completely new question", "answer": "yes"}])
    assert [q["id"] for q in store.questions_for_code("AAA")] == [q["id"] for q in asked]
    assert [q["prompt"] for q in store.questions("c/lab1")] == ["A completely new question"]


def test_a_question_nobody_was_ever_asked_is_genuinely_gone(store):
    """The other half: retiring is for questions with history. Keeping every draft a teacher ever
    typed would turn the editor into an archive."""
    store.questions_put("c/lab1", [QS[0]])
    assert len(store.questions("c/lab1", include_retired=True)) == 1


def test_a_new_question_does_not_inherit_a_deleted_ones_id(store):
    """Ids are not positions. If a teacher deletes question 2 and writes another, numbering by
    position would hand the newcomer the dead question's id — and a code already asked the old #2
    would report the new text as what the student was answering."""
    store.pick_questions("AAA", "c/lab1", 3)
    old2 = store.questions("c/lab1")[1]
    store.questions_put("c/lab1", [dict(store.questions("c/lab1")[0]),
                                   {"prompt": "Something else entirely", "answer": "x"}])
    fresh = next(q for q in store.questions("c/lab1") if q["prompt"] == "Something else entirely")
    assert fresh["id"] != old2["id"]
    asked = {q["id"]: q["prompt"] for q in store.questions_for_code("AAA")}
    assert asked[old2["id"]] == old2["prompt"], "an issued code was re-pointed at a new question"


def test_an_edit_keeps_the_id_so_it_stays_the_same_question(store):
    """Fixing a typo must not retire the question and mint a replacement — a code pointing at the
    old id would then be reported as asking something withdrawn."""
    q = store.questions("c/lab1")[0]
    store.questions_put("c/lab1", [{**q, "prompt": q["prompt"] + " (in CIDR)"}] + QS[1:])
    assert store.questions("c/lab1")[0]["id"] == q["id"]
    assert store.questions("c/lab1")[0]["prompt"].endswith("(in CIDR)")


# ---- the report: what a marker is shown ----------------------------------------- #
def _chain(*answers):
    return {"entries": [{"kind": k, "data": d} for k, d in answers]}


def _report(store, code="AAA", chain=None):
    return ACT.report({"receipt": "r1", "activity": "c/lab1", "code": code,
                       "data": {"proof": chain or _chain()}},
                      store.activity("c/lab1"), [], [],
                      questions=store.questions_for_code(code))


def test_the_answer_reaches_the_report_beside_the_key(store):
    q = store.pick_questions("AAA", "c/lab1", 2)[0]
    r = _report(store, chain=_chain(ev.answer(q["id"], q["prompt"], "10.0.0.2/24")))
    row = next(x for x in r["questions"] if x["id"] == q["id"])
    assert row["given"] == "10.0.0.2/24"
    assert row["key"] == q["answer"]
    assert row["prompt"] == q["prompt"]


def test_nothing_decides_whether_an_answer_is_right(store):
    """No score, no verdict, no flag — by design, and asserted so it stays that way. A student who
    writes "the default route was missing" against a key of "no default gateway" is right, and no
    string comparison available here can tell."""
    q = store.pick_questions("AAA", "c/lab1", 2)[0]
    r = _report(store, chain=_chain(ev.answer(q["id"], q["prompt"], "utter nonsense")))
    row = next(x for x in r["questions"] if x["id"] == q["id"])
    assert not {"correct", "score", "mark", "grade", "passed"} & set(row)
    assert "grade" not in r and "score" not in r


def test_an_unanswered_question_is_reported_rather_than_hidden(store):
    """A blank is a fact about the attempt, and one a marker needs. Dropping the row would make a
    student who answered nothing look identical to one who was never asked."""
    store.pick_questions("AAA", "c/lab1", 2)
    r = _report(store)
    assert len(r["questions"]) == 2
    assert all(x["answered"] is False and x["given"] == "" for x in r["questions"])


def test_thinking_again_shows_the_last_answer_and_says_it_moved(store):
    """The chain is append-only and a student may change their mind. The report shows what they
    settled on, and says how many passes there were so a marker can go and look."""
    q = store.pick_questions("AAA", "c/lab1", 2)[0]
    r = _report(store, chain=_chain(ev.answer(q["id"], q["prompt"], "first go"),
                                    ev.answer(q["id"], q["prompt"], "second thoughts")))
    row = next(x for x in r["questions"] if x["id"] == q["id"])
    assert row["given"] == "second thoughts" and row["revisions"] == 1


def test_a_question_edited_after_the_lab_shows_what_was_actually_asked(store):
    """The prompt is recorded in the chain WITH the answer for exactly this case. Showing only the
    current wording would present an answer as a reply to a question never put."""
    q = store.pick_questions("AAA", "c/lab1", 2)[0]
    chain = _chain(ev.answer(q["id"], q["prompt"], "10.0.0.2"))
    store.questions_put("c/lab1", [{**dict(x), "prompt": "Rewritten: " + x["prompt"]}
                                   for x in store.questions("c/lab1")])
    row = next(x for x in _report(store, chain=chain)["questions"] if x["id"] == q["id"])
    assert row["prompt"].startswith("Rewritten:")
    assert row["asked_as"] == q["prompt"]


def test_an_unedited_question_does_not_claim_to_have_changed(store):
    q = store.pick_questions("AAA", "c/lab1", 2)[0]
    row = next(x for x in _report(store, chain=_chain(
        ev.answer(q["id"], q["prompt"], "x")))["questions"] if x["id"] == q["id"])
    assert row["asked_as"] == ""


def test_a_lab_with_no_questions_reports_none_rather_than_breaking(store):
    assert _report(store, code="NEVER-VENDED")["questions"] == []


def test_the_answer_appears_in_the_transcript_a_marker_reads(store):
    """The narration is the account of the attempt, and the answers belong in it in sequence —
    beside the commands they ran, not in a separate list divorced from what they were doing."""
    q = store.pick_questions("AAA", "c/lab1", 2)[0]
    told = ACT.narrate(_chain(ev.answer(q["id"], q["prompt"], "10.0.0.2")))
    assert q["prompt"] in told and "10.0.0.2" in told


def test_a_blank_answer_says_so_in_the_transcript(store):
    q = store.pick_questions("AAA", "c/lab1", 2)[0]
    assert "left blank" in ACT.narrate(_chain(ev.answer(q["id"], q["prompt"], "")))


# ---- over the wire: the key never leaves the server ------------------------------ #
@pytest.fixture
def course(tmp_path, monkeypatch, tls_pair, trust_tls):
    """A real server, because the stripping happens in the handler and that is the thing to test.
    Reading the store directly would test a promise nobody makes."""
    monkeypatch.setenv("COURSE_ROOT", str(tmp_path))
    monkeypatch.setenv("ADMIN_ID", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    for mod in [m for m in list(sys.modules) if m.startswith("gini_teaching_center")]:
        sys.modules.pop(mod, None)
    from gini_teaching_center.store import Store as S
    S._instances.clear()
    from gini_teaching_center import accounts as A
    from gini_teaching_center import server
    server.ROOT = tmp_path
    server.MATERIALS = tmp_path / "materials"
    server.MATERIALS.mkdir(parents=True, exist_ok=True)
    server._ACCTS = A.Accounts(tmp_path)
    server._STORE = S(tmp_path)
    server._ACCTS.ensure_admin()
    cert, key = tls_pair
    ctx = server._tls_context(str(cert), str(key))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None, session="", method=None):
        method = method or ("POST" if body is not None else "GET")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        if session:
            req.add_header("Authorization", f"Bearer {session}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return json.loads(e.read() or b"null")

    tok = call("/auth/login", {"id": "boss", "password": "correct-horse"})["session"]
    call("/api/courses", {"id": "comp535", "title": "Networks"}, tok)
    call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "Multi-LAN",
                                  "brief": "Join two LANs with a router.",
                                  "vend_until": time.time() + HOUR, "session_minutes": 60,
                                  "questions": QS, "show_n": 2}, tok)
    call("/api/activities/release", {"course": "comp535", "lab": "lab1"}, tok)
    try:
        yield url, tok, call
    finally:
        httpd.shutdown()
        httpd.server_close()


def _armed(call):
    v = call("/api/activity?course=comp535&lab=lab1")
    code = v["code"].replace("-", "").replace(" ", "")
    return code, call(f"/api/activity?code={code}")


def test_arming_hands_over_the_questions(course):
    _url, _tok, call = course
    _code, armed = _armed(call)
    assert len(armed["questions"]) == 2
    assert all(q["prompt"] for q in armed["questions"])


def test_arming_never_hands_over_the_answers(course):
    """The one reply a student's own machine receives. Anything in it is on their disk, and
    "students will not look" has never been true of a marking key."""
    _url, _tok, call = course
    _code, armed = _armed(call)
    assert all("answer" not in q for q in armed["questions"])
    blob = json.dumps(armed)
    for q in QS:
        assert q["answer"] not in blob


def test_the_course_will_not_answer_a_question_it_set(course):
    """Ask GINI reads the course. If the question bank were searchable, the shortest path to full
    marks would be pasting the question into the assistant — and the assistant would oblige."""
    _url, _tok, call = course
    for q in QS:
        hits = call("/api/ask?course=comp535&q=" + urllib.parse.quote(q["prompt"]))
        blob = json.dumps(hits)
        assert q["answer"] not in blob, f"the key to {q['prompt']!r} came back from /api/ask"
        assert q["prompt"] not in blob, f"{q['prompt']!r} itself is searchable"


def test_the_teacher_can_read_back_what_they_wrote(course):
    """The editor has to be able to populate itself, or every save would be a blind overwrite."""
    _url, tok, call = course
    a = next(x for x in call("/api/activities?course=comp535", session=tok) if x["lab"] == "lab1")
    assert [q["prompt"] for q in a["questions"]] == [q["prompt"] for q in QS]
    assert a["show_n"] == 2


def test_asking_for_more_questions_than_were_written_is_refused_with_a_reason(course):
    """Caught at authoring time, where the teacher can fix it — not silently trimmed at vend time,
    which would leave them believing they had set three questions."""
    _url, tok, call = course
    r = call("/api/activities/save", {"course": "comp535", "lab": "lab2", "title": "x",
                                      "vend_until": time.time() + HOUR, "session_minutes": 60,
                                      "questions": QS[:1], "show_n": 3}, tok)
    assert r["ok"] is False and "3" in r["error"] and "1" in r["error"]


def test_the_whole_way_through_the_real_server(course):
    """Author → arm → answer → hand in → mark, over HTTP, with no store calls in the middle.

    The unit tests above call `report()` directly, so they would still pass if the handler forgot
    to pass the questions in — which is the exact wiring a marker's page depends on.
    """
    from gini.domain import proof as P
    from gini_teaching_center import activities as A

    _url, tok, call = course
    code, armed = _armed(call)
    q = armed["questions"][0]

    t0 = time.time()
    chain = P.Chain.start(A.normalize(code), assignment="comp535/lab1", gini_version="test", t=t0)
    chain.append("place", {"id": "1", "type": "Router", "name": "R1"}, t=t0 + 1)
    chain.append(*ev.answer(q["id"], q["prompt"], "10.0.0.2, set in the interface dialog"),
                 t=t0 + 100)
    chain.append("submit", {"artifact": P.artifact_summary(
        {"devices": [{"id": "1", "name": "R1", "type": "Router"}], "links": []})}, t=t0 + 300)
    sub = call("/api/activity/submit", {"code": code, "proof": P.build_proof(chain)})
    assert sub["ok"], sub

    rep = call(f"/api/receipt?receipt={sub['receipt']}", session=tok)
    row = next(x for x in rep["questions"] if x["id"] == q["id"])
    assert row["given"] == "10.0.0.2, set in the interface dialog"
    assert row["key"] == QS[0]["answer"] or row["key"] in (x["answer"] for x in QS)
    # The other question of the pair was never answered, and says so rather than vanishing.
    assert sum(1 for x in rep["questions"] if not x["answered"]) == 1


def test_an_unanswered_question_does_not_stop_them_handing_in(course):
    """Agreed explicitly: questions are part of the account of the attempt, not a gate on it. A
    student who runs out of time still submits the work they did."""
    from gini.domain import proof as P
    from gini_teaching_center import activities as A

    _url, _tok, call = course
    code, _reply = _armed(call)
    t0 = time.time()
    chain = P.Chain.start(A.normalize(code), assignment="comp535/lab1", gini_version="test", t=t0)
    chain.append("place", {"id": "1", "type": "Router", "name": "R1"}, t=t0 + 1)
    chain.append("submit", {"artifact": P.artifact_summary(
        {"devices": [{"id": "1", "name": "R1", "type": "Router"}], "links": []})}, t=t0 + 300)
    sub = call("/api/activity/submit", {"code": code, "proof": P.build_proof(chain)})
    assert sub["ok"] and sub["receipt"]
