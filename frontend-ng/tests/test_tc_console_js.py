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

_TC = Path(__file__).resolve().parents[2] / "teaching-center"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")

PAGES = ("teacher.html", "getcode.html")


def scripts(name: str) -> str:
    html = (_TC / "static" / name).read_text(encoding="utf-8")
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
    f.write_text(scripts("teacher.html") + "\nconst esc = 1;\n", encoding="utf-8")
    r = subprocess.run([shutil.which("node"), "--check", str(f)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0
    assert "esc" in r.stderr
