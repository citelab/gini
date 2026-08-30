"""The console's JavaScript must actually parse.

Written after shipping a blank page. The Activities tab re-declared `esc`, which was already
defined a couple of hundred lines above; `const` twice at top level is a SyntaxError, and a
SyntaxError anywhere in a <script> block aborts the WHOLE block — so the sign-in gate never
rendered either and the console was blank. Every HTTP test still passed, because the server was
fine and the page was served with a 200.

That is the shape of the failure worth guarding: nothing on the Python side can see it, and status
codes cannot either. So parse the JavaScript, with a real parser when one is available.
"""
from __future__ import annotations

import collections
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")

PAGES = ("console.html", "getcode.html")


def scripts(name: str) -> str:
    html = (_TC / "gini_teaching_center" / "static" / name).read_text(encoding="utf-8")
    return "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))


@pytest.mark.parametrize("page", PAGES)
def test_the_page_has_script_to_check(page):
    assert scripts(page).strip(), f"{page} has no inline script — did the extraction regex rot?"


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_javascript_parses(page, tmp_path):
    """The real check. `node --check` is a full parse, so it catches every SyntaxError, not just
    the one that bit us."""
    f = tmp_path / "page.js"
    f.write_text(scripts(page), encoding="utf-8")
    r = subprocess.run([shutil.which("node"), "--check", str(f)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"{page} does not parse:\n{r.stderr}"


@pytest.mark.parametrize("page", PAGES)
def test_no_duplicate_top_level_declaration(page):
    """A parser-free version of the same guard, so this still protects the console on a machine
    with no node. Narrow by design: it catches the exact mistake that shipped, and a `const` at
    column zero declared twice is never intentional."""
    names = collections.Counter(
        re.findall(r"^(?:const|let)\s+([A-Za-z_$][\w$]*)", scripts(page), re.M))
    dupes = {n: c for n, c in names.items() if c > 1}
    assert not dupes, f"{page} re-declares {dupes} at top level — that blanks the whole page"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_the_guard_would_have_caught_the_bug_that_shipped(tmp_path):
    """A test whose failure mode is 'silently stops testing' is worse than none, so prove the
    check still detects the original defect."""
    f = tmp_path / "bad.js"
    f.write_text(scripts("console.html") + "\nconst esc = 1;\n", encoding="utf-8")
    r = subprocess.run([shutil.which("node"), "--check", str(f)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0
    assert "esc" in r.stderr


# --------------------------------------------------------------------------- #
# a report belongs to one course
# --------------------------------------------------------------------------- #
def test_every_path_that_drops_the_course_also_clears_the_report():
    """The bug: open a submission in one course, switch to another, and the report stayed on
    screen — under a list saying "Nothing submitted yet". The console showed a submission and its
    own absence at the same time, and the receipt box still held a code that does not exist in the
    course now open.

    Checked as "every place that changes COURSE clears it", not "pick() clears it", because there
    are three such places and the two rarer ones — a course you are no longer staffed to, and a
    site reset — are exactly the ones a future edit would forget.
    """
    js = scripts("console.html")
    # each line that assigns COURSE, other than the declaration itself
    lines = [l.strip() for l in js.splitlines()
             if re.search(r"\bCOURSE\s*=", l) and "let COURSE" not in l]
    assert lines, "no COURSE assignment found — did the console change shape?"
    missing = [l for l in lines if "clearReport()" not in l]
    # pick() clears on its own line rather than inline, so allow a call in the same function body
    missing = [l for l in missing if "COURSE = id" not in l]
    assert not missing, f"these change the course without clearing the report: {missing}"
    assert "function clearReport()" in js
    # and it must clear the receipt box too, not just the rendered report
    body = js.split("function clearReport()", 1)[1].split("}", 1)[0]
    for field in ("#r-out", "#r-code", "#r-sid"):
        assert field in body, f"clearReport leaves {field} behind"


# --------------------------------------------------------------------------- #
# taking a late submission in by hand
# --------------------------------------------------------------------------- #
def _console_html() -> str:
    return (_TC / "gini_teaching_center" / "static" / "console.html").read_text(encoding="utf-8")


def test_the_console_can_take_a_submission_in_by_hand():
    """The endpoint existed and only gBuilder could reach it, which put the recovery for a late
    submission in the marking tool rather than in the place a teacher administers the course."""
    html, js = _console_html(), scripts("console.html")
    assert 'id="ing-file"' in html, "no file picker in the Submissions card"
    assert "ingest()" in html, "the picker has no button wired to it"
    assert "'/api/submissions/accept'" in js, "the console does not post to the accept endpoint"


def test_the_hand_in_panel_lives_with_the_submissions_it_is_about():
    """Not in Settings, not under Site: a teacher looking for the work goes to Submissions, and
    the panel is only reachable at all if it is in that section."""
    html = _console_html()
    subs = html.split('<h2 style="font-size:21px">Submissions</h2>', 1)
    assert len(subs) == 2, "the Submissions card changed shape"
    card = subs[1].split("</section>", 1)[0]
    assert 'id="ing-file"' in card, "the hand-in panel is outside the Submissions section"


def test_the_ingest_panel_is_cleared_with_the_rest_on_a_course_switch():
    """Same bug as the report: its outcome names a receipt in the course that just closed."""
    js = scripts("console.html")
    body = js.split("function clearReport()", 1)[1].split("\n}", 1)[0]
    for field in ("#ing-out", "#ing-file"):
        assert field in body, f"clearReport leaves {field} behind"


def test_a_late_submission_says_so_in_both_places():
    """A teacher scanning the list must see it without opening each one, and the report must name
    the member of staff who waived the deadline — an override nobody can see is not a record."""
    js = scripts("console.html")
    assert js.count("r.late") >= 2, "LATE is shown in fewer than both the list and the report"
    assert "r.accepted_by" in js, "the report does not say who took the submission in"
