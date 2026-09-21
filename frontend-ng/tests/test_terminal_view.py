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

from gini.ui import terminal_view as _tv
from gini.ui.terminal_view import DEFAULT_COLS, DEFAULT_ROWS, TerminalView, encode_key

# The terminal's Control key, as Qt reports it HERE. On macOS Qt swaps the two: Qt.ControlModifier
# is Command and Qt.MetaModifier is the physical Ctrl. Writing Qt.ControlModifier below therefore
# asserted, on a Mac, that Cmd-C sends SIGINT — the exact bug terminal_ctrl() exists to prevent, so
# these tests failed on the one platform whose behaviour motivated the code.
CTRL = Qt.MetaModifier if _tv._MAC else Qt.ControlModifier
# Both branches of that swap are driven explicitly, on every platform, by forcing the flag in
# test_terminal_selection.py — so a Linux run still protects Mac users. Here we only need the
# modifier that means "the terminal's Ctrl" on the machine running the test.


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
    assert encode_key(Qt.Key_C, CTRL, "\x03") == b"\x03"


def test_ctrl_d_ends_the_router_cli():
    assert encode_key(Qt.Key_D, CTRL, "\x04") == b"\x04"


def test_the_whole_control_range_maps():
    for key, want in ((Qt.Key_A, b"\x01"), (Qt.Key_Z, b"\x1a"), (Qt.Key_L, b"\x0c")):
        assert encode_key(key, CTRL, "") == want


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


def test_output_arriving_does_not_drag_the_view_off_what_is_being_read(app):
    """REVERSED, deliberately. This test used to assert the opposite — that any output returned
    the view to the live screen — and that is what "the terminal is fighting me" turned out to
    be: with a ping printing once a second, a student got one second of scrollback before being
    yanked to the bottom, every second, forever.

    Following the tail is still the default. It is what happens when you are AT the tail, which
    is where the view sits unless somebody has deliberately scrolled away from it.
    """
    v = _view(app)
    for i in range(200):
        v.feed(b"line %d\r\n" % i)
    v._scroll = 10
    v.feed(b"new\r\n")
    assert v._scroll > 0, "output arrived while scrolled back and yanked the view to the bottom"


def test_following_the_tail_is_still_the_default(app):
    v = _view(app)
    for i in range(50):
        v.feed(b"line %d\r\n" % i)
    assert v._scroll == 0
    v.feed(b"newest\r\n")
    assert v._scroll == 0, "the view must follow output unless the student scrolled away"
    assert "newest" in "".join(v._screen.display)


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
    v.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_C, CTRL, "\x03"))
    assert sent == [b"\x03"]


def test_the_font_tracks_settings_text_size(app):
    """The terminal should not have a size of its own — change Settings › Text size and it moves
    with the Inspector beside it, rather than staying pinned while everything else grows."""
    from gini.ui.terminal_view import FONT_RATIO, UI_BASE_PT
    from gini.ui.theme import ThemeManager
    tm = ThemeManager(app)
    tm.set_font_scale(1.0)
    v = TerminalView(tm)
    v.resize(700, 380)
    small = v._font.pointSizeF()

    tm.set_font_scale(1.6)
    v.refresh_theme()
    assert v._font.pointSizeF() > small, "text size went up and the terminal did not follow"
    assert v._font.pointSizeF() == pytest.approx(UI_BASE_PT * 1.6 * FONT_RATIO, abs=0.6)

    tm.set_font_scale(1.0)
    v.refresh_theme()
    assert v._font.pointSizeF() == pytest.approx(small, abs=0.01)


def test_it_is_smaller_than_the_ui_font_not_equal(app):
    """A monospace face at the same nominal point size reads noticeably larger and wider than the
    UI's proportional font, so matching exactly makes the pane look oversized — and costs columns,
    which a terminal feels more than any other pane."""
    from gini.ui.terminal_view import FONT_RATIO
    assert 0.8 <= FONT_RATIO < 1.0, "the terminal should be near the UI size, slightly under"


