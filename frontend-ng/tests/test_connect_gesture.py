"""Wiring two elements together must not be a coin flip.

The reported experience: "sometimes it works, sometimes the element just moves." Three causes, all
the same root — the SAME gesture (left press-drag on a node) meant three different things depending
on invisible state:

  1. connect mode was CLICK-CLICK only, so a press-drag-release did nothing at all, and the link
     appeared later, on what felt like an unrelated click;
  2. connect mode off, a left-drag MOVES the node (correct — but indistinguishable to the user);
  3. missing the icon by a few pixels counted as a background click, which silently switched connect
     mode OFF mid-gesture, so the next drag moved the element.

Now: in connect mode, BOTH a drag and a click-click link, a wire follows the cursor either way, and
near-misses on the icon still count.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from gini.app.context import AppContext
from gini.ui.canvas import CanvasView
from gini.ui.theme.tokens import get_theme


def _canvas():
    app = QApplication.instance() or QApplication([])
    ctx = AppContext()
    c = CanvasView(ctx, get_theme("Dark"))
    c.resize(800, 600)
    return c, ctx


def _place(c, ctx):
    """Two nodes, far apart, and their positions in VIEW coordinates."""
    a = ctx.add_device("switch", 100, 100)
    b = ctx.add_device("router", 400, 300)
    c.viewport().resize(800, 600)
    pa = c.mapFromScene(c.scene_.nodes[a.id].sceneBoundingRect().center())
    pb = c.mapFromScene(c.scene_.nodes[b.id].sceneBoundingRect().center())
    return a, b, pa, pb


def _ev(kind, pos, button=Qt.LeftButton):
    return QMouseEvent(kind, QPointF(pos), button, button, Qt.NoModifier)


def _press(c, pos, button=Qt.LeftButton):
    c.mousePressEvent(_ev(QEvent.MouseButtonPress, pos, button))


def _move(c, pos, button=Qt.LeftButton):
    c.mouseMoveEvent(_ev(QEvent.MouseMove, pos, button))


def _release(c, pos, button=Qt.LeftButton):
    c.mouseReleaseEvent(_ev(QEvent.MouseButtonRelease, pos, button))


def test_drag_from_one_element_to_another_makes_a_link():
    """The gesture EVERYONE tries first. It used to do nothing."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    c.set_connect_mode(True)

    _press(c, pa)
    _move(c, QPoint((pa.x() + pb.x()) // 2, (pa.y() + pb.y()) // 2))
    assert c._rc_line is not None                      # the wire follows the cursor…
    _release(c, pb)

    assert len(ctx.topology.links) == 1                # …and lands on the target
    link = next(iter(ctx.topology.links.values()))
    assert {link.source_id, link.target_id} == {a.id, b.id}
    assert c._rc_line is None                          # no wire left dangling


def test_click_click_still_makes_a_link():
    """The other natural gesture. Both must work — a student should not have to guess which."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    c.set_connect_mode(True)

    _press(c, pa); _release(c, pa)                     # click A (no drag)
    assert c._connect_first == a.id                    # armed
    _move(c, pb)
    assert c._rc_line is not None                      # the wire trails the cursor between clicks
    _press(c, pb); _release(c, pb)                     # click B

    assert len(ctx.topology.links) == 1
    assert c._connect_first is None and c._rc_line is None


def test_a_near_miss_on_the_icon_does_not_cancel_the_mode():
    """Being a few pixels off used to count as 'clicked the background' → connect mode switched
    itself off → the next drag moved the element. That was the coin flip."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    c.set_connect_mode(True)

    exits = []
    ctx.bus.canvas_background_clicked.connect(lambda: exits.append(1))
    near_miss = QPoint(pa.x() + 6, pa.y() + 6)         # just off the icon
    _press(c, near_miss)

    assert not exits                                   # NOT read as a background click
    assert c._connect_first == a.id                    # it armed the node we clearly meant
    _release(c, near_miss)
    _press(c, pb); _release(c, pb)
    assert len(ctx.topology.links) == 1


def test_dragging_out_to_empty_canvas_abandons_the_gesture():
    """No hidden armed endpoint left behind to surprise the next click."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    c.set_connect_mode(True)

    _press(c, pa)
    _move(c, QPoint(700, 550))
    _release(c, QPoint(700, 550))                      # let go over nothing

    assert not ctx.topology.links
    assert c._connect_first is None                    # disarmed…
    assert c._rc_line is None                          # …and the wire is gone


def test_right_drag_still_connects_with_connect_mode_off():
    """The book's gesture keeps working, and a left-drag still MOVES the node (as it should)."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    assert c._connect_mode is False

    _press(c, pa, Qt.RightButton)
    _move(c, QPoint((pa.x() + pb.x()) // 2, (pa.y() + pb.y()) // 2), Qt.RightButton)
    _release(c, pb, Qt.RightButton)
    assert len(ctx.topology.links) == 1


def test_leaving_connect_mode_never_leaves_a_dangling_wire():
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    c.set_connect_mode(True)
    _press(c, pa)
    _move(c, pb)
    assert c._rc_line is not None
    c.set_connect_mode(False)                          # bail out mid-gesture
    assert c._rc_line is None and c._connect_first is None


# --------------------------------------------------------------------------- #
# The regression that came BACK: right-drag-to-connect, flaky again.
#
# Two defects, and the second is why it "worked after the fix" and then rotted: the failure rate
# was really the odds of something overlapping the icon, so a fresh canvas passed and a real
# topology didn't.
# --------------------------------------------------------------------------- #
def test_right_drag_connects_without_connect_mode():
    """The book's gesture: no mode, just right-press A and drag to B."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    assert not c._connect_mode                          # explicitly NOT in connect mode

    _press(c, pa, Qt.RightButton)
    _move(c, QPoint((pa.x() + pb.x()) // 2, (pa.y() + pb.y()) // 2), Qt.RightButton)
    _release(c, pb, Qt.RightButton)

    assert len(ctx.topology.links) == 1
    link = next(iter(ctx.topology.links.values()))
    assert {link.source_id, link.target_id} == {a.id, b.id}


def test_right_drag_survives_a_few_pixels_of_miss():
    """A 3px miss on a 40px icon is a miss by the user's standards and a HIT by their intent.
    Connect mode already allowed slack; the right-drag path allowed none — so the same gesture
    worked or didn't depending on pixels the student can't see."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    off_a = QPoint(pa.x() + 3, pa.y() - 3)              # just off the icon, both ends
    off_b = QPoint(pb.x() - 3, pb.y() + 3)

    _press(c, off_a, Qt.RightButton)
    _move(c, QPoint((pa.x() + pb.x()) // 2, (pa.y() + pb.y()) // 2), Qt.RightButton)
    _release(c, off_b, Qt.RightButton)

    assert len(ctx.topology.links) == 1                 # near enough IS enough


def test_the_hit_test_looks_THROUGH_whatever_is_on_top():
    """`itemAt()` returns only the topmost item, so anything drawn ABOVE a node (a tutor callout is
    z=60, an X-ray ghost is z=20; nodes are z=10) masks it completely — the press lands on 'empty
    canvas' with the cursor dead-centre on the element. Edges (z=1) and group boxes (z=-5) are
    below, so they can't do this. The hit test now scans the whole stack instead of the top of it.

    NOTE: this is hardening, not a proven fix for the reported flakiness — see the console tracing
    (GINI_TRACE_GESTURES=1) which is what will actually tell us where the real gesture falls off."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    node_a = c.scene_.nodes[a.id]

    from PySide6.QtWidgets import QGraphicsRectItem
    cover = QGraphicsRectItem(node_a.sceneBoundingRect())     # something opaque, right on top
    cover.setZValue(100)
    c.scene_.addItem(cover)

    assert c.itemAt(pa) is cover                  # the OLD hit test sees only this…
    assert c._node_at(pa) is node_a               # …the new one still finds the node beneath

    _press(c, pa, Qt.RightButton)                 # and the gesture works through it
    _move(c, QPoint((pa.x() + pb.x()) // 2, (pa.y() + pb.y()) // 2), Qt.RightButton)
    _release(c, pb, Qt.RightButton)
    assert len(ctx.topology.links) == 1


def test_a_plain_right_click_still_opens_the_menu_not_a_link():
    """The drag must not steal the context menu: no movement = menu, movement = wire."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    opened = []
    c.scene_.nodes[a.id].popup_menu = lambda pos: opened.append(pos)

    _press(c, pa, Qt.RightButton)
    _release(c, pa, Qt.RightButton)                     # pressed and released, never moved

    assert opened                                       # the node menu came up…
    assert not ctx.topology.links                       # …and no phantom link was made


def _dblclick(c, pos, button=Qt.LeftButton):
    """Qt does NOT send a second Press for a rapid second click — it sends DblClick.
    Sequence: Press → Release → DblClick → Release."""
    c.mouseDoubleClickEvent(_ev(QEvent.MouseButtonDblClick, pos, button))


def test_a_fast_second_right_drag_is_not_swallowed():
    """THE reported bug. Wire one element, then immediately wire the next: Qt delivers that second
    press as a DblClick, not a Press, so mousePressEvent was never called and the gesture silently
    evaporated. Slow down and it worked. That is the whole coin flip."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    c2 = ctx.add_device("host", 100, 400)
    pc = c.mapFromScene(c.scene_.nodes[c2.id].sceneBoundingRect().center())

    # first wire: a normal press-drag-release
    _press(c, pa, Qt.RightButton)
    _move(c, QPoint((pa.x() + pb.x()) // 2, (pa.y() + pb.y()) // 2), Qt.RightButton)
    _release(c, pb, Qt.RightButton)
    assert len(ctx.topology.links) == 1

    # second wire, IMMEDIATELY after → Qt sends DblClick in place of the Press
    _dblclick(c, pc, Qt.RightButton)
    _move(c, QPoint((pc.x() + pb.x()) // 2, (pc.y() + pb.y()) // 2), Qt.RightButton)
    _release(c, pb, Qt.RightButton)

    assert len(ctx.topology.links) == 2            # it must wire, not vanish
    link = list(ctx.topology.links.values())[1]
    assert {link.source_id, link.target_id} == {c2.id, b.id}


def test_a_fast_right_click_does_not_open_a_console():
    """The same DblClick also reached NodeItem.mouseDoubleClickEvent, which didn't check the button
    — so wiring quickly could pop a terminal open. Left-double-click still opens one."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    opened = []
    ctx.bus.device_activated.connect(opened.append)

    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    def _scene_dbl(button):
        ev = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseDoubleClick)
        ev.setButton(button)
        ev.setPos(QPointF(10, 10))
        return ev

    node = c.scene_.nodes[a.id]
    node.mouseDoubleClickEvent(_scene_dbl(Qt.RightButton))
    assert not opened                              # a fast right-click is a WIRE, not a login

    node.mouseDoubleClickEvent(_scene_dbl(Qt.LeftButton))
    assert opened == [a.id]                        # …left double-click still logs in


def test_connect_mode_ALSO_drops_a_fast_second_wire():
    """The reason we didn't just delete the right-drag. Connect mode felt '100% reliable' only
    because aiming in it is slower — beat the double-click interval and it loses the wire exactly
    the same way. The bug was never about which button; it was the missing DblClick handler."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    h2 = ctx.add_device("host", 100, 400)
    pc = c.mapFromScene(c.scene_.nodes[h2.id].sceneBoundingRect().center())
    c.set_connect_mode(True)

    _press(c, pa)                                        # wire 1: the ordinary way
    _move(c, QPoint((pa.x() + pb.x()) // 2, (pa.y() + pb.y()) // 2))
    _release(c, pb)
    assert len(ctx.topology.links) == 1

    _dblclick(c, pc)                                     # wire 2, fast → Qt sends DblClick
    _move(c, QPoint((pc.x() + pb.x()) // 2, (pc.y() + pb.y()) // 2))
    _release(c, pb)
    assert len(ctx.topology.links) == 2                  # it must still wire


def test_a_plain_double_click_still_logs_in():
    """…without breaking what a left double-click MEANS outside connect mode."""
    c, ctx = _canvas()
    a, b, pa, pb = _place(c, ctx)
    opened = []
    ctx.bus.device_activated.connect(opened.append)
    assert not c._connect_mode

    from PySide6.QtWidgets import QGraphicsSceneMouseEvent
    ev = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseDoubleClick)
    ev.setButton(Qt.LeftButton)
    ev.setPos(QPointF(10, 10))
    c.scene_.nodes[a.id].mouseDoubleClickEvent(ev)
    assert opened == [a.id]                              # still opens the console
