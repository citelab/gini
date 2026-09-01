"""The kernel board's type: bigger, adjustable, and still inside its boxes.

The complaint was that this board's labels were 7pt and 8pt — smaller than anything else in
gBuilder — hard-coded at twelve call sites and unreachable from any setting.

Making type bigger here is not a one-line change, because the board is HAND-LAID-OUT at a fixed
640 x 560 and stays that size: the panel does not grow, so every box that holds a string has to
give way instead. `LANE_W = 150` was chosen when its caption was 7pt; at 13pt "the MMU checks" is
122px in a 110px box. So these tests do what reading the code cannot: they render the real widget,
record every `drawText` with the box and the font in force, and measure.

Two failure modes, and the second is the one that bites:

  * a string wider than the box it is drawn in — clipped, and easy to check;
  * two strings that each fit the same box and land on top of each other. `_chip` draws a label
    left and a value right in ONE rect, so "kernel only" and "disk · console · timer" both fit
    and still collide. A per-string width check sees nothing wrong.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from gini.domain.kernel_board import Frame
from gini.domain.os_events import OsEvent

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtGui = pytest.importorskip("PySide6.QtGui")
from PySide6.QtCore import QPoint, QRectF, Qt                    # noqa: E402
from PySide6.QtGui import QFontMetricsF, QPainter, QPixmap       # noqa: E402

#: Every size the Settings dialog offers, plus the sizes "Match text size" can produce.
OFFERED = (100, 110, 125, 150, 175)


# Parents are kept alive. A QWidget that goes out of scope in Python takes its C++ object with
# it, and the HUD parented to it dies too — which surfaces as "Internal C++ object already
# deleted" from inside an assertion, nowhere near the line that dropped the reference.
_ALIVE = []


def _parent(w=1200, h=900):
    p = QtWidgets.QWidget()
    p.resize(w, h)
    _ALIVE.append(p)
    return p


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _frame():
    """A busy board: every block used, both door counts, percentages in the wide blocks. The
    numbers matter — "310  56%" is a longer value than "3", and it is the long ones that collide
    with their labels."""
    return Frame(
        blocks={"syscall": 120, "proc": 40, "memory": 18, "file": 30, "inode": 22, "log": 9,
                "bcache": 310, "disk": 12, "console": 48, "plic": 51, "pipe": 3},
        blocks_obs={"console": 9, "plic": 4},
        resid={"bcache": 40, "proc": 18, "syscall": 9, "file": 5},
        edges={("syscall", "file"): 60, ("inode", "bcache"): 120, ("bcache", "disk"): 12},
        doors=(210, 14, 99), user_kinstr=8123, user_entries=1000, resid_n=67, span_s=10.0)


def _events():
    return [OsEvent(seq=i, pid=3, lane=lane, kind=kind, detail="")
            for i, (lane, kind) in enumerate(
                [("syscall", "exec"), ("proc", "fork"), ("memory", "page fault"),
                 ("fs", "open"), ("trap", "timer int")])]


def _render(theme, pct, frame=None, events=None):
    """Paint for real, recording every string with the box and font it was drawn with."""
    from gini.ui.os_hud import OsHud
    seen = []
    original = QPainter.drawText

    def spy(self, *a):
        try:
            if len(a) == 6:
                x, y, w, h, flags, text = a
                box = QRectF(x, y, w, h)
            elif len(a) == 3 and isinstance(a[0], QRectF):
                box, flags, text = a
            else:
                box, flags, text = QRectF(0, 0, 1e6, 1e6), 0, a[-1]
            if isinstance(text, str) and text.strip():
                seen.append((box, text, QFontMetricsF(self.font()).horizontalAdvance(text), flags))
        except Exception:                                        # noqa: BLE001
            pass
        return original(self, *a)

    QPainter.drawText = spy
    try:
        h = OsHud(_parent(), theme, scale_fn=lambda: pct)
        h.set_frame(frame if frame is not None else _frame(),
                    _events() if events is None else events)
        pm = QPixmap(h.size())
        pm.fill(Qt.black)
        h.render(pm, QPoint(0, 0))
        return h, seen
    finally:
        QPainter.drawText = original


# ---- the type is bigger, and it moves ------------------------------------------- #
def test_the_base_sizes_are_bigger_than_they_were(app, theme):
    """8 and 7 were the numbers, at twelve hard-coded call sites."""
    from gini.ui.os_hud import FONT_BODY, FONT_SMALL
    assert FONT_BODY > 8 and FONT_SMALL > 7


def test_a_larger_setting_really_draws_larger_type(app, theme):
    from gini.ui.os_hud import FONT_BODY
    from gini.ui.os_hud import OsHud
    parent = _parent()
    sizes = [OsHud(parent, theme, scale_fn=lambda p=p: p)._font(FONT_BODY).pointSizeF()
             for p in OFFERED]
    assert sizes == sorted(sizes) and sizes[0] < sizes[-1]


def test_it_follows_the_apps_text_size_when_asked_to(app, theme):
    """The original request: "connect them to the gBuilder system font size settings"."""
    from gini.ui import theme as _t
    from gini.ui.os_hud import FONT_BODY, OsHud
    h = OsHud(_parent(), theme, scale_fn=lambda: 0)          # 0 = match text size
    _t.manager._ACTIVE_SCALE = 1.0
    normal = h._font(FONT_BODY).pointSizeF()
    _t.manager._ACTIVE_SCALE = 1.32
    try:
        assert h._font(FONT_BODY).pointSizeF() > normal
    finally:
        _t.manager._ACTIVE_SCALE = 1.0


def test_a_chosen_percentage_ignores_the_apps_text_size(app, theme):
    """Otherwise the two multiply, and neither number on screen explains the board you got."""
    from gini.ui import theme as _t
    from gini.ui.os_hud import FONT_BODY, OsHud
    h = OsHud(_parent(), theme, scale_fn=lambda: 150)
    _t.manager._ACTIVE_SCALE = 1.0
    plain = h._font(FONT_BODY).pointSizeF()
    _t.manager._ACTIVE_SCALE = 1.32
    try:
        assert h._font(FONT_BODY).pointSizeF() == plain
        assert plain == round(1.5 * FONT_BODY)     # the percentage, and nothing else
    finally:
        _t.manager._ACTIVE_SCALE = 1.0


# ---- and the panel does NOT grow -------------------------------------------------- #
def test_the_panel_is_the_same_size_at_every_setting(app, theme):
    """THE constraint. An earlier attempt scaled the whole board, which made the panel 800x700
    and was not what was asked for. Only the type moves."""
    from gini.ui.os_hud import PANEL_H, PANEL_W
    for pct in OFFERED:
        h, _ = _render(theme, pct)
        assert (h.width(), h.height()) == (PANEL_W, PANEL_H), f"the panel grew at {pct}%"


# ---- nothing spills out of its box ------------------------------------------------ #
def _overflows(seen):
    return [(t, need, box.width()) for box, t, need, flags in seen
            if box.width() < 1e5 and need > box.width() - 1 and not (flags & Qt.TextWordWrap)]


@pytest.mark.parametrize("pct", OFFERED)
def test_every_string_fits_the_box_it_is_drawn_in(app, theme, pct):
    h, seen = _render(theme, pct)
    assert len(seen) > 40, "the board painted almost nothing; this test would pass on an empty HUD"
    bad = _overflows(seen)
    assert not bad, "clipped at {}%: {}".format(
        pct, "; ".join(f"{t!r} needs {n:.0f} in {w:.0f}" for t, n, w in bad[:5]))
    assert not h.paint_error


@pytest.mark.parametrize("pct", OFFERED)
def test_a_chips_label_and_its_value_do_not_land_on_each_other(app, theme, pct):
    """The failure a per-string check cannot see. `_chip` draws the label left-aligned and the
    value right-aligned in the SAME rect, so both fit and still overlap — which is how "kernel
    only" ended up written through "disk · console · timer"."""
    from gini.ui.os_hud import GAP, OsHud
    from PySide6.QtGui import QFont
    h = OsHud(_parent(), theme, scale_fn=lambda: pct)
    from gini.ui.os_hud import FONT_BODY, MACHINE_DIRECT, MACHINE_KERNEL, PAD, PANEL_W
    full = PANEL_W - 2 * PAD
    for (label, note), width in ((MACHINE_KERNEL, full * 0.44),
                                 (MACHINE_DIRECT, full - full * 0.44 - 12),
                                 (("your program", "812,300 instr/s"), full),
                                 (("block cache", "310  56%"), full * 0.72)):
        fitted = h._fit(h._font(FONT_BODY), label + note, width - 16 - GAP)
        both = QFontMetricsF(fitted).horizontalAdvance(label + note)
        assert both <= width - 16, (
            f"at {pct}%, {label!r} and {note!r} overlap: {both:.0f}px of ink in {width - 16:.0f}px")
        assert fitted.pointSizeF() >= 6.0


