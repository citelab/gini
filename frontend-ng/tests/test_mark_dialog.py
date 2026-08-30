"""The marking window: what a teacher is shown, and what it lets them do.

The network is covered end-to-end in `test_tc_marking.py` against a real server. What is left here
is the part a marker actually touches — whether the Open button is offered for a submission that
cannot be opened, and whether an expired session puts the sign-in form back instead of a dead end.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from gini.ui.mark_dialog import MarkDialog, _fmt


def _app():
    return QApplication.instance() or QApplication([])


def _ctx(session=""):
    return SimpleNamespace(
        settings=SimpleNamespace(tc_url="https://tc.example", tc_student="boss"),
        staff_session=session, staff_who="boss", staff_role="teacher")


REPORT = {"receipt": "4KTP-9QME", "activity": "comp535/lab1", "title": "Multi-LAN",
          "verdict": "intact", "minutes": 41.0, "within_session": True, "entries": 63,
          "runnable": True, "narration": "Placed R1, wired S1…"}


def test_signed_out_shows_the_sign_in_form_and_locks_the_receipt():
    _app()
    d = MarkDialog(_ctx(), lambda *a: None)
    assert d.auth.isVisibleTo(d)
    assert not d.receipt.isEnabled() and not d.look_btn.isEnabled()


def test_a_session_replaces_the_form_with_the_receipt_box():
    _app()
    d = MarkDialog(_ctx(session="S"), lambda *a: None)
    assert not d.auth.isVisibleTo(d)
    assert d.receipt.isEnabled() and d.look_btn.isEnabled()


def test_signing_in_keeps_the_session_and_forgets_the_password():
    _app()
    ctx = _ctx()
    d = MarkDialog(ctx, lambda *a: None)
    d.password.setText("correct-horse")
    d._on_signed_in({"session": "S", "who": "boss", "role": "teacher"}, "")
    assert ctx.staff_session == "S" and ctx.staff_who == "boss"
    assert d.password.text() == ""              # never left lying in a widget
    assert not d.auth.isVisibleTo(d)


def test_a_report_is_shown_and_open_is_offered():
    _app()
    d = MarkDialog(_ctx(session="S"), lambda *a: None)
    d._on_fetched(REPORT, "")
    text = d.out.toPlainText()
    assert "Multi-LAN" in text and "4KTP-9QME" in text and "63 recorded" in text
    assert d.open_btn.isEnabled()


def test_a_submission_with_no_runnable_copy_does_not_offer_to_open_it():
    """An older gBuilder sent a proof and no package. A button that yields nothing is worse than no
    button — the report says so instead."""
    _app()
    d = MarkDialog(_ctx(session="S"), lambda *a: None)
    d._on_fetched(dict(REPORT, runnable=False), "")
    assert not d.open_btn.isEnabled()
    assert "runnable  NO" in d.out.toPlainText()          # stated in the report…
    assert "no runnable copy" in d.hint.text()            # …and explained beside the button


def test_an_overrun_and_a_twin_are_both_surfaced():
    """Flagged, never decided: a shared starter topology is a legitimate reason for two submissions
    to match, and only the teacher can tell which it is."""
    out = _fmt(dict(REPORT, within_session=False, twins=["OTHER"]))
    assert "LONGER than the code allowed" in out
    assert "MATCHES" in out and "1 other code" in out


def test_nothing_in_the_report_pretends_to_be_a_score():
    out = _fmt(REPORT).lower()
    for word in ("score", "grade", "mark:", "/100", "%"):
        assert word not in out


def test_an_expired_session_puts_the_sign_in_form_back():
    """Rather than leaving a marker looking at a receipt box that will never work again."""
    _app()
    ctx = _ctx(session="S")
    d = MarkDialog(ctx, lambda *a: None)
    d._on_fetched(None, "not signed in")
    assert ctx.staff_session == ""
    assert d.auth.isVisibleTo(d)


def test_opening_hands_the_project_to_the_window_and_closes():
    _app()
    seen = {}
    d = MarkDialog(_ctx(session="S"), lambda proj, rep: seen.update(proj=proj, rep=rep))
    d._on_fetched(REPORT, "")
    d._on_opened({"format": "gini-project", "topology": {"devices": [], "links": []}}, "")
    assert seen["proj"]["format"] == "gini-project"
    assert seen["rep"]["receipt"] == "4KTP-9QME"


def test_a_download_that_fails_says_so_and_stays_open():
    _app()
    d = MarkDialog(_ctx(session="S"), lambda *a: pytest.fail("must not open"))
    d._on_fetched(REPORT, "")
    d._on_opened(None, "the course server refused")
    assert "refused" in d.hint.text()


def test_the_report_stays_open_after_the_work_lands_on_the_canvas():
    """A marker reads the account of what happened WHILE looking at the topology it describes.
    Closing the report the moment the canvas fills means holding it in your head, or looking the
    same receipt up a second time."""
    _app()
    opened = {}
    d = MarkDialog(_ctx(session="S"), lambda proj, rep: opened.update(rep=rep))
    d.show()                                           # as main_window opens it — show(), not exec()
    d._on_fetched(REPORT, "")
    d._on_opened({"format": "gini-project", "topology": {"devices": [], "links": []}}, "")
    assert opened["rep"]["receipt"] == "4KTP-9QME"     # the work went to the canvas…
    assert d.isVisible()                               # …and the report is still on screen
    assert d.result() == 0                             # not accepted/closed
    assert d.receipt.isEnabled()                       # ready for the next receipt


def test_the_window_never_blocks_the_canvas():
    """Modal would make 'open it and work on it' impossible by construction."""
    _app()
    assert MarkDialog(_ctx(session="S"), lambda *a: None).isModal() is False
