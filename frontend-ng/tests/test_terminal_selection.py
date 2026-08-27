"""Selecting, copying and pasting in the Qt-drawn terminal.

Possible precisely because it is a QWidget with our own paintEvent rather than an embedded web
page. Two things here are easy to get wrong and expensive when wrong:

  * Ctrl-C must stay the INTERRUPT. Copy is Ctrl+Shift+C (Cmd-C on macOS) for that reason — a
    student who cannot stop a `ping` has no way out of it.
  * On macOS Qt SWAPS the modifiers: Qt.ControlModifier is Command and Qt.MetaModifier is
    Control. Reading ControlModifier as "Ctrl" sends \\x03 when the student presses Cmd-C to copy,
    and sends nothing when they press the real Ctrl-C. That bug shipped in the first version of
    the key encoder and is what these tests exist to prevent recurring.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

from PySide6.QtCore import Qt

from gini.ui import terminal_view as tv
from gini.ui.terminal_view import TerminalView, clipboard_chord, encode_key, terminal_ctrl


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _view(app):
    v = TerminalView()
    v.resize(640, 320)
    v.feed(b"64 bytes from 10.0.1.10: icmp_seq=1 ttl=64 time=0.5 ms\r\n")
    v.feed(b"64 bytes from 10.0.1.10: icmp_seq=2 ttl=64 time=0.4 ms\r\n")
    return v


# -- the platform modifier trap ---------------------------------------------- #
def test_on_linux_ctrl_is_ctrl(monkeypatch):
    monkeypatch.setattr(tv, "_MAC", False)
    assert terminal_ctrl(Qt.ControlModifier)
    assert not terminal_ctrl(Qt.MetaModifier)


def test_on_macos_the_terminal_ctrl_is_qts_meta(monkeypatch):
    """Qt.ControlModifier is Command there. Treating it as Ctrl makes Cmd-C kill the command."""
    monkeypatch.setattr(tv, "_MAC", True)
    assert terminal_ctrl(Qt.MetaModifier), "real Ctrl-C would do nothing on a Mac"
    assert not terminal_ctrl(Qt.ControlModifier), "Cmd-C would send an interrupt on a Mac"


def test_copy_chord_is_cmd_on_macos_and_ctrl_shift_elsewhere(monkeypatch):
    monkeypatch.setattr(tv, "_MAC", True)
    assert clipboard_chord(Qt.ControlModifier)                       # Cmd
    monkeypatch.setattr(tv, "_MAC", False)
    assert clipboard_chord(Qt.ControlModifier | Qt.ShiftModifier)
    assert not clipboard_chord(Qt.ControlModifier), "plain Ctrl-C must not be a copy"


def test_plain_ctrl_c_still_interrupts(monkeypatch):
    """The one that matters most. If this ever returns b"" the student is stuck in `ping`."""
    monkeypatch.setattr(tv, "_MAC", False)
    assert encode_key(Qt.Key_C, Qt.ControlModifier, "\x03") == b"\x03"
    monkeypatch.setattr(tv, "_MAC", True)
    assert encode_key(Qt.Key_C, Qt.MetaModifier, "\x03") == b"\x03"


def test_the_copy_chord_does_not_reach_the_pty(monkeypatch):
    monkeypatch.setattr(tv, "_MAC", False)
    assert encode_key(Qt.Key_C, Qt.ControlModifier | Qt.ShiftModifier, "") == b""


# -- selection ---------------------------------------------------------------- #
def test_selecting_part_of_a_line(app):
    v = _view(app)
    v._sel_anchor, v._sel_head = (0, 15), (0, 24)
    assert v.selected_text() == "0.0.1.10:"


def test_selecting_across_lines(app):
    v = _view(app)
    v._sel_anchor, v._sel_head = (0, 0), (1, 10)
    text = v.selected_text()
    assert text.count("\n") == 1
    assert text.startswith("64 bytes from")


def test_trailing_blanks_are_trimmed(app):
    """A terminal row is padded to the full width. Without trimming, every copied line drags
    tens of spaces into whatever it is pasted into."""
    v = _view(app)
    v._sel_anchor, v._sel_head = (0, 0), (0, v._screen.columns)
    assert not v.selected_text().endswith(" ")


def test_a_selection_survives_new_output(app):
    """Anchored to DOCUMENT rows, not screen rows. Highlight a captured packet, let the capture
    run on, and the highlight must still be on that packet rather than sliding up the screen."""
    v = _view(app)
    v._sel_anchor, v._sel_head = (0, 15), (0, 24)
    before = v.selected_text()
    for i in range(40):
        v.feed(b"filler %d\r\n" % i)
    assert v.selected_text() == before, "the selection drifted when new output arrived"


def test_a_click_maps_to_the_document_not_the_screen(app):
    """The other half of anchoring, and the half the test above does not reach.

    selected_text() indexes the document directly, so it stays correct even if the mouse mapping
    is wrong — mutation testing caught that: making _doc_top() return 0 left every text test
    green. What breaks is the MOUSE: the same pixel must map to a later document row once output
    has scrolled, or a drag selects whatever used to be there.
    """
    from PySide6.QtCore import QPointF
    v = _view(app)
    v.show()
    first, _ = v._cell_at(QPointF(0, 0.5 * v._ch))
    for i in range(40):
        v.feed(b"line %d\r\n" % i)
    later, _ = v._cell_at(QPointF(0, 0.5 * v._ch))
    assert later > first, (
        "the top row maps to the same document index after 40 lines of output — clicks are "
        "anchored to the screen, so a selection points at whatever has scrolled into that row")
    line = v._line_at(later)
    assert "line" in "".join(line[x].data for x in range(v._screen.columns)), (
        "the mapped document row does not hold what is on screen there")


def test_double_click_selects_a_word(app):
    """Usually an IP address or an interface name — the thing most worth copying out of here."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    v = _view(app)
    v.show()
    x = 16 * v._cw          # inside "10.0.1.10"
    ev = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(x, 0.5 * v._ch),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    v.mouseDoubleClickEvent(ev)
    assert v.selected_text() == "10.0.1.10", f"got {v.selected_text()!r}"


