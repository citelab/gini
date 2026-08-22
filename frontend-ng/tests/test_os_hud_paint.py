"""The OS HUD's paint path — the one place nothing covered, and both regressions lived there.

Every other test around the kernel board is on the pure domain layer underneath it. That layer
was green through two shipped bugs:

  * the scrub timeline vanished, because a paintEvent that raises makes Qt abandon the rest of
    the widget — including the controls at the bottom
  * edges drew as spaghetti, because they were always anchored bottom-to-top, so an upward call
    left the bottom of its source and entered the top of a box above it

Neither was catchable without actually painting. These tests render the widget into a QPixmap and
assert it survives — deliberately including the awkward frames: nothing at all, a kernel with no
board, four hundred events, a trace armed, a scrub in progress, and a frame naming blocks that do
not exist on the board.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain.kernel_board import Frame, Hop, Window, parse
from gini.domain.os_events import OsEvent

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtGui = pytest.importorskip("PySide6.QtGui")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _hud(app, theme, **kw):
    from gini.ui.os_hud import OsHud
    h = OsHud(None, theme)
    h.resize(640, 560)
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def _paint(h):
    """Render for real. `render()` runs the whole paintEvent, so anything that throws, throws
    here — which is the entire point of this file."""
    pm = QtGui.QPixmap(h.size())
    pm.fill()
    h.render(pm)
    return pm


BOARD = """BOARDN 14
BSUB 0 user 700
BSUB 1 trap 20
BSUB 9 bcache 20
BSUB 10 disk 120
BEDGE 1 2 1000
BEDGE 2 5 300
BEDGE 5 7 260
BEDGE 7 9 600
BEDGE 9 10 12
BEOBS 1 11 180
BEOBS 11 12 200
BDOOR 1000 3 120
BUSER 210000 1123
BSAMP 840
BTRAIL 6 0 0 11 0 10 0
"""


def _frame(second=None):
    w = Window()
    w.add(parse(BOARD), 100.0)
    return w.add(parse(second or BOARD.replace("700", "1400").replace("1000", "2284")), 110.0)


def _events(n, lane="fs"):
    return [OsEvent(seq=i, pid=7, lane=lane, kind="read") for i in range(1, n + 1)]


# -- the frames that break things -------------------------------------------------------------- #
def test_paints_with_nothing_at_all(app, theme):
    """A HUD opened before any poll lands. Historically the emptiest path is the crashiest."""
    _paint(_hud(app, theme))


def test_paints_a_kernel_with_no_board(app, theme):
    h = _hud(app, theme, stale=True)
    _paint(h)


def test_paints_a_real_frame(app, theme):
    h = _hud(app, theme)
    h.set_frame(_frame(), _events(12), "user")
    _paint(h)


def test_paints_a_dense_frame(app, theme):
    """400 events is the window cap: every lane switches from dots to density ticks here."""
    h = _hud(app, theme)
    h.set_frame(_frame(), _events(400), "disk")
    _paint(h)


def test_paints_with_an_armed_trace(app, theme):
    """The overlay draws a path ACROSS the board, including a hop that doubles back."""
    f = _frame()
    f.armed = True
    f.path = tuple(Hop(seq=900 + i, src=a, dst=b, pid=7) for i, (a, b) in enumerate(
        [("user", "trap"), ("trap", "syscall"), ("syscall", "file"), ("file", "inode"),
         ("inode", "bcache"), ("bcache", "disk"), ("disk", "bcache"), ("bcache", "user")]))
    h = _hud(app, theme)
    h.set_frame(f, _events(20), "disk")
    _paint(h)


def test_paints_when_the_marker_is_in_trap_code(app, theme):
    """"trap" is not one of the drawn blocks; the marker lands on the doors band instead."""
    h = _hud(app, theme)
    h.set_frame(_frame(), _events(5), "trap")
    _paint(h)


def test_paints_a_frame_naming_blocks_that_are_not_on_the_board(app, theme):
    """A newer kernel could report a subsystem this build does not draw. It must be ignored, not
    fatal — the board is a fixed hand-authored layout and will always lag the kernel."""
    f = _frame()
    f.edges[("nonesuch", "bcache")] = 5
    f.edges[("bcache", "alsonot")] = 7
    f.blocks["nonesuch"] = 5
    h = _hud(app, theme)
    h.set_frame(f, _events(3), "nonesuch")
    _paint(h)


def test_paints_with_a_selection_and_a_focus(app, theme):
    from gini.ui.os_hud import OsHud
    h = _hud(app, theme)
    h.set_frame(_frame(), _events(9), "user")
    _paint(h)                                    # populate _hit
    for name in ("bcache", "asked", "seized", "disk"):
        h.set_focus_lanes(name)
        _paint(h)
    h.set_focus_lanes(None)
    h.set_focus_pid(7)
    _paint(h)
    assert isinstance(h, OsHud)


def test_paints_with_the_swimlanes_collapsed(app, theme):
    h = _hud(app, theme)
    h.set_frame(_frame(), _events(9), "user")
    h.set_lane("xray", False)
    _paint(h)
    h.set_lane("board", False)                   # both off: still must not throw
    _paint(h)


# -- the regressions, pinned ------------------------------------------------------------------- #
def test_the_scrub_timeline_survives_a_board_that_throws(app, theme, monkeypatch):
    """THE regression. Qt abandons the rest of paintEvent when it raises, so a single bad frame
    used to take the timeline down with it — and a missing timeline reads as "the recorder is
    broken" rather than "one block failed to draw". The controls must outlive the content."""
    from gini.ui.hud import HudHistory
    from gini.ui.os_hud import OsHud

    hist = HudHistory(retain_s=120.0)
    hist.push((_frame(), _events(4), "user"), 1, 100.0)
    hist.push((_frame(), _events(5), "user"), 2, 101.0)

    h = _hud(app, theme)
    h.set_history(hist)
    h.set_frame(_frame(), _events(5), "user")

    painted = {}
    real_scrub = OsHud._paint_scrub
    monkeypatch.setattr(OsHud, "_paint_scrub",
                        lambda self, p: (painted.__setitem__("yes", True), real_scrub(self, p))[1])
    monkeypatch.setattr(OsHud, "_paint_board",
                        lambda self, p, y: (_ for _ in ()).throw(RuntimeError("boom")))

    _paint(h)                                    # must NOT propagate
    assert painted.get("yes"), "the timeline did not paint when the board failed"
    assert "boom" in h.paint_error                # and the failure is surfaced, not swallowed