def test_shrinking_a_chip_converges(app, theme):
    """It was one proportional step, and font metrics are not linear in point size — so at 175%
    the machine bar still overlapped after the step that was supposed to separate it."""
    from gini.ui.os_hud import OsHud
    from PySide6.QtGui import QFont
    h = OsHud(_parent(), theme, scale_fn=lambda: 175)
    f = QFont(h.font().family(), 40)
    fitted = h._fit(f, "a long label and its value", 120)
    assert QFontMetricsF(fitted).horizontalAdvance("a long label and its value") <= 120


def test_a_box_that_holds_prose_grows_with_the_type(app, theme):
    """The direct lane is 150px because its caption was 7pt. It has to widen, into the kernel
    column — the blocks hold a word and a count and have slack; the caption is prose."""
    from gini.ui.os_hud import LANE_W, OsHud
    parent = _parent()
    small = OsHud(parent, theme, scale_fn=lambda: 100)._lane_w()
    large = OsHud(parent, theme, scale_fn=lambda: 175)._lane_w()
    assert small == LANE_W                       # unchanged at the size it was authored for
    assert large > small


def test_the_legend_wraps_rather_than_being_cut_off(app, theme):
    """It is the key to every visual encoding on the board — arrow width, block shade, the dashed
    edges — so it cannot be shortened. At 13pt it is 728px on a 608px board, and there are ~54px
    of vertical slack above the scrub timeline, which is four of these lines."""
    _, seen = _render(theme, 175)
    legend = [(box, t, need, fl) for box, t, need, fl in seen if t.startswith("arrow = calls")]
    assert legend, "the legend was not drawn"
    box, text, need, flags = legend[0]
    assert flags & Qt.TextWordWrap, "the legend must be allowed to wrap"
    assert box.height() >= need / box.width() * 0.9, "not enough room for the lines it needs"


