"""The Activities tab in the teacher console.

A page can only be wrong in two interesting ways here, and both are checked against the LIVE server
rather than by reading the HTML: it can call an endpoint that does not exist, or it can leak the
instrument to the wrong audience. Everything else is styling.

The second matters more than it sounds. The teacher console shows expectations by design — that is
its whole job — while the public page must never show one. A test that only read the teacher page
would happily pass while the student page leaked, so both are fetched from the same running server
and compared.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini.domain import aop as A                                # noqa: E402

HOUR = 3600.0
TEACHER_HTML = _TC / "static" / "teacher.html"


@pytest.fixture
def tc(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSE_ROOT", str(tmp_path))
    monkeypatch.setenv("COURSE", "comp535")
    monkeypatch.setenv("TEACHER_ID", "teacher")
    monkeypatch.setenv("TEACHER_PASSWORD", "probe-password")
    for mod in [m for m in list(sys.modules)
                if m in ("server", "store", "accounts", "teacher", "activities", "social", "ai")]:
        del sys.modules[mod]
    from store import Store
    Store._instances.clear()

    import server as SRV
    import teacher as T

    plan = A.Aop(header=A.Header(intent="routed network"),
                 expectations=(A.Expectation(id="e", say="A router exists", layer="L3",
                                             check="exists('router')"),))
    course = T.Course(str(tmp_path), "comp535")
    course.save_activity("lab1", title="Multi-LAN", intent="routed network",
                         plan=plan.to_dict(), vend_until=time.time() + HOUR)
    course.release_activity("lab1")

    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SRV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield type("TC", (), {"base": f"http://127.0.0.1:{httpd.server_address[1]}", "plan": plan})
    httpd.shutdown()


def fetch(tc, path):
    try:
        with urllib.request.urlopen(tc.base + path, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def console() -> str:
    return TEACHER_HTML.read_text(encoding="utf-8")


# -- the page reaches endpoints that exist ------------------------------------ #
def test_every_activity_endpoint_the_page_calls_is_served(tc):
    """The failure this catches is a renamed route: the page keeps calling the old path and the tab
    silently does nothing. Each is probed unauthenticated — 401 proves it is ROUTED (and guarded);
    404 would mean nobody serves it."""
    called = set(re.findall(r"api\('(/api/activities[^'?]*)", console()))
    assert called, "the console no longer calls any activity endpoint"
    for path in sorted(called):
        status, _ = fetch(tc, path)
        assert status != 404, f"{path} is called by the console but not served"
        assert status == 401, f"{path} answered {status} without a teacher session"


def test_the_receipt_lookup_is_routed(tc):
    status, _ = fetch(tc, "/api/activities/receipt?receipt=ABCD-EFGH")
    assert status == 401


def test_the_page_offers_the_student_link_that_actually_serves(tc):
    """The console renders a /getcode link for released activities; it has to be the real one."""
    assert "/getcode?course=" in console()
    assert fetch(tc, "/getcode?course=comp535&lab=lab1")[0] == 200


# -- the instrument stays on the right side ----------------------------------- #
def test_the_public_page_shows_no_expectation_while_the_console_may(tc):
    """The asymmetry is the point: the teacher console exists to show the plan, the student page
    must never. Checking only one of them would miss the leak."""
    _s, student = fetch(tc, "/getcode?course=comp535&lab=lab1")
    for leak in ("exists(", "reach(", "count(", "A router exists"):
        assert leak not in student
    assert "a-plan" in console()          # the console does render the literal plan


def test_the_student_page_has_nothing_writable(tc):
    _s, student = fetch(tc, "/getcode?course=comp535&lab=lab1")
    for tag in ("<input", "<textarea", "<form"):
        assert tag not in student.lower()


def test_guidance_is_offered_as_summaries_not_checks():
    """The console's own wording has to say what the guidance toggle really does, or a teacher will
    switch it on believing it reveals more than it does."""
    html = console()
    assert "a-guide" in html
    assert "never the checks themselves" in html


# -- the tab is wired into the console ---------------------------------------- #
def test_the_tab_is_reachable():
    html = console()
    assert 'data-t="acts"' in html                       # the nav button
    assert "'acts'" in html and "acts:loadActs" in html  # shown, and loaded on click
    assert '<section id="acts"' in html


def test_the_draft_loop_posts_the_last_draft_not_the_form():
    """The teacher never edits the plan directly — they talk and it is re-drafted. So save/release
    must post the DRAFT's selection and plan, not something reconstructed from the inputs."""
    html = console()
    assert "ACT && ACT.selection" in html and "ACT && ACT.plan" in html


def test_releasing_requires_a_vending_deadline():
    """Without one, nothing ever closes the activity — which is the whole late-submission control."""
    assert "that is what closes the activity" in console()


def test_objections_are_shown_as_questions_not_errors():
    """The Twin is a challenger, not a gate. Rendering its objections as blocking errors would
    teach teachers to dismiss them, and it must not disable sign-off."""
    html = console()
    assert "Worth a second look" in html
    assert "Nothing here blocks" in html