def test_scrubbing_paints(app, theme):
    from gini.ui.hud import HudHistory
    hist = HudHistory(retain_s=120.0)
    for i in range(5):
        hist.push((_frame(), _events(3 + i), "user"), i, 100.0 + i)
    h = _hud(app, theme)
    h.set_history(hist)
    h._scrub_to(120.0)                           # drag the playhead into the past
    _paint(h)
    h.go_live()
    _paint(h)


# -- the recorder controls, driven with a real mouse ------------------------------------------- #
def _history(n=60):
    """A history that really moves: n snapshots, one a second, every one a change."""
    from gini.ui.hud import HudHistory
    b = ("BOARDN 14\nBSUB 0 user {u}\nBEDGE 1 2 {e}\n"
         "BDOOR {e} 0 0\nBUSER {e} {e}\nBSAMP 40\n")
    hist = HudHistory(retain_s=120.0)
    w = Window()
    w.add(parse(b.format(u=700, e=1000)), 0.0)
    for i in range(1, n):
        f = w.add(parse(b.format(u=700 * (i + 1), e=1000 * (i + 1))), float(i))
        hist.push((f, _events(3), "user"), i, float(i))
    return hist


def _hud_with_history(app, theme):
    h = _hud(app, theme)
    hist = _history()
    h.set_history(hist)
    h.set_frame(*hist.latest())
    _paint(h)                                    # a paint is what populates the hit rects
    return h, hist


def _press(h, x, y):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    h.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(x, y),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def test_clicking_the_timeline_starts_a_scrub(app, theme):
    """THE regression this file was extended for. The scrub tail was once spliced into
    mouseDoubleClickEvent by accident, so a single click on the timeline did nothing at all — and
    because the board still painted perfectly, nothing looked broken. Paint tests cannot see this;
    only driving the mouse can."""
    from gini.ui.hud import timeline_rect
    h, hist = _hud_with_history(app, theme)
    tl = timeline_rect(h.width(), h.height())
    _press(h, tl.left() + tl.width() * 0.3, tl.center().y())
    assert h._scrub_drag, "a single click on the timeline did not begin a scrub"
    assert h.scrubbing, "the playhead did not move into the past"
    assert h._scrub_t < hist.t_end


def test_dragging_the_playhead_moves_it(app, theme):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from gini.ui.hud import timeline_rect
    h, _ = _hud_with_history(app, theme)
    tl = timeline_rect(h.width(), h.height())
    _press(h, tl.left() + tl.width() * 0.2, tl.center().y())
    first = h._scrub_t
    h.mouseMoveEvent(QMouseEvent(QEvent.MouseMove,
                                 QPointF(tl.left() + tl.width() * 0.6, tl.center().y()),
                                 Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    assert h._scrub_t > first, "dragging right did not advance the playhead"
    h.mouseReleaseEvent(None)
    assert not h._scrub_drag


def test_the_live_chip_returns_to_now(app, theme):
    from gini.ui.hud import live_rect, timeline_rect
    h, _ = _hud_with_history(app, theme)
    tl = timeline_rect(h.width(), h.height())
    _press(h, tl.left() + tl.width() * 0.3, tl.center().y())
    assert h.scrubbing
    lr = live_rect(h.width(), h.height())
    _press(h, lr.center().x(), lr.center().y())
    assert not h.scrubbing, "the LIVE chip did not return the HUD to now"


def test_clicking_a_block_selects_it_and_does_not_scrub(app, theme):
    h, _ = _hud_with_history(app, theme)
    r = h._hit["bcache"]
    _press(h, r.center().x(), r.center().y())
    assert h._focus == "bcache"
    assert not h._scrub_drag                     # a board click must not touch the recorder
    _press(h, r.center().x(), r.center().y())    # clicking again clears it
    assert h._focus == ""


def test_double_clicking_a_block_asks_for_its_source(app, theme):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    h, _ = _hud_with_history(app, theme)
    got = []
    h.open_source.connect(lambda name, files: got.append((name, files)))
    r = h._hit["bcache"]
    h.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick,
                                        QPointF(r.center().x(), r.center().y()),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert got and got[0][0] == "bcache" and got[0][1] == ["kernel/bio.c"]


def test_edges_leave_the_correct_side_of_a_block(app, theme):
    """The other regression: edges were always drawn bottom-to-top, so an upward call left the
    bottom of its source and entered the top of a box ABOVE it, producing crossings that looked
    like they encoded something and encoded nothing."""
    from PySide6.QtCore import QPointF, QRectF
    from gini.ui.os_hud import OsHud

    r = QRectF(0, 100, 100, 20)                  # centre (50, 110)
    assert OsHud._exit_point(r, QPointF(50, 400)).y() == 120      # downward -> bottom edge
    assert OsHud._exit_point(r, QPointF(50, -400)).y() == 100     # upward   -> TOP edge
    assert OsHud._exit_point(r, QPointF(900, 110)).x() == 100     # sideways -> side
    assert OsHud._exit_point(r, r.center()) == r.center()         # degenerate: no crash
