"""A lab's minimum GINI version: the course refuses a code to a gBuilder too old for the lab.

Written because students kept doing a lab on whatever gBuilder they installed in September, and
found it did not work -- a fix the lab needed had shipped since. The teacher now sets the oldest
GINI a lab can be done with, and a code entered in an older gBuilder does not start.

The design turns on one fact: the gBuilders this is FOR are already installed, and cannot be
changed. They send no version with a code. But they already show a refusal's `error` to the student
and do not arm, so the server refuses a code from a gBuilder that does not say its version exactly
as it refuses one that is too old, and writes the sentence so that it stands alone. A current
gBuilder sends its version and, on that refusal, shows a pop-up with the command for its own
install. Nothing is armed either way.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini.domain.versions import at_least, version_tuple
from gini_teaching_center import activities as ACT
from gini_teaching_center.store import Store
from test_tc_questions import HOUR, QS, _rc, course

# --------------------------------------------------------------------------- #
# comparing versions
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("v,want", [
    ("6.22.11", (6, 22, 11)), ("6.22", (6, 22, 0)), ("v7", (7, 0, 0)),
    ("6.22.12.dev3+gabc1234", (6, 22, 12)),        # a checkout past 6.22.11 counts as 6.22.12
    ("0.0.0+unknown", (0, 0, 0)), ("", None), ("unknown", None)])
def test_a_version_is_read_by_its_release_numbers(v, want):
    assert version_tuple(v) == want


@pytest.mark.parametrize("have,need,ok", [
    ("6.22.12", "6.22.12", True), ("6.22.13", "6.22.12", True), ("6.23.0", "6.22.12", True),
    ("6.22.11", "6.22.12", False), ("6.9.0", "6.22.12", False),      # numbers, not strings
    ("", "6.22.12", False), ("0.0.0+unknown", "6.22.12", False),
    ("6.22.11", "", True), ("", "", True)])
def test_at_least(have, need, ok):
    assert at_least(have, need) is ok


# --------------------------------------------------------------------------- #
# the rule, on its own
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("typed,stored", [
    ("6.22.12", "6.22.12"), (" v6.22.12 ", "6.22.12"), ("6.23", "6.23"), ("", ""), (None, ""),
    ("six", None), ("6.22.12-beta", None), ("6.22.12.1", None)])
def test_what_a_teacher_types_is_kept_or_refused(typed, stored):
    assert ACT.min_gini_value(typed) == stored


def test_a_lab_without_a_minimum_takes_any_gbuilder_including_one_that_says_nothing():
    assert ACT.gini_too_old({"min_gini": ""}, "") is None
    assert ACT.gini_too_old({}, "") is None


def test_a_gbuilder_that_does_not_say_its_version_is_refused_like_an_old_one():
    """Every gBuilder from before this check: the ones it exists for. The refusal's sentence is
    all such a gBuilder will show, so it has to carry the version needed and the command."""
    r = ACT.gini_too_old({"min_gini": "6.22.12"}, "")
    assert r["ok"] is False and r["reason"] == ACT.UPDATE_GINI
    assert "GINI 6.22.12 or newer" in r["error"]
    assert "pipx upgrade gini-toolkit" in r["error"] and "enter the code again" in r["error"]


def test_an_old_gbuilder_is_told_what_it_is_and_a_new_enough_one_is_not_refused():
    r = ACT.gini_too_old({"min_gini": "6.22.12"}, "6.22.11")
    assert "this gBuilder is 6.22.11" in r["error"] and r["your_gini"] == "6.22.11"
    assert ACT.gini_too_old({"min_gini": "6.22.12"}, "6.22.12") is None


# --------------------------------------------------------------------------- #
# over the wire
# --------------------------------------------------------------------------- #

def _set_min(call, tok, value):
    return call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "Multi-LAN",
                                         "vend_until": time.time() + HOUR, "min_gini": value,
                                         "questions": QS, "show_n": 2}, tok)


def _code(call):
    v = call("/api/activity?course=comp535&lab=lab1&rc=" + _rc())
    return v, v["code"].replace("-", "").replace(" ", "")


def test_the_teacher_sets_it_and_reads_it_back(course):
    _url, tok, call = course
    assert _set_min(call, tok, "6.22.12")["ok"]
    a = next(x for x in call("/api/activities?course=comp535", session=tok) if x["lab"] == "lab1")
    assert a["min_gini"] == "6.22.12"


def test_a_version_the_server_cannot_read_is_refused_with_a_reason(course):
    _url, tok, call = course
    r = _set_min(call, tok, "the latest")
    assert not r["ok"] and "6.22.12" in r["error"]


def test_saving_from_an_older_console_leaves_it_alone_and_blank_clears_it(course):
    _url, tok, call = course
    _set_min(call, tok, "6.22.12")
    call("/api/activities/save", {"course": "comp535", "lab": "lab1", "title": "Renamed",
                                  "vend_until": time.time() + HOUR, "questions": QS,
                                  "show_n": 2}, tok)                      # no min_gini at all
    rows = call("/api/activities?course=comp535", session=tok)
    assert next(x for x in rows if x["lab"] == "lab1")["min_gini"] == "6.22.12"
    _set_min(call, tok, "")
    rows = call("/api/activities?course=comp535", session=tok)
    assert next(x for x in rows if x["lab"] == "lab1")["min_gini"] == ""


def test_releasing_the_lab_keeps_its_minimum(course):
    _url, tok, call = course
    _set_min(call, tok, "6.22.12")
    call("/api/activities/unrelease", {"course": "comp535", "lab": "lab1"}, tok)
    call("/api/activities/release", {"course": "comp535", "lab": "lab1"}, tok)
    rows = call("/api/activities?course=comp535", session=tok)
    assert next(x for x in rows if x["lab"] == "lab1")["min_gini"] == "6.22.12"


def test_the_student_is_told_when_they_take_a_code(course):
    _url, tok, call = course
    _set_min(call, tok, "6.22.12")
    vend, _ = _code(call)
    assert vend["min_gini"] == "6.22.12"


def test_an_installed_gbuilder_that_sends_no_version_cannot_start_the_code(course):
    """What every gBuilder now in students' hands sends: the code and nothing else."""
    _url, tok, call = course
    _set_min(call, tok, "6.22.12")
    _, code = _code(call)
    r = call(f"/api/activity?code={code}")
    assert r["ok"] is False and r["reason"] == ACT.UPDATE_GINI and r["min_gini"] == "6.22.12"
    assert "questions" not in r and "brief" not in r            # nothing to start with


