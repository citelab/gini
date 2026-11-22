"""The terminal's scrollback holds everything the element printed, not just what tmux passed on.

Reported: "scrolling is broken" in the Terminal pane. Every element's terminal runs inside tmux,
and tmux collapses output that arrives faster than it redraws into a jump to the final screen:
`seq 1 300` reached the pane as one `CSI 30 S` plus lines 272-300 (captured from a real machine
image). Lines 1-271 lived only in tmux's own history, so the scroll bar scrolled over blank lines.
The pane now asks tmux for that history (`capture-pane`) when the student starts scrolling back.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

from gini.ui.terminal_panel import HISTORY_CMD
from gini.ui.terminal_view import TerminalView, rows_from_capture


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _text(row, cols=100):
    return "".join(row[x].data for x in range(cols)).rstrip()


# What tmux sent for `seq 1 300` on a 100x30 pane: a whole-screen scroll, then the last 29 lines.
TMUX_SKIPPED = (b"\x1b[Hroot@m1:~# seq 1 300\r\n\x1b[30S\x1b[H"
                + b"".join(b"%d\x1b[K\r\n" % n for n in range(272, 301)) + b"root@m1:~# ")
# ...and what tmux's own history held for the same session.
TMUX_HISTORY = ("root@m1:~# \x1b[31mprintf red\x1b[39m\n"
                + "".join(f"{n}\n" for n in range(1, 272)))


def _view(app, source=None):
    v = TerminalView()
    v.resize(900, 600)
    v._screen.resize(30, 100)
    v.feed(TMUX_SKIPPED)
    if source is not None:
        v.set_history_source(source)
    return v


def _settle(app, v, secs=3.0):
    end = time.time() + secs
    while time.time() < end and v._history_busy:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _numbers(v):
    return [int(t) for t in (_text(r) for r in v._screen.history.top) if t.isdigit()]


def test_without_tmuxs_history_the_skipped_lines_are_simply_not_there(app):
    v = _view(app)
    v._set_scroll(10_000)
    assert _numbers(v) == []          # the reported bug: nothing but blank lines to scroll over


def test_scrolling_back_fetches_tmuxs_history_and_shows_every_line(app):
    calls = []
    v = _view(app, lambda: calls.append(1) or TMUX_HISTORY)
    v._set_scroll(3)
    _settle(app, v)
    assert calls == [1]
    assert _numbers(v) == list(range(1, 272))
    assert v._bar.maximum() == len(v._screen.history.top)
    assert v.scrolled_back() == 3


def test_colour_survives_the_fetch(app):
    v = _view(app, lambda: TMUX_HISTORY)
    v._set_scroll(1)
    _settle(app, v)
    first = v._screen.history.top[0]
    assert first[11].data == "p" and first[11].fg == "red"


def test_the_first_notch_after_a_reattach_fetches_even_with_nothing_local(app):
    """Right after switching back to an element the local scrollback is empty, so the scroll is
    clamped at 0 -- it must still fetch, and land where the student asked."""
    v = TerminalView()
    v.resize(900, 600)
    v._screen.resize(30, 100)
    v.set_history_source(lambda: TMUX_HISTORY)
    v._set_scroll(6)
    _settle(app, v)
    assert len(_numbers(v)) == 271 and v.scrolled_back() == 6


def test_one_fetch_per_scroll_back_and_a_fresh_one_next_time(app):
    calls = []
    v = _view(app, lambda: calls.append(1) or TMUX_HISTORY)
    v._set_scroll(3); _settle(app, v)
    v._set_scroll(9); v._set_scroll(4)
    assert len(calls) == 1                 # scrolling within a scroll-back does not refetch
    v.to_bottom(); v._set_scroll(3); _settle(app, v)
    assert len(calls) == 2                 # leaving the live screen again reads it afresh


def test_history_for_the_previous_element_is_never_shown(app):
    v = _view(app, lambda: (time.sleep(0.3), TMUX_HISTORY)[1])
    v._set_scroll(3)
    v.set_history_source(lambda: "")       # re-pointed while the fetch was in flight
    v.reset()
    _settle(app, v, 1.0)
    time.sleep(0.4); app.processEvents()
    assert _numbers(v) == []


def test_a_failed_fetch_keeps_what_passed_through(app):
    def boom():
        raise RuntimeError("docker exec failed")
    v = _view(app, boom)
    before = len(v._screen.history.top)
    v._set_scroll(3)
    _settle(app, v)
    assert len(v._screen.history.top) == before and not v._history_busy


def test_capture_rows_wrap_like_the_screen_and_ignore_the_trailing_newline():
    rows = rows_from_capture("a\n" + "x" * 25 + "\n", 10)
    assert ["".join(r[x].data for x in range(10)).rstrip() for r in rows] == \
        ["a", "x" * 10, "x" * 10, "x" * 5]
    assert rows_from_capture("", 80) == []


def test_the_command_reads_the_history_size_before_capturing():
    """With an empty history `capture-pane -S -N -E -1` returns the screen's FIRST LINE, which
    would then appear twice; the size check returns nothing instead."""
    assert "#{history_size}" in HISTORY_CMD
    assert HISTORY_CMD.index("history_size") < HISTORY_CMD.index("capture-pane")
    assert "-E -1" in HISTORY_CMD and "-t =gini:" in HISTORY_CMD


def test_the_panel_points_the_source_at_the_selected_element(app, tmp_path):
    from gini.ui.terminal_panel import TerminalPanel
    from gini.ui.theme import ThemeManager
    seen = []
    p = TerminalPanel(ThemeManager(app), history_fn=lambda dev, cmd: seen.append((dev, cmd)) or "")
    p.active._embed("http://127.0.0.1:37601/", "M2", "")
    assert p.active._view._history_source is not None
    p.active._view._history_source()
    assert seen == [("M2", HISTORY_CMD)]
    p.active._detach()
    assert p.active._view._history_source is None
