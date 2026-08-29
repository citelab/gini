"""Arming a code: what the student is told they are recording for.

The server has always answered `check_code` with `activity` (which is `course/lab`), `title`,
`brief`, `session_minutes` and `valid_until`. The strip checked `ok` and discarded the rest — so a
student who pasted a code from their OTHER course armed silently, worked, and handed in there.
Correct, and invisible. These tests pin the two halves of the fix: say what was armed, and warn on
a mismatch without ever refusing one.
"""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pathlib
import tempfile

from PySide6.QtWidgets import QApplication

from gini.domain import proof as P
from gini.domain.ticket import mint
from gini.domain.topology import Topology
from gini.services.proof_recorder import ProofRecorder
from gini.ui.proof_strip import ProofStrip
from gini.ui.theme.manager import ThemeManager

CODE = mint(lambda n: bytes((i * 17 + 5) % 256 for i in range(n))).pretty


def _app():
    return QApplication.instance() or QApplication([])


class _Bus:
    def __getattr__(self, _n):
        return type("S", (), {"connect": lambda *a: None, "emit": lambda *a: None})()


def _strip(course=""):
    app = _app()

    class Ctx:
        bus = _Bus()
        settings = type("S", (), {"tc_url": "https://tc.example", "tc_course": course})()
        topology = Topology("lab1")

    rec = ProofRecorder(Ctx(), store=P.ChainStore(pathlib.Path(tempfile.mkdtemp())))
    return ProofStrip(ThemeManager(app), rec)


def _hint(strip):
    return re.sub("<[^>]+>", "", strip.hint.text())


ANSWER = {"ok": True, "activity": "cs4480-fall26/lab3", "title": "Multi-LAN",
          "session_minutes": 60, "valid_until": 9e9}


def test_arming_says_which_activity_it_is_recording_for():
    strip = _strip(course="cs4480-fall26")
    strip._on_arm_checked(CODE, ANSWER)
    assert strip.recorder.armed
    text = _hint(strip)
    assert "cs4480-fall26/lab3" in text and "Multi-LAN" in text


def test_a_code_from_another_course_warns_but_still_arms():
    """THE case: two courses running at once, and the wrong code pasted. A refusal here would
    strand a student legitimately enrolled in both, at deadline time."""
    strip = _strip(course="cs4480-fall26")
    strip._on_arm_checked(CODE, dict(ANSWER, activity="comp535/lab1", title="Routing"))
    assert strip.recorder.armed                      # NOT refused
    text = _hint(strip)
    assert "comp535" in text and "cs4480-fall26" in text
    assert "your course is set to" in text


def test_a_matching_course_says_nothing_about_courses():
    strip = _strip(course="cs4480-fall26")
    strip._on_arm_checked(CODE, ANSWER)
    assert "your course is set to" not in _hint(strip)


def test_no_configured_course_is_not_a_mismatch():
    """Course is for Missions; a student who never set one still hands work in normally."""
    strip = _strip(course="")
    strip._on_arm_checked(CODE, dict(ANSWER, activity="comp535/lab1"))
    assert strip.recorder.armed
    assert "your course is set to" not in _hint(strip)


def test_a_refused_code_still_reports_the_servers_reason():
    strip = _strip(course="cs4480-fall26")
    strip._on_arm_checked(CODE, {"ok": False, "reason": "expired",
                                 "error": "That code has expired."})
    assert not strip.recorder.armed
    assert "expired" in _hint(strip)


def test_an_unreachable_server_still_records_locally():
    """Unchanged behaviour, pinned here because _on_arm_checked was restructured around it."""
    strip = _strip(course="cs4480-fall26")
    strip._on_arm_checked(CODE, {})                  # {} == could not ask
    assert strip.recorder.armed
    assert "recording locally" in _hint(strip).lower()