def test_an_old_version_is_refused_and_a_new_enough_one_arms(course):
    _url, tok, call = course
    _set_min(call, tok, "6.22.12")
    _, code = _code(call)
    assert call(f"/api/activity?code={code}&gini=6.22.11")["reason"] == ACT.UPDATE_GINI
    ok = call(f"/api/activity?code={code}&gini=6.22.12")
    assert ok["ok"] is True and ok["min_gini"] == "6.22.12" and ok["questions"]


def test_a_lab_without_a_minimum_arms_for_a_gbuilder_that_says_nothing(course):
    """Every lab already out there has no minimum, and must go on working exactly as before."""
    _url, _tok, call = course
    _, code = _code(call)
    assert call(f"/api/activity?code={code}")["ok"] is True


def test_a_bad_code_is_still_refused_as_a_bad_code_not_as_an_old_gbuilder(course):
    """The version is checked only after the code, so the reply says nothing new about codes."""
    _url, tok, call = course
    _set_min(call, tok, "6.22.12")
    assert call("/api/activity?code=ZZZZZZZZZZ")["reason"] != ACT.UPDATE_GINI


def test_gbuilder_sends_its_own_version_with_the_code(course, monkeypatch):
    from gini import version as V
    from gini.services import tc_submit
    url, tok, call = course
    _set_min(call, tok, "6.22.12")
    _, code = _code(call)
    monkeypatch.setattr(V, "gini_version", lambda: "6.22.11")
    r = tc_submit.check_code(url, code)
    assert r["reason"] == tc_submit.UPDATE_GINI and r["your_gini"] == "6.22.11"
    monkeypatch.setattr(V, "gini_version", lambda: "6.22.12")
    assert tc_submit.check_code(url, code)["ok"] is True