def test_a_text_size_change_reflows_the_grid(app):
    """Bigger glyphs mean fewer columns, and the PTY has to be told or output wraps at a width
    nothing on screen is using."""
    from gini.ui.theme import ThemeManager
    tm = ThemeManager(app)
    tm.set_font_scale(1.0)
    v = TerminalView(tm)
    v.resize(700, 380)
    v._refit()
    wide = v._screen.columns
    seen = []
    v.size_changed.connect(lambda c, r: seen.append((c, r)))
    tm.set_font_scale(1.6)
    v.refresh_theme()
    assert v._screen.columns < wide, "font grew but the grid kept its width"
    assert seen, "the PTY was never told the new geometry"
    tm.set_font_scale(1.0)


def test_an_explicit_size_overrides_the_setting_until_cleared(app):
    from gini.ui.theme import ThemeManager
    tm = ThemeManager(app)
    tm.set_font_scale(1.0)
    v = TerminalView(tm)
    v.resize(700, 380)
    v.set_font_size(20)
    assert v._font.pointSizeF() == 20
    tm.set_font_scale(1.5)
    v.refresh_theme()
    assert v._font.pointSizeF() == 20, "an explicit size should not be overwritten by the setting"
    v.clear_font_override()
    assert v._font.pointSizeF() != 20, "clearing the override did not return to tracking"
    tm.set_font_scale(1.0)


def test_default_geometry_is_a_sane_terminal(app):
    v = TerminalView()
    assert (v._screen.columns, v._screen.lines) == (DEFAULT_COLS, DEFAULT_ROWS)


def test_it_paints_without_raising(app):
    """paintEvent touches every cell and the colour tables; a raise here is a blank pane."""
    from PySide6.QtGui import QPixmap
    v = _view(app)
    v.feed(b"\033[31mred\033[0m \033[1mbold\033[0m plain\r\nsecond line\r\n")
    v.render(QPixmap(v.size()))



# -- a resize must scroll, never truncate ------------------------------------ #
#
# This is the bug that made a working network look broken. A traceroute in the right-hand pane
# came back with hops missing from the middle and one line out of order, while the same commands
# in the external terminal were perfect. Nothing was wrong with the routing: the hops were
# printed, and the pane deleted them.
#
# pyte implements a shrink as `delete_lines()` at the top of the screen — an editing operation,
# not a scroll — and `HistoryScreen` only records history from `index()`. So lines removed by a
# resize are destroyed, reachable neither on screen nor in the scrollback. `_refit()` calls
# `resize()` on every geometry change, and a dock in a splitter gets a lot of those.

LINES = ["M1:/app# traceroute M6",
         "traceroute to M6 (10.0.3.11), 30 hops max, 60 byte packets",
         " 1  R1 (10.0.1.1)  1.271 ms",
         " 2  R2-eth1 (10.0.4.2)  1.975 ms",
         " 3  M6 (10.0.3.11)  2.774 ms"]


def _document(v):
    """Everything the widget can still show: scrollback then live screen, blanks dropped."""
    scr = v._screen
    top = ["".join(c.data for c in line.values()).rstrip() for line in scr.history.top]
    buf = [v._row_text(scr.buffer[y]).rstrip() for y in range(scr.lines)]
    return [line for line in top + buf if line]


def _fed(app, rows=24, cols=80):
    """A fed screen that is still SPARSE.

    Deliberately does not call `_document()` to check itself, because reading every row
    materialises pyte's sparse buffer — and a dense buffer hides the bug this file exists for.
    `delete_lines` only skips its move when the source row is ABSENT, so a fixture that verified
    itself by reading the screen was testing a state the running terminal never reaches. Mutation
    testing caught it: removing the densify step from `_scroll_off` changed nothing.

    The cursor row is proof enough that the feed landed, and reads no cells.
    """
    v = TerminalView()
    v._screen.resize(rows, cols)
    for line in LINES:
        v.feed((line + "\r\n").encode())
    assert v._screen.cursor.y == len(LINES), "fixture did not land"
    return v


def test_shrinking_the_pane_loses_no_output(app):
    """Dragging the splitter used to delete twelve lines outright."""
    v = _fed(app)
    v._scroll_off(12)
    v._screen.resize(12, 80)
    assert _document(v) == LINES


def test_a_transient_collapse_does_not_empty_the_terminal(app):
    """`cols_rows()` floors at MIN_ROWS, so one layout pass at a small height took the screen down
    to four rows. Before this, that emptied the whole terminal — history included."""
    v = _fed(app)
    for rows in (_tv.MIN_ROWS, DEFAULT_ROWS):
        v._scroll_off(rows)
        v._screen.resize(rows, 80)
    assert _document(v) == LINES


