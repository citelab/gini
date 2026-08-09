"""What the canvas says about a real board that has just lost power.

Reported: pull the ESP32's power and the element still reads "connected" for over half a
minute, the Inspector goes on describing a board that is no longer there, and a board
that IS there never shows its address the way every other element does.

Three separate faults with one theme — the canvas kept asserting things about hardware it
could no longer hear. A stale address is the worst of them, because unlike a stale label
it looks actionable: it invites you to go and ping something that cannot answer.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from gini.app.context import AppContext
from gini.runtime import gbridge as gb
from gini.ui.canvas import CanvasView
from gini.ui.theme.tokens import get_theme


def _canvas():
    QApplication.instance() or QApplication([])
    ctx = AppContext()
    c = CanvasView(ctx, get_theme("Dark"))
    c.resize(800, 600)
    return c, ctx


def _board_node(c, ctx):
    d = ctx.add_device("gini32", 100, 100)
    return c.scene_.nodes[d.id]


# ------------------------------------------------------------- the address line

def test_a_board_shows_no_address_until_hardware_checks_in():
    """An element on the canvas with no board behind it must not claim an address."""
    c, ctx = _canvas()
    assert _board_node(c, ctx).board_addr == ""


def test_the_address_appears_when_the_board_is_online_and_clears_when_it_is_not():
    c, ctx = _canvas()
    node = _board_node(c, ctx)

    node.set_board_addr("10.0.9.1")
    assert node.board_addr == "10.0.9.1"

    # power pulled -> the address must go with it. Showing 10.0.9.1 for a board that is
    # not there invites pinging something that cannot answer.
    node.set_board_addr("")
    assert node.board_addr == ""


def test_setting_the_same_address_does_not_force_a_repaint():
    """This runs on a 3 s poll for every board; repainting unconditionally would put the
    canvas under constant needless load."""
    c, ctx = _canvas()
    node = _board_node(c, ctx)
    painted = []
    node.update = lambda *a, **k: painted.append(1)

    node.set_board_addr("10.0.9.1")
    node.set_board_addr("10.0.9.1")
    node.set_board_addr("10.0.9.1")
    assert len(painted) == 1


def test_the_board_address_is_never_saved_with_the_topology():
    """It is observed hardware state, not something anyone drew — the same rule the live
    client nodes follow."""
    c, ctx = _canvas()
    node = _board_node(c, ctx)
    node.set_board_addr("10.0.9.1")
    blob = ctx.topology.to_dict()
    assert "10.0.9.1" not in repr(blob)


# ------------------------------------------------------------------- the timeout

def test_how_long_a_pulled_power_cable_stays_invisible():
    """Bound the WORST case a student can see, end to end.

    The chain is: the board speaks every BOARD_KEEPALIVE_S, the relay waits
    OFFLINE_AFTER before giving up, and the UI polls every 3 s. Power can be pulled the
    instant after a keepalive, so the worst case is the sum. This asserts the total is
    under 20 s — it used to be 33 s, which reads as a broken link rather than a timeout.
    """
    ui_poll_s = 3.0
    worst_case = gb.OFFLINE_AFTER + ui_poll_s
    assert worst_case < 20.0, f"a dead board stays 'connected' for up to {worst_case:.0f}s"


def test_the_grace_period_still_tolerates_a_lossy_channel():
    """The other half of the trade-off: several boards share one 2.4 GHz channel (APSTA
    forces them onto the uplink's), so losing two keepalives in a row is ordinary and
    must NOT flap the canvas."""
    assert gb.OFFLINE_AFTER > 2 * gb.BOARD_KEEPALIVE_S


def test_online_is_false_the_moment_the_grace_expires():
    link = gb.BoardLink({"board_id": "gini-5",
                         "fabric": {"bind_host": "127.0.0.1", "bind_port": 0,
                                    "peer_host": "127.0.0.1", "peer_port": 0}})
    link.addr = ("192.168.1.9", 5555)
    link.last_seen = time.time() - (gb.OFFLINE_AFTER - 1.0)
    assert link.online
    link.last_seen = time.time() - (gb.OFFLINE_AFTER + 1.0)
    assert not link.online


def test_a_board_that_never_checked_in_is_not_online():
    """`last_seen = 0` must not read as "seen at the epoch, therefore ancient but real"."""
    link = gb.BoardLink({"board_id": "gini-5",
                         "fabric": {"bind_host": "127.0.0.1", "bind_port": 0,
                                    "peer_host": "127.0.0.1", "peer_port": 0}})
    assert not link.online