# --------------------------------------------------------------------------- #
# an existing database
# --------------------------------------------------------------------------- #

def test_a_database_from_before_the_column_gains_it_and_keeps_its_labs(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = sqlite3.connect(data / "gini.db")
    db.execute("CREATE TABLE activity (id TEXT PRIMARY KEY, course TEXT NOT NULL, lab TEXT NOT "
               "NULL, title TEXT DEFAULT '', status TEXT DEFAULT 'draft', "
               "vend_until REAL DEFAULT 0, session_minutes INTEGER DEFAULT 60)")
    db.execute("INSERT INTO activity(id, course, lab, title) VALUES('c/lab1','c','lab1','Old')")
    db.commit()
    db.close()
    Store._instances.clear()
    st = Store(str(tmp_path))
    row = st.activity("c/lab1")
    assert row["title"] == "Old" and row["min_gini"] == ""
    st.activity_put({**row, "min_gini": "6.22.12"})
    assert st.activity("c/lab1")["min_gini"] == "6.22.12"


# --------------------------------------------------------------------------- #
# gBuilder: the pop-up
# --------------------------------------------------------------------------- #

def test_the_upgrade_command_matches_how_gbuilder_was_installed(monkeypatch):
    from gini.services import tc_submit
    monkeypatch.setattr(sys, "prefix", "/Users/s/.local/pipx/venvs/gini-toolkit")
    assert tc_submit.upgrade_command() == "pipx upgrade gini-toolkit"
    monkeypatch.setattr(sys, "prefix", "/Users/s/venv")
    monkeypatch.setattr(sys, "executable", "/Users/s/venv/bin/python3")
    assert tc_submit.upgrade_command() == "/Users/s/venv/bin/python3 -m pip install --upgrade " \
                                          "gini-toolkit"
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Python312\python.exe")
    assert tc_submit.upgrade_command().startswith(r'"C:\Program Files\Python312\python.exe" -m')


def test_a_too_old_refusal_pops_up_and_arms_nothing(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from gini import version as V
    from gini.ui import proof_strip as PS
    QApplication.instance() or QApplication([])
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self) or 0)
    monkeypatch.setattr(V, "gini_version", lambda: "6.22.11")
    strip = PS.ProofStrip.__new__(PS.ProofStrip)
    said, armed = [], []
    strip._say = lambda text, bad=False: said.append(text)
    strip.refresh = lambda keep_hint=False: None
    strip._arm_locally = lambda *a, **k: armed.append(a) or True
    PS.QWidget.__init__(strip)
    refusal = ACT.gini_too_old({"min_gini": "6.22.12"}, "6.22.11")
    strip._on_arm_checked("ABCD-EFGH-JK", refusal)
    assert not armed
    assert said and "6.22.12" in said[0]
    box = shown[0]
    assert "needs GINI 6.22.12 or newer" in box.text() and "6.22.11" in box.text()
    assert "gini-toolkit" in box.informativeText()


def test_any_other_refusal_stays_in_the_strip():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gini.ui import proof_strip as PS
    QApplication.instance() or QApplication([])
    strip = PS.ProofStrip.__new__(PS.ProofStrip)
    PS.QWidget.__init__(strip)
    popped = []
    strip._say = lambda text, bad=False: None
    strip.refresh = lambda keep_hint=False: None
    strip._ask_to_update = lambda answer: popped.append(answer)
    strip._on_arm_checked("X", {"ok": False, "reason": ACT.EXPIRED, "error": "expired"})
    assert not popped