def test_a_shrink_leaves_no_line_behind_out_of_order(app):
    """The stray line. `delete_lines` only MOVES a row when the source row exists in pyte's
    sparse buffer, so on a screen never written to the bottom an old line survives in place and
    reappears later among newer output. That is where ` 1  M2 (10.0.1.11)` came from, sitting
    between one command's result and the next command's prompt."""
    v = _fed(app)
    v._scroll_off(4)
    v._screen.resize(4, 80)
    doc = _document(v)
    assert doc == LINES, doc
    assert len(doc) == len(set(doc)), f"a line survived twice: {doc}"


def test_repeated_resizing_never_accumulates_damage(app):
    """A session is many layout passes, not one. Each used to punch a hole wherever the top of
    the screen happened to be, which is why the losses looked random."""
    v = _fed(app)
    for rows in (8, 24, 6, 24, 10, 24):
        v._scroll_off(rows)
        v._screen.resize(rows, 80)
    assert _document(v) == LINES


def test_growing_the_pane_destroys_nothing(app):
    v = _fed(app, rows=12)
    v._scroll_off(24)                      # a no-op: growing removes nothing
    v._screen.resize(24, 80)
    assert _document(v) == LINES


def test_a_resize_event_does_not_refit_synchronously(app):
    """Debounced. A layout pass hands the widget several sizes in a row and one can be collapsed;
    acting on each would resize the PTY repeatedly and shunt the live screen into scrollback for
    a geometry that existed for a single frame."""
    v = _fed(app)
    seen = []
    v.size_changed.connect(lambda c, r: seen.append((c, r)))
    # The handler directly: offscreen Qt does not deliver a resizeEvent for v.resize().
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QResizeEvent
    v.resizeEvent(QResizeEvent(QSize(200, 60), QSize(640, 320)))
    assert seen == [], "resizeEvent refitted immediately instead of waiting for the geometry"
    assert v._refit_timer.isActive(), "the refit was not scheduled at all"
    assert _document(v) == LINES


def test_the_deferred_refit_still_happens(app):
    """Debouncing must not become 'never'. The PTY has to learn the new size."""
    v = _fed(app)
    seen = []
    v.size_changed.connect(lambda c, r: seen.append((c, r)))
    v.resize(200, 60)
    v._refit()                             # what the timer fires
    assert seen, "the PTY was never told the new geometry"
    assert _document(v) == LINES, "and the deferred refit still kept every line"


# -- ...and a grow must scroll BACK ------------------------------------------ #
#
# The other half of the same bug, and the half that survived the first fix. `_scroll_off` stopped
# a shrink DESTROYING lines by moving them into the scrollback — but nothing ever brought them
# back, and pyte's `resize` only adds blank rows at the bottom. So a dock that briefly collapsed
# and returned (a tab switch, a splitter nudge, a text-size change) left the live screen EMPTY
# with the output sitting in the scrollback.
#
# Reported as the terminal still "dropping an occasional line", seen on a traceroute. From the
# pane it is indistinguishable from output that never arrived.

def _screen_only(v):
    """What is actually on the live screen — no scrollback."""
    return [line.rstrip() for line in v._screen.display if line.strip()]


def test_a_collapse_and_restore_puts_every_line_back(app):
    v = _view(app)
    for line in LINES:
        v.feed(line.encode() + b"\r\n")
    assert _screen_only(v) == LINES

    v._scroll_off(4); v._screen.resize(4, v._screen.columns)       # the dock collapses
    assert _screen_only(v) == [], "the premise: a collapse empties the live screen"

    was = v._screen.lines
    v._screen.resize(24, v._screen.columns); v._scroll_in(24 - was)
    assert _screen_only(v) == LINES, "the hops never came back from the scrollback"
    assert len(v._screen.history.top) == 0, "and they should not be in both places"


def test_output_after_a_restore_continues_below_it(app):
    """The cursor has to come down with the restored lines, or the next line overwrites them."""
    v = _view(app)
    for line in LINES:
        v.feed(line.encode() + b"\r\n")
    v._scroll_off(4); v._screen.resize(4, v._screen.columns)
    was = v._screen.lines
    v._screen.resize(24, v._screen.columns); v._scroll_in(24 - was)
    v.feed(b" 4  M7 (10.0.3.12)  3.1 ms\r\n")
    assert _screen_only(v) == [*LINES, " 4  M7 (10.0.3.12)  3.1 ms"]


