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
    text = _hint(strip).lower()
    assert "recording on this computer" in text and "could not be reached" in text


# -- when the lab is due ------------------------------------------------------------------------ #
# The strip discarded `valid_until` and `session_minutes` too, which was survivable only while
# every lab was a timed attempt ("you have 60 minutes", said by the page that vended the code).
# A duration of 0 now means the lab is due at a fixed WALL-CLOCK time, so the deadline is the whole
# story — and gBuilder was the one place that never mentioned it.

def _due(**answer):
    return ProofStrip._due_phrase(answer)


def test_a_fixed_hand_in_time_is_announced_as_a_due_moment():
    import datetime as dt
    when = dt.datetime(2031, 5, 15, 21, 0)
    phrase = _due(valid_until=when.timestamp(), session_minutes=0)
    assert "Due" in phrase
    assert when.strftime("%H:%M") in phrase          # the actual moment, in local time
    assert "min" not in phrase                       # no attempt window to speak of


def test_a_timed_attempt_still_leads_with_the_minutes():
    import datetime as dt
    when = dt.datetime(2031, 5, 15, 21, 0)
    phrase = _due(valid_until=when.timestamp(), session_minutes=45)
    assert "45 min" in phrase                        # what the student acts on
    assert when.strftime("%H:%M") in phrase          # the backstop, still named


def test_a_lab_with_no_deadline_says_nothing_rather_than_inventing_one():
    assert _due(valid_until=0, session_minutes=0) == ""
    assert _due(session_minutes=60) == ""             # field absent entirely
    assert _due(valid_until="not a number") == ""     # unreadable is not a deadline


# -- pausing, resuming, finishing ------------------------------------------------ #
#
# Students asked for Pause and Resume by name. The strip had a Cancel that did the same thing
# underneath (the chain was always kept) and read to a student as "throw my work away". These pin
# the three states the strip now paints — recording, paused, finished — and the teacher's
# one-sitting switch, which hides Pause.

def _state(strip):
    return re.sub("<[^>]+>", "", strip.state.text())


def test_a_pausable_lab_shows_pause_while_recording():
    strip = _strip()
    strip._on_arm_checked(CODE, {**ANSWER, "pausable": True})
    assert strip.recorder.armed
    assert not strip.pause.isHidden()
    assert strip.button.text() == "Generate proof"
    assert "one sitting" not in _hint(strip)


def test_an_older_server_that_says_nothing_about_pausing_means_pausable():
    """Every lab was pausable before the switch existed; an absent key must not hide the button."""
    strip = _strip()
    strip._on_arm_checked(CODE, ANSWER)
    assert not strip.pause.isHidden()


def test_a_one_sitting_lab_has_no_pause_and_says_so_at_arm_time():
    """Said when the code is armed, because by the time a student looks for Pause and cannot find
    it they have already decided to stop — the hint is the only place that explains why."""
    strip = _strip()
    strip._on_arm_checked(CODE, {**ANSWER, "pausable": False})
    assert strip.recorder.armed
    assert strip.pause.isHidden()
    assert "one sitting" in _hint(strip)


def test_pausing_shows_the_paused_code_and_offers_resume():
    strip = _strip()
    strip._on_arm_checked(CODE, ANSWER)
    strip.pause.click()
    assert not strip.recorder.armed
    assert "PAUSED" in _state(strip) and CODE[:4] in _state(strip)
    assert strip.button.text() == "Resume"
    assert not strip.code.isHidden(), "a different code can still be typed over a paused one"
    assert CODE in _hint(strip) and "Resume" in _hint(strip)


def test_resume_arms_the_paused_code_unless_something_was_typed():
    strip = _strip()
    strip._on_arm_checked(CODE, ANSWER)
    strip.pause.click()
    assert strip._code_to_arm() == CODE               # nothing typed: Resume means this one
    strip.code.setText("ABCD-EFGH-1234")
    assert strip._code_to_arm() == "ABCD-EFGH-1234"   # typed wins


def test_resuming_carries_on_in_the_same_chain():
    strip = _strip()
    strip._on_arm_checked(CODE, ANSWER)
    strip.pause.click()
    strip._on_arm_checked(strip._code_to_arm(), ANSWER)
    assert strip.recorder.armed
    kinds = [e.kind for e in strip.recorder._chain.entries]
    assert kinds[-1] == "resumed" and "stopped" in kinds
    assert strip.button.text() == "Generate proof" and "PAUSED" not in _state(strip)


def test_the_paused_state_survives_a_new_strip():
    """A restart. The bookmark is a file beside the chains, so a fresh strip over the same store
    paints Paused with Resume — the case a pause exists for."""
    strip = _strip()
    strip._on_arm_checked(CODE, ANSWER)
    strip.pause.click()
    again = ProofStrip(ThemeManager(_app()), ProofRecorder(strip.recorder.ctx,
                                                           store=strip.recorder.store))
    assert again.button.text() == "Resume"
    assert CODE[:4] in _state(again)


def test_handing_in_finishes_the_code_and_the_strip_goes_back_to_a_code_box():
    strip = _strip()
    strip._on_arm_checked(CODE, ANSWER)
    receipt = strip.recorder.generate_proof()["receipt"]
    strip.refresh(keep_hint=True)
    assert not strip.recorder.armed
    assert strip.button.text() == "Record" and strip.state.isHidden()
    assert strip.pause.isHidden()
    # ...and the same code is refused, with the receipt, rather than quietly reopened.
    strip._on_arm_checked(CODE, ANSWER)
    assert not strip.recorder.armed
    assert "already been handed in" in _hint(strip) and receipt in _hint(strip)
