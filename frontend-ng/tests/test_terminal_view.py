"""The Qt-drawn terminal: pyte in, painted cells and key bytes out.

Replaces the embedded xterm.js page. The tests concentrate on the things a student would notice
immediately and that no unit test upstream covers: control keys reaching the PTY, colour and
scrollback surviving, and a byte stream that splits a UTF-8 character across two frames not
turning into mojibake.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

from PySide6.QtCore import Qt

from gini.ui.terminal_view import DEFAULT_COLS, DEFAULT_ROWS, TerminalView, encode_key


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _view(app, w=640, h=320):
    v = TerminalView()
    v.resize(w, h)
    return v


# -- keys ------------------------------------------------------------------- #
def test_ctrl_c_interrupts():
    """Without this a student who runs `ping` with no count has no way to stop it."""
    assert encode_key(Qt.Key_C, Qt.ControlModifier, "\x03") == b"\x03"


def test_ctrl_d_ends_the_router_cli():
    assert encode_key(Qt.Key_D, Qt.ControlModifier, "\x04") == b"\x04"


def test_the_whole_control_range_maps():
    for key, want in ((Qt.Key_A, b"\x01"), (Qt.Key_Z, b"\x1a"), (Qt.Key_L, b"\x0c")):
        assert encode_key(key, Qt.ControlModifier, "") == want


def test_arrows_are_escape_sequences():
    """Shell history is up-arrow. A plain character here gives the student a stray 'A'."""
    assert encode_key(Qt.Key_Up, Qt.NoModifier, "") == b"\x1b[A"
    assert encode_key(Qt.Key_Down, Qt.NoModifier, "") == b"\x1b[B"
    assert encode_key(Qt.Key_Right, Qt.NoModifier, "") == b"\x1b[C"
    assert encode_key(Qt.Key_Left, Qt.NoModifier, "") == b"\x1b[D"


def test_backspace_sends_del_not_backspace():
    """Terminals set `stty erase ^?`. Sending \\b instead leaves the character on screen and moves
    the cursor, which looks like the key does nothing."""
    assert encode_key(Qt.Key_Backspace, Qt.NoModifier, "\b") == b"\x7f"


def test_enter_sends_carriage_return():
    assert encode_key(Qt.Key_Return, Qt.NoModifier, "\r") == b"\r"


def test_ordinary_text_passes_through_as_utf8():
    assert encode_key(Qt.Key_A, Qt.NoModifier, "a") == b"a"
    assert encode_key(0, Qt.NoModifier, "é") == "é".encode()


def test_alt_is_escape_then_the_key():
    assert encode_key(Qt.Key_B, Qt.AltModifier, "b") == b"\x1bb"


def test_an_unhandled_key_defers_to_qt():
    """Returning b"" lets Qt do its own thing — tab-focus, shortcuts — instead of the terminal
    swallowing every key in the application."""
    assert encode_key(Qt.Key_F5, Qt.NoModifier, "") == b""


# -- screen ------------------------------------------------------------------ #
def test_output_lands_on_the_screen(app):
    v = _view(app)
    v.feed(b"GINI-r1 $ route show\r\n")
    assert v._screen.display[0].startswith("GINI-r1 $ route show")


def test_colour_and_bold_survive(app):
    """The gRouter CLI and tcpdump both colour their output; losing it loses the meaning."""
    v = _view(app)
    v.feed(b"\033[1;32mUP\033[0m")
    cell = v._screen.buffer[0][0]
    assert cell.fg == "green" and cell.bold


def test_a_utf8_character_split_across_frames_is_not_corrupted(app):
    """WebSocket frames do not respect character boundaries. A str-based stream would produce
    mojibake at exactly the wrong moment — mid-capture."""
    v = _view(app)
    raw = "héllo".encode()
    v.feed(raw[:2])
    v.feed(raw[2:])
    assert v._screen.display[0].startswith("héllo")


def test_there_is_scrollback(app):
    """Reading back through `tcpdump` output IS the exercise in several chapters."""
    v = _view(app)
    for i in range(200):
        v.feed(b"line %d\r\n" % i)
    assert len(v._screen.history.top) > 0, "no scrollback: earlier output is unreachable"


def test_the_wheel_scrolls_back_and_new_output_returns_to_live(app):
    v = _view(app)
    for i in range(200):
        v.feed(b"line %d\r\n" % i)
    v._scroll = 10
    v.feed(b"new\r\n")
    assert v._scroll == 0, "output arrived while scrolled back and the view did not follow it"


def test_resize_reports_new_geometry_for_the_pty(app):
    """The PTY has to be told, or `top` and `less` paint to the wrong width."""
    v = _view(app)
    seen = []
    v.size_changed.connect(lambda c, r: seen.append((c, r)))
    v.resize(320, 160)
    v._refit()                      # offscreen defers resizeEvent; _refit is what it calls
    assert seen, "resize never reported new geometry"
    cols, rows = seen[-1]
    assert cols >= 20 and rows >= 4
    assert (cols, rows) == (v._screen.columns, v._screen.lines), (
        "told the PTY a size the emulator is not using")


def test_a_font_change_also_refits(app):
    """Bigger font, fewer columns — the PTY has to hear about it too, or output wraps at a width
    nothing on screen is using."""
    v = _view(app)
    v._refit()
    before = v._screen.columns
    seen = []
    v.size_changed.connect(lambda c, r: seen.append((c, r)))
    v.set_font_size(22)
    assert v._screen.columns < before, "font grew but the grid did not shrink"
    assert seen, "font change never reported new geometry to the PTY"


def test_reset_clears_the_previous_element(app):
    """Switching elements reuses this widget; the last router's output must not appear under the
    next host's name."""
    v = _view(app)
    v.feed(b"secrets from r1\r\n")
    v.reset()
    assert v._screen.display[0].strip() == ""


def test_a_key_press_emits_bytes(app):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    v = _view(app)
    sent = []
    v.key_bytes.connect(sent.append)
    v.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, "\x03"))
    assert sent == [b"\x03"]


def test_default_geometry_is_a_sane_terminal(app):
    v = TerminalView()
    assert (v._screen.columns, v._screen.lines) == (DEFAULT_COLS, DEFAULT_ROWS)


def test_it_paints_without_raising(app):
    """paintEvent touches every cell and the colour tables; a raise here is a blank pane."""
    from PySide6.QtGui import QPixmap
    v = _view(app)
    v.feed(b"\033[31mred\033[0m \033[1mbold\033[0m plain\r\nsecond line\r\n")
    v.render(QPixmap(v.size()))
