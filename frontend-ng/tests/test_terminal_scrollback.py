"""Scrolling back through a terminal that is still printing.

Reported from use: "we can scroll, but it is very jumpy… the terminal wants to jump back to the
current line as the next line is updated — the terminal is fighting me", with a `ping` running.

Three separate defects were behind that, and fixing only the obvious one leaves the complaint
standing:

1. **It snapped.** `feed()` reset the scroll offset on every chunk, so any output returned the
   view to the live screen. A once-a-second ping gave one second of reading per second.
2. **It drifted.** `_doc_top` is `len(history.top) - _scroll`, so every line that scrolls off the
   live screen moves the viewport one line further down the document. Holding the offset still
   does NOT hold the text still — the page slides out from under the reader at exactly the rate
   output arrives, which is its own kind of fighting and survives fixing (1).
3. **The wheel ignored how far you scrolled.** A fixed ±3 lines per EVENT looks right for a
   mouse, which sends one event per notch, and is unusable on a trackpad, which sends a stream
   of small ones — three lines each. That is the "jumpy" in the report.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from gini.ui.terminal_view import TerminalView


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Theme:
    class theme:
        panel2, bg, text, line, muted, accent = ("#1e222a", "#15181d", "#dcdfe4",
                                                 "#232b36", "#8b93a1", "#4c8dff")


def _view(app, lines=200):
    v = TerminalView(_Theme())
    for i in range(lines):
        v.feed(b"line %d\r\n" % i)
    return v


def _top_row(v) -> str:
    line = v._visible_lines()[0]
    return "".join(line[x].data for x in range(v._screen.columns)).rstrip()


def _wheel(v, *, angle=0, pixels=0):
    e = QWheelEvent(QPointF(10, 10), v.mapToGlobal(QPointF(10, 10)),
                    QPoint(0, pixels), QPoint(0, angle),
                    Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    v.wheelEvent(e)


# --------------------------------------------------------------------------- #
# 1 + 2 — the view stays where the reader put it
# --------------------------------------------------------------------------- #

def test_output_does_not_snap_the_view_back_to_the_bottom(app):
    v = _view(app)
    v._set_scroll(40)
    for i in range(10):
        v.feed(b"more %d\r\n" % i)
    assert v.scrolled_back() > 0


def test_the_text_being_read_stays_under_the_reader(app):
    """The drift bug. Not snapping is not enough — the offset has to GROW with the history, or
    the line you are looking at walks off the top while you read it."""
    v = _view(app)
    v._set_scroll(40)
    before = _top_row(v)
    for i in range(10):
        v.feed(b"more %d\r\n" % i)
    assert _top_row(v) == before, "the page slid while output arrived"
    assert v.scrolled_back() == 50, "the offset must track what scrolled off"


def test_a_long_burst_does_not_move_it_either(app):
    v = _view(app)
    v._set_scroll(60)
    before = _top_row(v)
    v.feed(b"".join(b"burst %d\r\n" % i for i in range(300)))
    assert _top_row(v) == before


def test_following_the_tail_is_the_default(app):
    v = _view(app)
    assert v.scrolled_back() == 0
    v.feed(b"newest\r\n")
    assert v.scrolled_back() == 0
    assert "newest" in "".join(v._screen.display)


def test_going_back_to_the_bottom_shows_the_newest_line(app):
    v = _view(app)
    v._set_scroll(40)
    v.feed(b"newest\r\n")
    v.to_bottom()
    assert v.scrolled_back() == 0
    assert "newest" in "".join(v._screen.display)


def test_typing_returns_to_the_live_screen(app):
    """Unchanged, and it is the escape hatch: press any key and you are back at the prompt."""
    v = _view(app)
    v._set_scroll(40)
    v.keyPressEvent(_key(Qt.Key_A, "a"))
    assert v.scrolled_back() == 0


def _key(key, text="", mods=Qt.NoModifier):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    return QKeyEvent(QEvent.KeyPress, key, mods, text)


# --------------------------------------------------------------------------- #
# 3 — the wheel moves as far as you turned it
# --------------------------------------------------------------------------- #

def test_one_notch_is_three_lines(app):
    v = _view(app)
    _wheel(v, angle=120)
    assert v.scrolled_back() == 3


def test_a_trackpads_small_deltas_do_not_jump_three_lines_each(app):
    """The jumpiness. Each of these used to move three lines; together they should move about
    as far as the fingers did."""
    v = _view(app)
    for _ in range(4):
        _wheel(v, angle=10)                  # a twelfth of a notch, four times
    assert v.scrolled_back() == 1, v.scrolled_back()


def test_the_remainder_is_carried_between_events(app):
    """Otherwise every sub-notch event rounds to zero and a slow drag does nothing at all."""
    v = _view(app)
    for _ in range(12):
        _wheel(v, angle=10)                  # 120 units total = one notch
    assert v.scrolled_back() == 3


def test_pixel_deltas_scroll_by_cell_height(app):
    """`_ch` is a float — rounding it down before multiplying loses most of a line over five."""
    v = _view(app)
    _wheel(v, pixels=round(v._ch * 5))
    assert v.scrolled_back() == 5


def test_scrolling_stops_at_both_ends(app):
    v = _view(app)
    _wheel(v, angle=-120)
    assert v.scrolled_back() == 0, "cannot scroll below the live screen"
    for _ in range(500):
        _wheel(v, angle=120)
    assert v.scrolled_back() == len(v._screen.history.top), "cannot scroll past the oldest line"


# --------------------------------------------------------------------------- #
# the keyboard chords every terminal has
# --------------------------------------------------------------------------- #

def test_shift_pageup_and_pagedown_move_a_screen(app):
    v = _view(app)
    v.keyPressEvent(_key(Qt.Key_PageUp, mods=Qt.ShiftModifier))
    assert v.scrolled_back() == v._screen.lines - 1
    v.keyPressEvent(_key(Qt.Key_PageDown, mods=Qt.ShiftModifier))
    assert v.scrolled_back() == 0


def test_shift_home_and_end_go_to_the_ends(app):
    v = _view(app)
    v.keyPressEvent(_key(Qt.Key_Home, mods=Qt.ShiftModifier))
    assert v.scrolled_back() == len(v._screen.history.top)
    v.keyPressEvent(_key(Qt.Key_End, mods=Qt.ShiftModifier))
    assert v.scrolled_back() == 0


def test_unshifted_pageup_still_belongs_to_the_program(app):
    """`less` and `vi` need it. Only the shifted chords are the terminal's own."""
    v = _view(app)
    sent = []
    v.key_bytes.connect(sent.append)
    v.keyPressEvent(_key(Qt.Key_PageUp))
    assert sent, "PageUp was swallowed instead of reaching the shell"
    assert v.scrolled_back() == 0