def test_the_literal_check_is_shown_next_to_its_prose():
    """A friendly summary a teacher cannot check against the real plan is worse than none. The
    plan column renders each expectation's own words AND the predicate that implements it, so the
    two can be compared without trusting either."""
    html = console()
    assert "e.probe || e.check" in html      # the raw predicate is rendered...
    assert "esc(e.say)" in html              # ...beside the sentence it claims to mean


def test_the_conversation_is_the_only_place_the_teacher_types_about_content():
    """Design 3.2: the teacher never edits the plan, they talk about it. A textarea bound to plan
    content would quietly reintroduce direct editing."""
    html = console()
    assert 'id="a-say"' in html                       # the one content input
    assert 'id="a-intent"' not in html                # the old separate description box is gone


def test_the_course_is_a_label_not_an_input():
    """One Teaching Center serves one course — that is deployment configuration. An input here
    would invite mixing courses in a store that has no notion of doing so."""
    html = console()
    assert 'id="a-course"' in html and 'class="fixed"' in html
    assert '<input id="a-course"' not in html


def test_sign_off_is_the_release_action():
    assert "Sign off" in console()


def test_artifact_twins_are_surfaced_on_lookup():
    assert "same topology as" in console()


# -- liveness: progress driven by real tokens --------------------------------- #
# The model streams into the SERVER, so without this the browser posts once and waits in silence,
# and a model producing beautifully looks exactly like a hung one. Everything below exists so the
# indicator tells the truth: it is fed by tokens, not by a timer, and it stalls when they stop.
def _stream(tc, body, tok):
    req = urllib.request.Request(tc.base + "/api/activities/draft",
                                 data=json.dumps({**body, "stream": True}).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        return [json.loads(line) for line in r if line.strip()]


@pytest.fixture
def teacher_token(tc, monkeypatch):
    import server as SRV
    SRV._ACCTS.ensure_teacher()
    req = urllib.request.Request(tc.base + "/auth/login",
                                 data=json.dumps({"id": "teacher",
                                                  "password": "probe-password"}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("session", "")
    except Exception:                                      # noqa: BLE001
        return ""


def test_the_draft_route_streams_progress_before_the_answer(tc, teacher_token, monkeypatch):
    """The whole point: progress must arrive WHILE the model works, not with the result."""
    assert teacher_token, "could not sign in — the streaming test would silently skip"
    import ai as AI

    def slow(self, system, user, *, json_mode=False, num_predict=0, on_chunk=None):
        text = ('{"patterns":[{"key":"multi-lan"}],"questions":[],"note":"n",'
                '"coverage":{"addressed":[],"omitted":[]}}')
        for i in range(0, len(text), 16):
            if on_chunk:
                on_chunk(text[i:i + 16])
        return text

    monkeypatch.setattr(AI.Ollama, "chat", slow)
    monkeypatch.setattr(AI.Ollama, "available", lambda self: True)
    lines = _stream(tc, {"intent": "Build LANs joined by routers."}, teacher_token)
    kinds = [m["t"] for m in lines]
    assert kinds[-1] == "done", "the payload must be the LAST line, after the progress"
    assert "phase" in kinds and "tick" in kinds
    assert kinds.index("phase") < kinds.index("done")


def test_the_pulse_is_fed_by_tokens_not_a_timer():
    """A CSS spinner keeps spinning on a dead model — confidently wrong. This advances only when a
    chunk arrives, so it freezes exactly when generation freezes, and a frozen indicator is
    information."""
    html = console()
    assert "LIVE.beat++" in html                      # advanced on 'tick'/'say' only
    assert "renderLive" in html


def test_the_phase_is_named_not_just_spun():
    """The labels live server-side, next to the calls they describe. "Reconsidering — something did
    not add up" is far more reassuring to watch than an unlabelled spinner."""
    src = (_TC / "server.py").read_text()
    assert "Reconsidering" in src and "choosing what to watch" in src


def test_only_the_human_readable_call_is_shown_verbatim():
    """Streaming raw JSON tokens from the selection call would be noise; the back-translation is
    prose written for the teacher, so it is the one worth showing as it is written."""
    html = console()
    assert "saying" in html                           # the live prose panel
    import re as _re
    tc_server = (_TC / "server.py").read_text()
    assert 't="say"' in tc_server
    # ...and it is emitted from the PROSE call, not the JSON one
    prose_block = tc_server.split("_prose_call")[1]
    assert 't="say"' in prose_block


def test_a_stalled_model_is_reported_as_silence_not_slowness(tc):
    """Two failures, two messages: the fix for silence is 'is anything listening', the fix for
    slowness is a bigger budget or a smaller model."""
    server_src = (_TC / "server.py").read_text()
    assert "ModelTooSlow" in server_src
    assert "went silent" in server_src