def test_a_word_stops_at_punctuation_that_is_not_part_of_it(app):
    """ping prints "from 10.0.1.10:" and "icmp_seq=1". Dragging the ':' or the '=1' along means
    editing it out of every paste, so ':' and '=' are not word characters — while '.', '-', '/'
    and '_' are, because addresses, masks, interface names and paths need them."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    v = _view(app)
    v.show()
    col = v._screen.display[0].index("icmp_seq") + 2
    v.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick,
                                        QPointF(col * v._cw, 0.5 * v._ch),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert v.selected_text() == "icmp_seq", f"got {v.selected_text()!r}"


def test_select_all_covers_scrollback(app):
    v = _view(app)
    for i in range(60):
        v.feed(b"line %d\r\n" % i)
    v.select_all()
    text = v.selected_text()
    assert "64 bytes from" in text, "select all missed the scrollback"
    assert "line 59" in text, "select all missed the live screen"


def test_a_plain_click_clears_the_selection(app):
    v = _view(app)
    v._sel_anchor, v._sel_head = (0, 0), (0, 10)
    v._selecting = False
    v._sel_head = v._sel_anchor          # a click leaves anchor == head
    v.mouseReleaseEvent(_click(v))
    assert not v.has_selection()


def _click(v):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    return QMouseEvent(QEvent.MouseButtonRelease, QPointF(1, 1),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


# -- clipboard ---------------------------------------------------------------- #
def test_copy_puts_the_selection_on_the_clipboard(app):
    v = _view(app)
    v._sel_anchor, v._sel_head = (0, 15), (0, 24)
    assert v.copy() is True
    assert QtWidgets.QApplication.clipboard().text() == "0.0.1.10:"


def test_copy_with_nothing_selected_reports_false(app):
    """So the caller can tell the difference between 'copied' and 'nothing to copy'."""
    v = _view(app)
    v.clear_selection()
    assert v.copy() is False


def test_paste_sends_carriage_returns_not_newlines(app):
    """A shell runs a line on CR. Pasting LF gives lines that look entered but never execute —
    the classic 'I pasted my commands and nothing happened'."""
    v = _view(app)
    QtWidgets.QApplication.clipboard().setText("route show\nifconfig show\n")
    sent = []
    v.key_bytes.connect(sent.append)
    v.paste()
    assert sent == [b"route show\rifconfig show\r"]


def test_paste_handles_windows_line_endings(app):
    v = _view(app)
    QtWidgets.QApplication.clipboard().setText("a\r\nb")
    sent = []
    v.key_bytes.connect(sent.append)
    v.paste()
    assert sent == [b"a\rb"], "CRLF became a double carriage return"


def test_an_empty_clipboard_sends_nothing(app):
    v = _view(app)
    QtWidgets.QApplication.clipboard().setText("")
    sent = []
    v.key_bytes.connect(sent.append)
    v.paste()
    assert sent == []


def test_painting_a_selection_does_not_raise(app):
    from PySide6.QtGui import QPixmap
    v = _view(app)
    v._sel_anchor, v._sel_head = (0, 5), (1, 20)
    v.render(QPixmap(v.size()))
