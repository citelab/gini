"""The GINI AI endpoints: the bot's half and the teacher's half, and the wall between them.

Two audiences on one surface. The bot may push what was said and collect what to post; a teacher
may read and write. Neither can do the other's half — which is what keeps a leaked Discord token
from being a way into the course, and a signed-in teacher from being able to forge observations.
"""
from __future__ import annotations

import json
import os
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

KEY = "a-shared-bot-key"


@pytest.fixture
def tc(tmp_path, tls_pair, trust_tls, monkeypatch):
    from gini_teaching_center import accounts as A
    from gini_teaching_center import server
    from gini_teaching_center.store import Store

    monkeypatch.setenv("GINI_BOT_KEY", KEY)
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    monkeypatch.setenv("ADMIN_ID", "boss")
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

    def call(path, body=None, *, session="", bot="", method=""):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url + path, data=data, method=method or
                                     ("POST" if data is not None else "GET"))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if session:
            req.add_header("Authorization", f"Bearer {session}")
        if bot:
            req.add_header("X-GINI-Bot-Key", bot)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    tok = call("/auth/login", {"id": "boss", "password": "correct-horse"})[1]["session"]
    try:
        yield url, tok, call, server._STORE
    finally:
        httpd.shutdown()
        httpd.server_close()


def _said(text, who="aaa", at=None, mid="m1"):
    from gini.domain.similarity import terms
    return {"at": at or time.time(), "channel": "help", "who": who, "kind": "question",
            "text": text, "terms": terms(text), "thread": "", "channel_id": "c1",
            "message_id": mid}


# -- the wall ----------------------------------------------------------------- #

def test_the_bots_endpoints_do_not_exist_without_the_key(tc):
    _, tok, call, _ = tc
    assert call("/api/ai/observe", {"observations": []})[0] == 404
    assert call("/api/ai/observe", {"observations": []}, bot="wrong")[0] == 404


def test_a_signed_in_teacher_still_cannot_push_observations(tc):
    """A teacher reads and replies; only the bot says what was said. Otherwise the log stops being
    a record of Discord and becomes a record of whatever the console posted to it."""
    _, tok, call, _ = tc
    assert call("/api/ai/observe", {"observations": [_said("x")]}, session=tok)[0] == 404


def test_the_bot_cannot_read_the_teachers_views(tc):
    _, _, call, _ = tc
    assert call("/api/ai/summary", bot=KEY)[0] == 401


def test_the_views_need_a_signed_in_person(tc):
    _, _, call, _ = tc
    assert call("/api/ai/summary")[0] == 401
    assert call("/api/ai/reply", {"obs": 1, "body": "hi"})[0] == 401


# -- the loop ----------------------------------------------------------------- #

def test_what_the_bot_pushes_is_what_the_teacher_sees(tc):
    _, tok, call, _ = tc
    now = time.time()
    obs = [_said("Where do we send the receipt code?", who="aaa", at=now, mid="m1"),
           _said("sorry, where does the receipt code go", who="bbb", at=now - 5, mid="m2"),
           _said("how do I add a router to the canvas", who="ccc", at=now - 9, mid="m3")]
    assert call("/api/ai/observe", {"observations": obs}, bot=KEY)[1]["stored"] == 3

    st, out = call("/api/ai/summary?window=day", session=tok)
    assert st == 200
    assert out["counts"]["total"] == 3 and out["counts"]["people"] == 3
    groups = out["groups"]
    assert groups[0]["people"] == 2, "the two phrasings of one question must group"
    assert "receipt" in groups[0]["sample"].lower()


def test_pushing_the_same_message_again_does_not_invent_traffic(tc):
    """The bot backfills and listens at once, so it pushes duplicates as a matter of course."""
    _, tok, call, _ = tc
    o = _said("Where do we send the receipt code?", at=1789000000.5)
    call("/api/ai/observe", {"observations": [o]}, bot=KEY)
    call("/api/ai/observe", {"observations": [o]}, bot=KEY)
    assert call("/api/ai/summary?window=term", session=tok)[1]["counts"]["total"] == 1