def test_the_swimlane_names_are_not_clipped(app, theme):
    """"memory" in a 60px column was fine at 8pt and 68px at 13pt. The column is measured now,
    and the rails start after it."""
    from gini.domain.os_events import LANES
    for pct in OFFERED:
        _, seen = _render(theme, pct)
        drawn = {t for _b, t, _n, _f in seen}
        for lane in LANES:
            assert lane in drawn, f"lane {lane!r} missing at {pct}%"
        assert not _overflows([r for r in seen if r[1] in LANES])


def test_an_event_caption_stays_inside_its_rail(app, theme):
    """The first and last events sit exactly on the ends of the rail, so a caption merely centred
    on the dot runs left into the lane's own name and right off the panel."""
    from gini.ui.os_hud import PANEL_W
    for pct in OFFERED:
        _, seen = _render(theme, pct)
        for box, text, _need, _f in seen:
            if text in ("exec", "fork", "open", "page fault", "timer int"):
                assert box.left() >= 0, f"{text!r} starts off the panel at {pct}%"
                assert box.right() <= PANEL_W, f"{text!r} runs off the panel at {pct}%"


# ---- awkward frames still paint --------------------------------------------------- #
@pytest.mark.parametrize("pct", (100, 175))
def test_the_awkward_frames_survive_the_new_type(app, theme, pct):
    """Empty, one block, and four hundred events. The type changes what is measured on every
    paint, so the frames that had nothing to measure are worth re-checking."""
    many = [OsEvent(seq=i, pid=1, lane="fs", kind="read", detail="") for i in range(400)]
    for frame, events in ((Frame(), []), (Frame(span_s=10.0), many), (_frame(), many)):
        h, _ = _render(theme, pct, frame=frame, events=events)
        assert not h.paint_error, f"{h.paint_error} at {pct}%"


