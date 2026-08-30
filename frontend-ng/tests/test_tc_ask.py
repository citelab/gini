"""Asking the course what it says: the plumbing between Ask GINI and the Teaching Center.

Deliberately about the CONTRACT, not the ranking. What callers depend on — the shape of a hit, the
course boundary, and the promise that a draft never leaves the server — has to survive whatever
replaces the matching later. The ranking itself is a placeholder and says so.
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

from gini.services import tc_ask, tc_submit                     # noqa: E402
from gini_teaching_center import search                          # noqa: E402

HOUR = 3600.0


# -- the ranking, with no database and no HTTP ---------------------------------- #
def _acts():
    return [
        {"id": "c/lab1", "lab": "lab1", "title": "Multi-LAN routing",
         "brief": "Join two LANs with a router.", "status": "released"},
        {"id": "c/lab9", "lab": "lab9", "title": "Final exam topology",
         "brief": "routing router LAN subnet", "status": "draft"},
        {"id": "c/lab3", "lab": "lab3", "title": "Switching",
         "brief": "A switch learns MAC addresses.", "status": "released"},
    ]


def _mats():
    return [{"id": "m1", "title": "Routing handout", "kind": "file", "filename": "routing.pdf"},
            {"id": "m2", "title": "Kurose chapter 4", "kind": "link", "url": "https://x/ch4"}]


def test_a_draft_activity_never_appears():
    """THE rule. An unreleased activity is the teacher's private working copy, and its brief
    describes an assignment nobody has been set yet. Asked with words taken straight OUT of the
    draft, so a leak could not hide behind a weak match."""
    hits = search.rank("routing router LAN subnet", _acts(), _mats())
    assert hits                                        # the released ones do match
    assert not any(h["id"] == "c/lab9" for h in hits)
    assert not any("exam" in (h.get("title", "") + h.get("brief", "")).lower() for h in hits)


def test_the_title_outweighs_the_body():
    """A question matching an activity's title is more likely to be about it than one matching a
    word buried in a brief."""
    hits = search.rank("switching", _acts(), _mats())
    assert hits[0]["title"] == "Switching"


def test_word_endings_do_not_decide_whether_a_student_gets_an_answer():
    """'route' vs 'routing' is a difference no student knows they made."""
    titles = {h["title"] for h in search.rank("how do I route between two LANs?", _acts(), _mats())}
    assert "Multi-LAN routing" in titles and "Routing handout" in titles


def test_a_question_of_nothing_but_stop_words_matches_nothing():
    assert search.rank("how do I do the thing", _acts(), _mats()) == []


def test_ties_break_the_same_way_every_time():
    """A ranking that reshuffles on equal scores makes a tutor look like it is guessing."""
    a, b = search.rank("routing", _acts(), _mats()), search.rank("routing", _acts(), _mats())
    assert [h["id"] for h in a] == [h["id"] for h in b]


# -- the client, with nothing running ------------------------------------------- #
def test_every_failure_is_an_empty_answer_not_an_exception():
    """The tutor is useful offline. Losing the course server must make its answers thinner, never
    stop it answering."""
    for args, reason in (
            (("", "c", "q"), "no_server"),
            (("https://x", "", "q"), "no_course"),
            (("http://x", "c", "q"), "insecure"),
            (("https://127.0.0.1:9", "c", "q"), "unreachable")):
        a = tc_ask.ask(*args)
        assert not a and a.reason == reason, args


def test_an_empty_question_asks_nothing_at_all():
    a = tc_ask.ask("https://127.0.0.1:9", "c", "   ")
    assert not a and not a.reason              # no server call attempted, and nothing wrong


def test_as_context_names_a_material_but_never_quotes_it():
    """The server stores a filename, not the text inside the PDF. Summarising one nobody read is
    exactly the confident wrongness a tutor must not produce."""
    a = tc_ask.Answer(course="c", hits=[
        {"kind": "activity", "title": "Multi-LAN routing", "brief": "Join two LANs."},
        {"kind": "material", "title": "Routing handout", "url": "/m/m1"}])
    out = tc_ask.as_context(a)
    assert "Multi-LAN routing" in out and "Join two LANs." in out
    assert "Routing handout" in out and "/m/m1" in out
    assert "pdf" not in out.lower()


def test_as_context_of_nothing_is_nothing():
    assert tc_ask.as_context(tc_ask.Answer(course="c")) == ""


# -- against the real server ----------------------------------------------------- #
@pytest.fixture
def course(tmp_path, monkeypatch, tls_pair, trust_tls):
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

    def post(path, body, session=""):
        req = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        if session:
            req.add_header("Authorization", f"Bearer {session}")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"null")

    tok = post("/auth/login", {"id": "boss", "password": "correct-horse"})["session"]
    post("/api/courses", {"id": "comp535", "title": "Networks"}, tok)
    post("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "Multi-LAN routing",
                                  "brief": "Join two LANs with a router.",
                                  "vend_until": time.time() + HOUR, "session_minutes": 60}, tok)
    post("/api/activities/release", {"course": "comp535", "lab": "lab1"}, tok)
    post("/api/activities/save", {"course": "comp535", "lab": "lab9", "title": "Final exam",
                                  "brief": "routing router LAN — do not release yet",
                                  "vend_until": time.time() + HOUR, "session_minutes": 60}, tok)
    try:
        yield _Course(url, tok, post)
    finally:
        httpd.shutdown()
        httpd.server_close()


class _Course(str):
    """The server URL, with the staff session and a poster hung off it.

    A `str` subclass so every existing test that passes `course` straight to `tc_ask.ask` keeps
    working unchanged — the fixture grew a way to add material without the tests that do not need
    one having to care.
    """
    def __new__(cls, url, token, post):
        self = super().__new__(cls, url)
        self.token, self._post = token, post
        return self

    def post(self, path, body):
        return self._post(path, body, self.token)


def test_a_student_asks_without_any_account(course):
    """Students never sign in — a code is a scope, not an identity — so this endpoint takes no
    session, like the /m/<id> materials it points at."""
    a = tc_ask.ask(course, "comp535", "how do I route between two LANs?")
    assert a and a.course == "comp535"
    assert any(h["kind"] == "activity" and "Multi-LAN" in h["title"] for h in a.hits)


def test_the_draft_stays_on_the_server(course):
    a = tc_ask.ask(course, "comp535", "routing router LAN")
    assert a                                            # the released one answers
    assert not any("exam" in h.get("title", "").lower() for h in a.hits)


def test_a_course_the_server_does_not_have_is_named_plainly(course):
    """The fix is in the student's own Settings, so saying 'no results' would send them looking
    everywhere except the one place that is wrong."""
    a = tc_ask.ask(course, "cs4480-fall26", "routing")
    assert not a and a.reason == "no_such_course"
    assert "cs4480-fall26" in a.error and "Settings" in a.error


def test_one_course_cannot_answer_for_another(course):
    """The Course setting is the whole scope boundary."""
    a = tc_ask.ask(course, "comp535", "routing")
    assert all(h["id"].startswith("comp535/") or h["kind"] == "material" for h in a.hits)


def test_a_very_long_question_is_truncated_not_refused(course):
    a = tc_ask.ask(course, "comp535", "routing " * 500)
    assert a.reason in ("", None) or a.hits is not None      # answered, not rejected


# ---- the terminal check ------------------------------------------------------ #
# `_course_context` is silent on purpose — working offline is normal, and a warning on every
# question would be worse than the problem. That silence is also why "is GINI actually reading my
# course?" had no answer from inside the app, which is what the CLI is for.
def test_the_check_reads_gbuilders_own_settings(course, tmp_path, monkeypatch, capsys):
    """The point of not taking a URL. A check against hand-typed values proves the server works;
    it does not prove the app is pointed at it, which is the thing that is actually wrong."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"tc_url": course, "tc_course": "comp535"}), encoding="utf-8")
    assert tc_ask._cli(["routing"]) == 0
    out = capsys.readouterr().out
    assert course in out and "comp535" in out