def test_a_teacher_replies_and_the_bot_collects_it_addressed_to_the_message(tc):
    _, tok, call, _ = tc
    call("/api/ai/observe", {"observations": [_said("Where do we send the receipt code?")]},
         bot=KEY)
    obs = call("/api/ai/summary?window=day", session=tok)[1]["groups"][0]["obs"]

    st, r = call("/api/ai/reply", {"obs": obs, "body": "On MyCourses, under Assignment 3."},
                 session=tok)
    assert st == 200 and r["ok"]

    st, outbox = call("/api/ai/outbox", bot=KEY)
    assert st == 200 and len(outbox) == 1
    assert outbox[0]["channel_id"] == "c1" and outbox[0]["message_id"] == "m1"
    assert "On MyCourses" in outbox[0]["body"]
    assert "via GINI AI" in outbox[0]["body"], "a student must be able to tell the bot carried it"
    assert "boss" in outbox[0]["body"], "and who answered"


def test_a_sent_reply_is_not_collected_twice_and_marks_the_question_answered(tc):
    _, tok, call, _ = tc
    call("/api/ai/observe", {"observations": [_said("Where do we send the receipt code?")]},
         bot=KEY)
    obs = call("/api/ai/summary?window=day", session=tok)[1]["groups"][0]["obs"]
    call("/api/ai/reply", {"obs": obs, "body": "On MyCourses."}, session=tok)
    rid = call("/api/ai/outbox", bot=KEY)[1][0]["id"]

    call("/api/ai/sent", {"id": rid}, bot=KEY)
    assert call("/api/ai/outbox", bot=KEY)[1] == []
    assert call("/api/ai/summary?window=day", session=tok)[1]["groups"][0]["answered"] is True


def test_a_send_that_failed_says_why_in_the_thread_rather_than_disappearing(tc):
    _, tok, call, _ = tc
    call("/api/ai/observe", {"observations": [_said("Where do we send the receipt code?")]},
         bot=KEY)
    obs = call("/api/ai/summary?window=day", session=tok)[1]["groups"][0]["obs"]
    call("/api/ai/reply", {"obs": obs, "body": "On MyCourses."}, session=tok)
    rid = call("/api/ai/outbox", bot=KEY)[1][0]["id"]
    call("/api/ai/sent", {"id": rid, "error": "403 Forbidden: missing Send Messages"}, bot=KEY)

    thread = call(f"/api/ai/thread?obs={obs}", session=tok)[1]
    assert "Send Messages" in thread[0]["replies"][0]["error"]
    assert call("/api/ai/outbox", bot=KEY)[1] == [], "a failure must not be retried for ever"


def test_a_message_too_old_to_reply_to_is_refused_with_somewhere_else_to_go(tc):
    """Rather than posting a loose message into a busy channel where nobody can tell what it
    answers."""
    _, tok, call, store = tc
    call("/api/ai/observe", {"observations": [_said("Where do we send the receipt code?")]},
         bot=KEY)
    obs = call("/api/ai/summary?window=day", session=tok)[1]["groups"][0]["obs"]
    store.obs_refs_sweep(time.time() + 400 * 86400)          # the window has passed

    st, r = call("/api/ai/reply", {"obs": obs, "body": "too late"}, session=tok)
    assert st == 409
    assert "Answer it in Discord" in r["error"]


def test_an_empty_reply_is_refused_before_it_reaches_anyone(tc):
    _, tok, call, _ = tc
    call("/api/ai/observe", {"observations": [_said("Where do we send the receipt code?")]},
         bot=KEY)
    obs = call("/api/ai/summary?window=day", session=tok)[1]["groups"][0]["obs"]
    assert call("/api/ai/reply", {"obs": obs, "body": "   "}, session=tok)[0] == 400


def test_the_windows_a_teacher_actually_asks_for(tc):
    _, tok, call, _ = tc
    now = time.time()
    call("/api/ai/observe", {"observations": [
        _said("Where do we send the receipt code?", at=now, mid="m1"),
        _said("gbuilder core dumped on the lab machine", who="b", at=now - 20 * 86400, mid="m2"),
    ]}, bot=KEY)

    assert call("/api/ai/summary?window=day", session=tok)[1]["counts"]["total"] == 1
    assert call("/api/ai/summary?window=month", session=tok)[1]["counts"]["total"] == 2
    assert call("/api/ai/summary?window=nonsense", session=tok)[1]["days"] == 7


# -- the answer bank: the public door's voice ----------------------------------- #

