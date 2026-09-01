"""The join between the course server, the recorder and the tab.

The unit tests either side of this one would all pass with the two halves never connected. What is
tested here is the wiring: the questions really do come off the arm reply, an answer pressed in the
panel really does reach the chain, and the hand-in button really does stop and ask when something
is unanswered.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication                        # noqa: E402

from gini.domain import lab_questions as lq                       # noqa: E402
from gini.domain import proof as P                                # noqa: E402
from gini.domain.ticket import mint                               # noqa: E402
from gini.domain.topology import Topology                         # noqa: E402
from gini.services.proof_recorder import ProofRecorder            # noqa: E402
from gini.ui.proof_strip import ProofStrip                        # noqa: E402
from gini.ui.theme import ThemeManager                            # noqa: E402

REPLY = {"ok": True, "activity": "comp535/lab1", "title": "Multi-LAN",
         "questions": [{"id": "q1", "prompt": "What IP did you give M1?"},
                       {"id": "q2", "prompt": "Which command showed the route?"}]}


class _Bus:
    def __getattr__(self, _n):
        return type("S", (), {"connect": lambda *a: None, "emit": lambda *a: None})()


@pytest.fixture
def strip(qtbot):
    app = QApplication.instance() or QApplication([])

    class Ctx:
        bus = _Bus()
        settings = type("S", (), {"tc_url": "https://tc.example", "tc_course": "comp535"})()
        topology = Topology("lab1")

    rec = ProofRecorder(Ctx(), store=P.ChainStore(pathlib.Path(tempfile.mkdtemp())))
    s = ProofStrip(ThemeManager(app), rec)
    qtbot.addWidget(s)
    return s


# ---- the arm reply carries them -------------------------------------------------- #
def test_arming_puts_the_questions_on_the_recorder(strip, qtbot):
    """They arrive on the reply gBuilder was ALREADY making. No push, no poll, no second call."""
    code = mint(questions=True).pretty
    with qtbot.waitSignal(strip.questionsArrived):
        strip.armChecked.emit(code, REPLY)
    assert [q.id for q in strip.recorder.questions] == ["q1", "q2"]


def test_a_lab_with_no_questions_announces_nothing(strip):
    strip.armChecked.emit(mint().pretty, {"ok": True, "activity": "comp535/lab1", "title": "x"})
    assert strip.recorder.questions == []


def test_armed_offline_the_code_still_says_there_are_questions(strip):
    """{} from the server means "could not ask", and gBuilder arms locally. Everything about the
    lab is unknown at that point EXCEPT this, because it was printed on the code."""
    code = mint(questions=True).pretty
    strip.armChecked.emit(code, {})                    # {} == unreachable
    assert strip.recorder.armed
    assert strip.recorder.questions == []
    assert lq.missing_because_offline(strip.recorder.ticket.questions,
                                      strip.recorder.questions) is True


def test_fetching_again_fills_them_in(strip, qtbot):
    """The same button serves a code armed offline and a gBuilder restarted mid-lab: both are
    "we know there are questions and we do not have them"."""
    strip.armChecked.emit(mint(questions=True).pretty, {})
    with qtbot.waitSignal(strip.questionsArrived):
        strip.questionsFetched.emit(REPLY)
    assert len(strip.recorder.questions) == 2


def test_a_failed_refetch_says_so_and_changes_nothing(strip):
    strip.armChecked.emit(mint(questions=True).pretty, {})
    strip.questionsFetched.emit({})
    assert strip.recorder.questions == []


# ---- the nudge at hand-in --------------------------------------------------------- #
def test_handing_in_with_everything_answered_asks_nothing(strip):
    """The ordinary case must be unchanged: press Generate, get a proof."""
    strip.armChecked.emit(mint(questions=True).pretty, REPLY)
    for q in strip.recorder.questions:
        strip.recorder.note_answer(q.id, q.prompt, "an answer")
    assert strip._questions_checked() is True


def test_a_lab_with_no_questions_asks_nothing(strip):
    strip.armChecked.emit(mint().pretty, {"ok": True, "activity": "c/l", "title": "x"})
    assert strip._questions_checked() is True


def test_an_unanswered_question_stops_and_asks(strip, monkeypatch):
    """A warning, never a refusal — but not silence either, or a student hands in having simply
    never noticed the tab."""
    strip.armChecked.emit(mint(questions=True).pretty, REPLY)
    seen = {}

    def fake_exec(self):
        seen["text"] = self.text()
        # Qt returns the clicked button through clickedButton(); the first added button is
        # "Hand in anyway", and the strip compares identity against it.
        self.setResult(0)
        seen["buttons"] = [b.text() for b in self.buttons()]

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec", fake_exec)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.clickedButton",
                        lambda self: self.buttons()[0])
    assert strip._questions_checked() is True          # they chose "Hand in anyway"
    assert "Which command showed the route?" in seen["text"]
    assert seen["buttons"] == ["Hand in anyway", "Answer them first"]


def test_choosing_to_answer_first_stops_the_hand_in(strip, monkeypatch, qtbot):
    strip.armChecked.emit(mint(questions=True).pretty, REPLY)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec", lambda self: self.setResult(1))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.clickedButton",
                        lambda self: self.buttons()[1])
    with qtbot.waitSignal(strip.answerFirst):
        assert strip._questions_checked() is False


def test_questions_that_never_arrived_get_their_own_warning(strip, monkeypatch):
    """Worse than an unanswered question, because the student was never given the chance — so it
    is said separately rather than folded into "you left one blank"."""
    strip.armChecked.emit(mint(questions=True).pretty, {})
    seen = {}
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec",
                        lambda self: seen.setdefault("text", self.text()))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.clickedButton",
                        lambda self: self.buttons()[0])
    strip._questions_checked()
    assert "never managed to fetch them" in seen["text"]


# ---- the window ties it together --------------------------------------------------- #
@pytest.fixture
def win(qtbot):
    from gini.ui.main_window import MainWindow
    w = MainWindow(QApplication.instance() or QApplication([]))
    qtbot.addWidget(w)
    return w


def test_the_tab_is_there_after_terminal(win):
    from PySide6.QtWidgets import QDockWidget
    titles = [d.windowTitle() for d in win.findChildren(QDockWidget)]
    assert "Ask Questions" in titles
    assert titles.index("Ask Questions") > titles.index("Terminal")


def test_pressing_record_in_the_panel_writes_to_the_chain(win):
    """End to end through the real window: the panel's signal, the recorder's guard, the chain."""
    win.proof_strip.armChecked.emit(mint(questions=True).pretty, REPLY)
    card = win.questions_panel._cards["q1"]
    card._box.setPlainText("10.0.0.2, from the interface dialog")
    card._save.click()
    assert win.proof_recorder.answers() == {"q1": "10.0.0.2, from the interface dialog"}


def test_the_dock_tab_counts_what_is_outstanding(win):
    win.proof_strip.armChecked.emit(mint(questions=True).pretty, REPLY)
    assert win._questions_dock.windowTitle() == "Ask Questions (2)"
    win.proof_recorder.note_answer("q1", "?", "10.0.0.2")
    win._refresh_questions()
    assert win._questions_dock.windowTitle() == "Ask Questions (1)"


def test_the_dock_flags_questions_it_could_not_fetch(win):
    win.proof_strip.armChecked.emit(mint(questions=True).pretty, {})
    assert win._questions_dock.windowTitle() == "Ask Questions (!)"


def test_the_tab_is_quiet_for_a_lab_that_asks_nothing(win):
    win.proof_strip.armChecked.emit(mint().pretty, {"ok": True, "activity": "c/l", "title": "x"})
    assert win._questions_dock.windowTitle() == "Ask Questions"


def test_the_panel_goes_dead_once_the_work_is_handed_in(win):
    win.proof_strip.armChecked.emit(mint(questions=True).pretty, REPLY)
    win.proof_recorder.note_answer("q1", "?", "10.0.0.2")
    assert win.proof_recorder.generate_proof()["ok"]
    win._refresh_questions()
    assert win.questions_panel._cards["q1"]._box.isReadOnly()