def test_the_check_shows_the_context_verbatim(course, tmp_path, monkeypatch, capsys):
    """What reaches the model is the only interesting answer. A hit count would let a wrong
    context — an empty one, one from the wrong course — look like success."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"tc_url": course, "tc_course": "comp535"}), encoding="utf-8")
    tc_ask._cli(["routing"])
    out = capsys.readouterr().out
    for line in tc_ask.as_context(tc_ask.ask(course, "comp535", "routing")).splitlines():
        assert line in out


def test_the_check_prints_only_json_when_asked(course, tmp_path, monkeypatch, capsys):
    """It has to pipe. A header on stdout would make every caller strip it first, and one of
    them would forget — which is exactly what happened the first time this was used."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"tc_url": course, "tc_course": "comp535"}), encoding="utf-8")
    tc_ask._cli(["--json", "routing"])
    assert json.loads(capsys.readouterr().out)["course"] == "comp535"


def test_the_check_names_the_fix_when_the_course_is_wrong(course, tmp_path, monkeypatch, capsys):
    """The failure that was actually in the field: the server up, TLS fine, and a course name in
    Settings that the server has never heard of. 'It did not work' would have sent someone to the
    network for an afternoon."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"tc_url": course, "tc_course": "cs310_typo"}), encoding="utf-8")
    assert tc_ask._cli(["routing"]) == 1
    out = capsys.readouterr().out
    assert "no_such_course" in out and "Settings" in out


def test_the_check_distinguishes_an_empty_course_from_a_broken_link(course, tmp_path,
                                                                   monkeypatch, capsys):
    """Reached-and-matched-nothing is not the same as could-not-reach, and the difference is the
    whole diagnosis. Nothing indexed sends you to the console; unreachable sends you to the VPN."""
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"tc_url": course, "tc_course": "comp535"}), encoding="utf-8")
    assert tc_ask._cli(["supercalifragilistic"]) == 1
    out = capsys.readouterr().out
    assert "matched nothing" in out
    assert "draft" in out and "PDF" in out          # the reasons, not just the count


# ---- materials, all the way through ------------------------------------------ #
# Everything above tests ACTIVITIES. The materials path — upload, store, rank, hit — had no test
# of its own, and a materials path that silently returned nothing would look exactly like a course
# with nothing in it. That ambiguity is not academic: it is what a live server looked like while
# working out whether a course was empty or the search was broken.
def test_an_uploaded_file_can_be_found_by_name(course):
    import base64
    r = course.post("/api/materials",
                    {"course": "comp535", "title": "Subnetting handout",
                     "filename": "subnets.pdf",
                     "data": base64.b64encode(b"%PDF-1.4 not really a pdf").decode()})
    assert r["ok"], r
    a = tc_ask.ask(course, "comp535", "subnetting")
    hit = next((h for h in a.hits if h["kind"] == "material"), None)
    assert hit is not None, f"the upload is not searchable: {a.hits}"
    assert hit["title"] == "Subnetting handout"
    assert hit["url"] == f"/m/{r['id']}"


def test_a_link_is_found_the_same_way_as_a_file(course):
    r = course.post("/api/materials", {"course": "comp535", "title": "RFC 791 — IP",
                                       "url": "https://www.rfc-editor.org/rfc/rfc791"})
    assert r["ok"]
    a = tc_ask.ask(course, "comp535", "rfc")
    assert any(h["kind"] == "material" and "RFC" in h["title"] for h in a.hits)


def test_a_material_is_pointed_at_never_quoted(course):
    """The honest limit of the whole feature, pinned so a later change has to be deliberate. No
    text is extracted from an upload, so a question whose words live only INSIDE the file cannot
    match — and the context names the material rather than pretending to summarise it."""
    import base64
    body = b"the word supercalifragilistic appears only inside this file"
    course.post("/api/materials",
                {"course": "comp535", "title": "Week 3 notes", "filename": "w3.txt",
                 "data": base64.b64encode(body).decode()})
    assert not tc_ask.ask(course, "comp535", "supercalifragilistic")

    a = tc_ask.ask(course, "comp535", "week")
    assert any(h["title"] == "Week 3 notes" for h in a.hits)     # findable by its NAME
    context = tc_ask.as_context(a)
    assert "Week 3 notes" in context
    assert "supercalifragilistic" not in context


def test_a_material_belongs_to_its_own_course(course):
    """Materials go through the same scope gate activities do — the Course in Settings is the
    entire boundary, and it must not have a hole on the materials side."""
    course.post("/api/courses", {"id": "comp310", "title": "Operating Systems"})
    course.post("/api/materials", {"course": "comp310", "title": "Paging slides",
                                   "url": "https://example.edu/paging"})
    assert not any("Paging" in h.get("title", "")
                   for h in tc_ask.ask(course, "comp535", "paging slides").hits)
    assert any("Paging" in h.get("title", "")
               for h in tc_ask.ask(course, "comp310", "paging slides").hits)


def test_a_course_with_nothing_in_it_answers_ok_with_no_hits(course):
    """Not an error, and it must stay that way: an empty course is a course a teacher has not
    filled in yet. The CLI leans on this to tell 'nothing posted' apart from 'cannot reach'."""
    course.post("/api/courses", {"id": "comp999", "title": "Brand new"})
    a = tc_ask.ask(course, "comp999", "anything at all")
    assert a.reason == "" and a.error == "" and a.hits == []


# ---- the library reaches the model as words, not as a pointer ------------------- #
# The "never quoted" rule had an exact reason: the server stored a filename, not the text inside
# it, so any summary would have been invented. That reason does not hold for a work the server has
# actually indexed, and quoting real text is the opposite of the failure it guarded against.
LIB = {"kind": "reference", "title": "7.6 Code: Sleep and wakeup",
       "book": "xv6: a simple, Unix-like teaching operating system",
       "url": "https://xv6-guide.github.io/xv6-riscv-book/Ch7.S6.html",
       "passage": "The basic idea is to have sleep mark the current process as SLEEPING.",
       "attribution": "Copyright (c) 2006-2024 Russ Cox, Frans Kaashoek, Robert Morris."}


def test_a_library_passage_reaches_the_model_verbatim():
    out = tc_ask.as_context(tc_ask.Answer(course="c", hits=[LIB]))
    assert LIB["passage"] in out
    assert "VERBATIM" in out, "the model is not told these are somebody's actual words"


def test_a_passage_carries_where_to_read_it():
    out = tc_ask.as_context(tc_ask.Answer(course="c", hits=[LIB]))
    assert LIB["url"] in out and "7.6" in out


def test_the_notice_sits_with_the_passage_it_belongs_to():
    """Not gathered at the end: a model given four passages and one trailing notice attaches it to
    the wrong one, or to none. Carrying it is a condition of use, so it travels per block."""
    two = tc_ask.as_context(tc_ask.Answer(course="c", hits=[LIB, dict(LIB, title="8.1 Overview")]))
    assert two.count(LIB["attribution"]) == 2


def test_a_material_is_still_only_named():
    """The old rule holds where its reason still does — the server has a filename and nothing
    more, so there is nothing honest to quote."""
    out = tc_ask.as_context(tc_ask.Answer(course="c", hits=[
        {"kind": "material", "title": "Lab 1 handout", "url": "/m/abc"}]))
    assert "Lab 1 handout" in out and "VERBATIM" not in out


def test_activities_and_books_are_kept_apart():
    """A lab brief is what the student was set; a book passage is somebody else's prose. Running
    them together in one list invites the model to quote the teacher and paraphrase the book."""
    out = tc_ask.as_context(tc_ask.Answer(course="c", hits=[
        {"kind": "activity", "title": "Lab 3", "brief": "Add a scheduler."}, LIB]))
    assert out.index("Lab 3") < out.index("VERBATIM")
    assert "From this course's material:" in out and "From the books" in out


# ---- a certificate is not a network ------------------------------------------- #
def test_an_untrusted_certificate_is_not_reported_as_unreachable(tmp_path, monkeypatch):
    """It said "unreachable — VPN, or the wrong port" about a server that was up and answering,
    which sent someone to check a VPN and a port number over a self-signed certificate. Retrying
    cannot fix it and the network is fine; `tc_submit` has told the two apart since it was
    written, and this asked the same server over the same TLS without doing so."""
    import ssl
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    monkeypatch.setattr(tc_ask.urllib.request, "urlopen", boom)
    a = tc_ask.ask("https://127.0.0.1:8443", "comp310", "anything")
    assert a.reason == "untrusted"


def test_a_real_outage_is_still_an_outage(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(tc_ask.urllib.request, "urlopen", boom)
    assert tc_ask.ask("https://127.0.0.1:9", "comp310", "anything").reason == "unreachable"


def test_the_check_names_the_fix_for_an_untrusted_certificate(tmp_path, monkeypatch, capsys):
    """The advice is the whole point of separating them: one says check your network, the other
    says trust this certificate, and only one of those is ever going to work."""
    import ssl
    import urllib.error
    monkeypatch.setenv("GINI_HOME_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"tc_url": "https://127.0.0.1:8443", "tc_course": "comp310"}), encoding="utf-8")

    def boom(*a, **k):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    monkeypatch.setattr(tc_ask.urllib.request, "urlopen", boom)
    assert tc_ask._cli(["routing"]) == 1
    out = capsys.readouterr().out
    assert "SSL_CERT_FILE" in out and "IS up" in out
    assert "VPN" not in out.split("untrusted")[1].split("SSL_CERT_FILE")[0]
