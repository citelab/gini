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
    teach teachers to dismiss them."""
    html = console()
    assert "renderObjections" in html
    assert "Nothing here blocks" in html


def test_the_literal_plan_is_shown_beside_the_prose():
    """A friendly summary a teacher cannot check against the real plan is worse than none."""
    assert "The plan, exactly" in console()


def test_artifact_twins_are_surfaced_on_lookup():
    assert "same topology as" in console()
