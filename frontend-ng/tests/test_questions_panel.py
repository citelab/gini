"""The Ask Questions tab: what a student actually sees, and when.

Written as the states the panel has to get right, because every one of them is a moment where a
student could lose work or lose marks:

  * armed offline — it knows there ARE questions and has to say so, or a lab is handed in with
    blanks caused by hotel wifi and nobody finds out until it is marked;
  * mid-answer — a rebuild must never destroy the box someone is typing in;
  * handed in — the boxes have to go dead, because an answer past the `submit` entry is in a
    chain nobody reads again.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication                        # noqa: E402

from gini.domain import lab_questions as lq                       # noqa: E402
from gini.ui.questions_panel import QuestionsPanel, announce      # noqa: E402
from gini.ui.theme import ThemeManager                            # noqa: E402

QS = [lq.Question("q1", "What IP did you give M1?"),
      lq.Question("q2", "Which command showed the route?")]


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qtbot):
    p = QuestionsPanel(ThemeManager(_app(), "Light"))
    qtbot.addWidget(p)
    return p


def _text(p):
    return p._sub.text()


# ---- the four states ------------------------------------------------------------- #
def test_before_arming_it_says_where_a_code_goes(panel):
    panel.show_state(armed=False, submitted=False, questions=[], answers={})
    assert "assignment code" in _text(panel)
    assert panel._cards == {}


def test_a_lab_with_no_questions_says_so_rather_than_looking_broken(panel):
    panel.show_state(armed=True, submitted=False, questions=[], answers={})
    assert "does not ask any questions" in _text(panel)


def test_armed_offline_it_says_the_questions_exist_and_offers_to_fetch(panel):
    """The whole reason the assignment code carries a bit. Without this the panel is empty and
    indistinguishable from a lab that asks nothing."""
    panel.show_state(armed=True, submitted=False, questions=[], answers={},
                     expects_questions=True)
    assert "could not be reached" in _text(panel)
    assert not panel._fetch.isHidden()


def test_it_does_not_cry_offline_for_a_lab_that_asks_nothing(panel):
    panel.show_state(armed=True, submitted=False, questions=[], answers={},
                     expects_questions=False)
    assert panel._fetch.isHidden()


def test_armed_with_questions_it_shows_one_box_each(panel):
    panel.show_state(armed=True, submitted=False, questions=QS, answers={})
    assert len(panel._cards) == 2
    assert panel._cards["q1"]._prompt.text() == "What IP did you give M1?"


# ---- answers ---------------------------------------------------------------------- #
def test_an_answer_already_in_the_chain_comes_back_into_its_box(panel):
    """Resuming a code, or reopening gBuilder. The chain is the state; this only displays it."""
    panel.show_state(armed=True, submitted=False, questions=QS, answers={"q1": "10.0.0.2"})
    assert panel._cards["q1"].text() == "10.0.0.2"
    assert panel._cards["q2"].text() == ""


def test_recording_an_answer_reports_the_id_and_the_text(panel, qtbot):
    panel.show_state(armed=True, submitted=False, questions=QS, answers={})
    panel._cards["q1"]._box.setPlainText("10.0.0.2")
    with qtbot.waitSignal(panel.answered) as got:
        panel._cards["q1"]._save.click()
    assert got.args == ["q1", "10.0.0.2"]


def test_the_button_is_dead_until_there_is_something_new_to_record(panel):
    panel.show_state(armed=True, submitted=False, questions=QS, answers={"q1": "10.0.0.2"})
    card = panel._cards["q1"]
    assert not card._save.isEnabled(), "nothing has changed; there is nothing to record"
    card._box.setPlainText("10.0.0.2/24")
    assert card._save.isEnabled()


def test_an_answer_already_recorded_says_it_can_be_changed(panel):
    panel.show_state(armed=True, submitted=False, questions=QS, answers={"q1": "10.0.0.2"})
    assert "change it" in panel._cards["q1"]._state.text()


def test_a_rebuild_does_not_destroy_the_box_someone_is_typing_in(panel):
    """THE bug this design has to avoid. The panel is refreshed on every recorder change — which
    includes every device placed and every command run — so a rebuild on each one would wipe a
    half-typed answer while the student was still writing it."""
    panel.show_state(armed=True, submitted=False, questions=QS, answers={})
    card = panel._cards["q1"]
    card._box.setPlainText("half a thought")
    panel.show_state(armed=True, submitted=False, questions=QS, answers={})
    assert panel._cards["q1"] is card
    assert card.text() == "half a thought"


def test_a_different_lab_does_rebuild(panel):
    panel.show_state(armed=True, submitted=False, questions=QS, answers={})
    other = [lq.Question("z9", "Something else entirely")]
    panel.show_state(armed=True, submitted=False, questions=other, answers={})
    assert list(panel._cards) == ["z9"]


def test_an_answer_is_bounded_where_the_chain_bounds_it(panel):
    """The chain clips at MAX_ANSWER. Saying so while they type beats truncating in silence."""
    panel.show_state(armed=True, submitted=False, questions=QS, answers={})
    card = panel._cards["q1"]
    card._box.setPlainText("x" * (lq.MAX_ANSWER + 500))
    assert len(card.text()) == lq.MAX_ANSWER


# ---- handed in --------------------------------------------------------------------- #
def test_after_handing_in_the_boxes_go_dead(panel):
    """An answer appended past the `submit` entry is not in the proof that was sent. A live box
    would invite a student to type something no marker will ever read."""
    panel.show_state(armed=True, submitted=True, questions=QS, answers={"q1": "10.0.0.2"})
    panel.set_live(False)
    assert panel._cards["q1"]._box.isReadOnly()
    assert not panel._cards["q1"]._save.isEnabled()
    assert "cannot be changed" in _text(panel)


# ---- the tab ------------------------------------------------------------------------ #
def test_the_tab_carries_the_count_of_what_is_left():
    """A beep is gone in a second. A student who comes back twenty minutes later needs something
    still on screen, and the tab is all of this panel that is visible behind another one."""
    assert announce(QS, {}) == "Ask Questions (2)"
    assert announce(QS, {"q1": "x"}) == "Ask Questions (1)"
    assert announce(QS, {"q1": "x", "q2": "y"}) == "Ask Questions"
    assert announce([], {}) == "Ask Questions"


def test_the_title_says_how_far_along_they_are(panel):
    panel.show_state(armed=True, submitted=False, questions=QS, answers={"q1": "x"})
    assert "1 of 2" in panel._title.text()


def test_a_long_question_cannot_set_the_panels_width(panel):
    """The scheduler panel was 3325 px wide for exactly this reason: a QLabel that cannot wrap
    hands its whole single-line width to the layout as a MINIMUM."""
    long_q = [lq.Question("q1", "Why did the first ping fail, and what did you have to change "
                                "on M1 before the second one worked? " * 3)]
    panel.show_state(armed=True, submitted=False, questions=long_q, answers={})
    assert panel._cards["q1"]._prompt.wordWrap()
    assert panel.minimumSizeHint().width() < 700