def test_the_dialog_offers_exactly_the_sizes_that_were_measured(app, theme):
    """If someone adds 200% here, the parametrised tests above must go with it — at 200% the
    legend and the door counts stop fitting the board at any arrangement."""
    from gini.app.context import Settings
    from gini.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(None, Settings())
    try:
        assert [d.os_scale.itemData(i) for i in range(d.os_scale.count())] == [0, *OFFERED]
        d.os_scale.setCurrentIndex(d.os_scale.findData(150))
        assert d.values()["os_hud_scale"] == 150
        d.os_scale.setCurrentIndex(0)
        assert d.values()["os_hud_scale"] == 0, "0 is a value (match text size), not a missing one"
    finally:
        d.deleteLater()


def test_the_setting_survives_a_restart():
    from gini.app.context import Settings
    from gini.app.paths import PERSISTED_KEYS
    assert Settings().os_hud_scale == 0
    assert "os_hud_scale" in PERSISTED_KEYS


def test_a_broken_setting_falls_back_rather_than_crashing(app, theme):
    """It is read from a JSON file a person can edit."""
    from gini.ui.os_hud import FONT_BODY, OsHud
    for bad in (None, "", "big", object(), -5):
        h = OsHud(_parent(), theme, scale_fn=lambda b=bad: b)
        assert h._font(FONT_BODY).pointSizeF() > 0


# ---- the seam between the window and the HUD -------------------------------------- #
# This is where it actually broke in front of a user: `main_window` passed `scale_getter=` and
# `OsHudController.__init__` had no such parameter, so opening the HUD raised
# "unexpected keyword argument 'scale_getter'" and the panel simply never appeared.
#
# Every test above passed through it, because they all build `OsHud` directly. Nothing built the
# CONTROLLER, and MainWindow builds it lazily — only when the HUD is opened, inside a try/except
# that turns the TypeError into a line in the console. A whole green suite proved nothing about
# the one call the application actually makes.
def test_the_controller_takes_what_the_window_passes_it(app, theme):
    """Constructed with exactly the keywords `main_window` uses, positionally identical."""
    from gini.ui.os_hud import OsHudController
    c = OsHudController(_parent(), theme,
                        agent_of=lambda *_a: None,
                        on_source=lambda *_a: None,
                        window_getter=lambda: 10,
                        scale_getter=lambda: 150,
                        scrub_getter=lambda: 120)
    try:
        assert c.hud.scale_fn() == 150
        assert c.hud._font(9).pointSizeF() == round(9 * 1.5)
    finally:
        c.close()


def test_the_window_and_the_controller_agree_on_the_argument_list():
    """Read the call site rather than trusting it. A keyword the window sends and the controller
    does not accept is a TypeError at the moment a student opens the panel — the one place it
    cannot be discovered by anything short of opening it."""
    import inspect

    from gini.ui import main_window as mw
    from gini.ui.os_hud import OsHudController
    accepted = set(inspect.signature(OsHudController.__init__).parameters)
    src = inspect.getsource(mw.MainWindow)
    start = src.index("OsHudController(") + len("OsHudController")
    depth, end = 0, start
    for i in range(start, len(src)):            # balance the parens: the call spans ten lines
        depth += (src[i] == "(") - (src[i] == ")")
        if depth == 0 and i > start:
            end = i
            break
    call = src[start:end]
    sent = {ln.split("=")[0].strip() for ln in call.split("\n")
            if "=" in ln and ln.strip().split("=")[0].strip().isidentifier()}
    assert "scale_getter" in sent, "the call site changed; this test is now checking nothing"
    unknown = sent - accepted
    assert not unknown, f"main_window sends {sorted(unknown)}, which OsHudController rejects"


def test_the_controller_defaults_to_matching_the_text_size(app, theme):
    """Built without the getter at all — the shape every existing caller in the tests uses."""
    from gini.ui.os_hud import OsHudController
    c = OsHudController(_parent(), theme, agent_of=lambda *_a: None)
    try:
        assert c.hud.scale_fn() == 0
    finally:
        c.close()
