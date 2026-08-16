"""The generic DiagnoseGameWidget — drives any game from a spec + source + renderer (offscreen)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain.diagnose import GRADED, PRACTICE, Case, GameSpec

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

SPEC = GameSpec(id="t", title="T", prompt="what is it?", classes=["a", "b", "c"],
                abbrev={"a": "A", "b": "B", "c": "C"})
CASES = [Case(id="1", signature="s1", truth="a", subtitle="one", hint="a"),
         Case(id="2", signature="s2", truth="b", subtitle="two", hint="b")]


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


class _FakeRenderer(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.shown = []
    def show_signature(self, sig):
        self.shown.append(sig)


def _widget(app, mode=PRACTICE):
    from gini.ui.diagnose_game import DiagnoseGameWidget
    w = DiagnoseGameWidget(_theme(app), SPEC, lambda: list(CASES), _FakeRenderer(),
                           live=False, deck=4)
    if mode == GRADED:
        w._set_mode(GRADED)
    return w


def test_widget_has_a_button_per_class_and_renders_a_mystery(app):
    w = _widget(app)
    labels = [b.text() for b in w._class_btns]
    assert labels == ["a", "b", "c"]
    assert w._renderer.shown                              # a mystery signature was drawn
    w.close()


def test_guessing_scores_into_the_matrix(app):
    w = _widget(app)
    w._session.current = CASES[0]                         # truth 'a'
    w._guess("a")
    w._session.current = CASES[1]                         # truth 'b'
    w._guess("a")
    assert w._session.pairs == [("a", "a"), ("b", "a")]
    assert w._session.matrix()[("b", "a")] == 1
    assert "1 / 2" in w._score.text()
    w.close()


def test_graded_mode_finishes_after_the_deck(app):
    w = _widget(app, mode=GRADED)                         # deck = 4
    for _ in range(4):
        w._session.current = w._session.current or CASES[0]
        w._guess("a")
        w._session.next()                                # advance the deck counter
    w._new_mystery()
    assert w._session.finished
    assert "Run complete" in w._msg.text()
    w.close()


def test_reset_clears_everything(app):
    w = _widget(app)
    w._session.current = CASES[0]
    w._guess("a")
    w._reset()
    assert w._session.pairs == [] and "0 / 0" in w._score.text()
    w.close()