def test_a_banked_answer_comes_back_with_the_push_that_asked_it(tc):
    """The whole of the public door's voice, and the bot still holds no judgement: it pushes a
    message and is handed the words to post, or nothing."""
    _, tok, call, _ = tc
    call("/api/ai/answers", {"question": "Where do we send the receipt code?",
                             "answer": "On MyCourses, under Assignment 3."}, session=tok)

    st, r = call("/api/ai/observe",
                 {"observations": [_said("my gbuilder — sorry, where do we send the receipt code")]},
                 bot=KEY)
    assert st == 200
    assert r["reply"] and "MyCourses" in r["reply"]
    assert "via GINI AI" not in r["reply"], "a banked answer is the course's own words"
    assert "boss" in r["reply"], "with a byline, so a student knows who settled it"


def test_nothing_comes_back_when_the_bank_does_not_cover_it(tc):
    """Silence is a valid answer. A hedge from a bot is noise on a busy server."""
    _, tok, call, _ = tc
    call("/api/ai/answers", {"question": "Where do we send the receipt code?",
                             "answer": "On MyCourses."}, session=tok)
    r = call("/api/ai/observe",
             {"observations": [_said("how do I add a router to the canvas")]}, bot=KEY)[1]
    assert r["reply"] is None


def test_six_people_piling_onto_one_outage_are_answered_once(tc):
    _, tok, call, _ = tc
    call("/api/ai/answers", {"question": "gbuilder core dumped on the lab machine",
                             "answer": "Install libxcb-cursor0."}, session=tok)
    said = 0
    for i in range(4):
        o = _said("gbuilder core dumped on the lab machine", who=f"p{i}",
                  at=time.time() + i, mid=f"m{i}")
        o["kind"] = "problem"
        said += bool(call("/api/ai/observe", {"observations": [o]}, bot=KEY)[1]["reply"])
    assert said == 1, "the answer is useful once and spam five times"


def test_a_backfill_of_old_history_never_answers_anybody(tc):
    """Reading a term of history must not reply to a question somebody asked in March."""
    _, tok, call, _ = tc
    call("/api/ai/answers", {"question": "Where do we send the receipt code?",
                             "answer": "On MyCourses."}, session=tok)
    r = call("/api/ai/observe",
             {"backfill": True,
              "observations": [_said("where do we send the receipt code")]}, bot=KEY)[1]
    assert r["reply"] is None

    many = [_said("where do we send the receipt code", who=f"x{i}", at=time.time() - i, mid=f"b{i}")
            for i in range(3)]
    assert call("/api/ai/observe", {"observations": many}, bot=KEY)[1]["reply"] is None, \
        "a batch is history; only a single live message can be answered"


def test_a_teacher_teaches_the_bank_a_phrasing_in_one_click(tc):
    _, tok, call, _ = tc
    aid = call("/api/ai/answers", {"question": "Where do we send the receipt code?",
                                   "answer": "On MyCourses."}, session=tok)[1]["id"]
    odd = _said("do the receipt codes go on mycourses or by email to you")
    assert call("/api/ai/observe", {"observations": [odd]}, bot=KEY)[1]["reply"] is None

    call("/api/ai/answers/attach", {"id": aid, "sample": odd["text"]}, session=tok)
    odd2 = dict(odd, at=time.time() + 9999, mid="m9")
    assert call("/api/ai/observe", {"observations": [odd2]}, bot=KEY)[1]["reply"] is not None


def test_a_disabled_answer_stops_being_posted_without_being_lost(tc):
    _, tok, call, _ = tc
    aid = call("/api/ai/answers", {"question": "Where do we send the receipt code?",
                                   "answer": "On MyCourses."}, session=tok)[1]["id"]
    call("/api/ai/answers", {"id": aid, "question": "Where do we send the receipt code?",
                             "answer": "On MyCourses.", "enabled": False}, session=tok)
    assert call("/api/ai/observe",
                {"observations": [_said("where do we send the receipt code", mid="mz")]},
                bot=KEY)[1]["reply"] is None
    assert len(call("/api/ai/answers", session=tok)[1]) == 1, "disabled, not deleted"


def test_the_bank_needs_a_signed_in_teacher(tc):
    _, _, call, _ = tc
    assert call("/api/ai/answers", {"question": "q", "answer": "a"})[0] == 401
    assert call("/api/ai/answers", {"question": "q", "answer": "a"}, bot=KEY)[0] == 401


def test_an_answer_with_nothing_in_it_is_refused(tc):
    _, tok, call, _ = tc
    assert call("/api/ai/answers", {"question": "q", "answer": "   "}, session=tok)[0] == 400
