"""Right-drag-to-connect (the book's gesture) creates a link between two nodes."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _evt(kind, local, button=Qt.RightButton):
    g = QPointF(local)
    return QMouseEvent(kind, QPointF(local), g, button, button, Qt.NoModifier)


def test_right_drag_connects_two_nodes():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.resize(900, 700)
    view = w.canvas

    r1 = w.api.add_device("router", x=80, y=80)["id"]
    s1 = w.api.add_device("switch", x=420, y=360)["id"]
    app.processEvents()

    n1 = view.scene_.nodes[r1]
    n2 = view.scene_.nodes[s1]
    p1 = view.mapFromScene(n1.sceneBoundingRect().center())
    p2 = view.mapFromScene(n2.sceneBoundingRect().center())

    before = len(w.ctx.topology.links)
    view.mousePressEvent(_evt(QEvent.MouseButtonPress, p1))
    # move in steps so it crosses the drag threshold
    mid = (p1 + p2) / 2
    view.mouseMoveEvent(_evt(QEvent.MouseMove, mid))
    view.mouseMoveEvent(_evt(QEvent.MouseMove, p2))
    view.mouseReleaseEvent(_evt(QEvent.MouseButtonRelease, p2))

    assert len(w.ctx.topology.links) == before + 1     # a link was created
    # the link joins R1 and S1
    ends = {(l.source_id, l.target_id) for l in w.ctx.topology.links.values()}
    assert (r1, s1) in ends or (s1, r1) in ends
    assert view._rc_from is None                        # drag state cleared


def test_click_connect_with_stale_first_endpoint_does_not_raise():
    """Click-to-connect must not throw if the first-clicked device is gone (deleted or
    a New/Open between clicks). Regression for the KeyError spam in mousePressEvent."""
    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.resize(900, 700)
    view = w.canvas
    view.set_connect_mode(True)

    a = w.api.add_device("router", x=80, y=80)["id"]
    b = w.api.add_device("switch", x=420, y=360)["id"]
    app.processEvents()

    # click the first node, then delete it out from under the pending connection
    n_a = view.scene_.nodes[a]
    view.mousePressEvent(_evt(QEvent.MouseButtonPress,
                              view.mapFromScene(n_a.sceneBoundingRect().center()),
                              button=Qt.LeftButton))
    assert view._connect_first == a
    w.api.remove_device(a)
    app.processEvents()

    # clicking the surviving node must NOT raise; the stale first is dropped and b
    # becomes the new pending first (no link to a gone device)
    n_b = view.scene_.nodes[b]
    before = len(w.ctx.topology.links)
    view.mousePressEvent(_evt(QEvent.MouseButtonPress,
                              view.mapFromScene(n_b.sceneBoundingRect().center()),
                              button=Qt.LeftButton))
    assert len(w.ctx.topology.links) == before          # no bogus link created
    assert view._connect_first == b                     # recovered: b is the new first


def test_add_link_hard_blocks_xv6_to_network():
    """xv6 has no networking: wiring it to a switch must be rejected at the context choke
    point (all canvas connect paths funnel through ctx.add_link)."""
    import pytest

    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    k = w.api.add_device("xv6", x=80, y=80)["id"]
    s = w.api.add_device("switch", x=420, y=360)["id"]
    before = len(w.ctx.topology.links)
    with pytest.raises(ValueError):
        w.ctx.add_link(k, s)
    assert len(w.ctx.topology.links) == before          # no link created

    # but a peripheral attaches fine
    term = w.api.add_device("terminal", x=420, y=80)["id"]
    w.ctx.add_link(k, term)
    assert len(w.ctx.topology.links) == before + 1