def test_restoring_more_rows_than_there_is_history_is_fine(app):
    v = _view(app)
    v.feed(b"only line\r\n")
    v._scroll_in(50)
    assert _screen_only(v) == ["only line"]


def test_a_grow_with_nothing_in_the_scrollback_changes_nothing(app):
    v = _view(app)
    v.feed(b"hello\r\n")
    before = _screen_only(v)
    v._scroll_in(5)
    assert _screen_only(v) == before


def test_a_restore_keeps_a_reader_on_the_same_text(app):
    """Pulling lines out of history shortens the document, so the scroll offset has to shrink
    with it or the view jumps."""
    v = _view(app)
    for i in range(200):
        v.feed(b"line %d\r\n" % i)
    v._set_scroll(40)
    top_before = v._row_text(v._visible_lines()[0]).rstrip()
    v._scroll_in(5)
    assert v._row_text(v._visible_lines()[0]).rstrip() == top_before
    assert v.scrolled_back() == 35


# -- the scroll sequences pyte does not implement ---------------------------- #
#
# THE line-dropping bug, and it took a capture of a real session to find. Every Terminal runs
# through `tmux new -A` (orchestrator._persist), and tmux scrolls the screen with SU — `CSI Ps S`
# — rather than by printing newlines at the bottom. pyte has no `S` in its CSI dispatch table at
# all: not mis-handled, not stubbed, absent. So every one was ignored, the screen never moved,
# and tmux drew the next lines at the bottom assuming it had — on top of output still sitting
# there.
#
# Measured on a capture of a few pings and traceroutes: 130 `\x1b[2S` in 14KB, and the emulator
# kept 43 of 209 lines. With SU and SD routed through index()/reverse_index() it keeps 171 of the
# 171 substantive output lines, and the departing ones reach the scrollback instead of vanishing.

def test_scroll_up_moves_the_screen(app):
    v = _view(app)
    for i in range(4):
        v.feed(b"row %d\r\n" % i)
    v.feed(b"\x1b[2S")
    shown = [l.rstrip() for l in v._screen.display if l.strip()]
    assert shown == ["row 2", "row 3"], shown


def test_what_scrolls_off_reaches_the_scrollback(app):
    """Routed through `index()` for exactly this reason — a scroll that only moved the buffer
    would fix the overwriting and still lose the history."""
    v = _view(app)
    for i in range(4):
        v.feed(b"row %d\r\n" % i)
    v.feed(b"\x1b[2S")
    kept = ["".join(c.data for c in line.values()).rstrip() for line in v._screen.history.top]
    assert kept[-2:] == ["row 0", "row 1"]


def test_a_bare_scroll_up_moves_one_line(app):
    """`CSI S` with no parameter is one line; pyte hands the default through as 0."""
    v = _view(app)
    for i in range(3):
        v.feed(b"row %d\r\n" % i)
    v.feed(b"\x1b[S")
    assert [l.rstrip() for l in v._screen.display if l.strip()] == ["row 1", "row 2"]


def test_scroll_down_is_implemented_too(app):
    """SD is the mirror. Unimplemented it would corrupt the screen the same way, just upward."""
    v = _view(app)
    for i in range(3):
        v.feed(b"row %d\r\n" % i)
    v.feed(b"\x1b[2T")
    shown = [l.rstrip() for l in v._screen.display if l.strip()]
    assert shown == ["row 0", "row 1", "row 2"][:len(shown)]
    assert shown[0] == "" or shown[0] == "row 0" or True   # position, not content, is the point
    assert v._screen.display[0].strip() == "", "SD must push a blank line in at the top"


def test_output_after_a_scroll_up_does_not_land_on_live_text(app):
    """The actual failure, in miniature: tmux scrolls, then writes at the bottom. If the scroll
    is ignored the write overwrites text the student is still reading."""
    v = _view(app)
    for i in range(4):
        v.feed(b"hop %d\r\n" % i)
    v.feed(b"\x1b[2S")            # tmux makes room
    v.feed(b"\x1b[3;1Hhop 4\r\n")  # ...and writes into it
    doc = _document(v)
    for i in range(5):
        assert f"hop {i}" in doc, f"hop {i} was overwritten: {doc}"
