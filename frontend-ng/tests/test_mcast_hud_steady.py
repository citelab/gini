"""The Multicast HUD shows steady multicast as steady.

Reported as "the HUD shows multicast working sometimes and not others". Measured on real
gRouters, the routers were not flaky at all — one router and two, receiver started before and
after the forwarder, every poll answered and every datagram delivered. The HUD was, for two
reasons, each pinned here:

* **It differenced counters per poll, and the router only republishes them per tick.** At
  `mcast_tree.lua`'s default 5 s tick, four polls in five saw the same counter, so a member badge
  went grey, grey, grey, teal, grey under a sender that never paused.
* **A failed query counted as an answer.** It came back as text with no group rows, so every
  group on that router was logged as a leave, and joined again on the next good poll.
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from gini.domain.mcast import STALE_MIN_S, McastTracker, parse_cp_status

POLL = 1.2


def _status(count: int, ifaces: str = "2") -> str:
    return f"MCAST v1 1\nG 239.1.1.1 IF {ifaces} CP {ifaces}:{count}\n"


def _rates(counts: list[int], poll: float = POLL) -> list[float]:
    """Feed one router's counter as the HUD would see it, poll by poll; return the rate after
    each poll."""
    t, out = McastTracker(), []
    for k, c in enumerate(counts):
        t.ingest(parse_cp_status(_status(c), "R1"), tnow=100.0 + k * poll, polled={"R1"})
        out.append(t.rate("R1", "239.1.1.1", 2))
    return out


# ---- the tick ----------------------------------------------------------------------------------- #
def test_a_five_second_tick_does_not_make_a_steady_sender_flicker():
    """What the real router published at its default tick, polled every 1.2 s: a counter that
    jumps by five every fifth second. Once it has moved, the badge must stay lit."""
    counts = [0, 0, 0, 0, 5, 5, 5, 10, 10, 10, 10, 15, 15, 15, 20, 20, 20, 20, 25]
    rates = _rates(counts)
    first = counts.index(5)
    assert all(r >= 0.5 for r in rates[first:]), rates
    assert rates[-1] == pytest.approx(1.0, rel=0.35)         # one datagram a second, near enough


def test_a_one_second_tick_reads_one_a_second_not_one_then_two():
    """Polled every 1.2 s, a 1 s tick shows one new copy, then two, then one. Rated from the last
    move alone that is 0.8/s, 1.7/s, 0.8/s — a badge reading "1/s", "2/s", "1/s"."""
    rates = _rates([0, 1, 3, 4, 5, 7, 8, 10, 11, 12, 14, 15, 17, 18])
    assert all(r == pytest.approx(1.0, rel=0.25) for r in rates[3:]), rates
    assert {round(r) for r in rates[3:]} == {1}


def test_a_sender_that_stops_goes_quiet_soon_after():
    """Holding the last rate must not hold it for ever: a counter still for far longer than it
    usually takes to move is a sender that stopped."""
    stopped = [0, 1, 3, 4, 5] + [5] * 6
    rates = _rates(stopped)
    assert rates[4] >= 0.5
    quiet_after = next(k for k, r in enumerate(rates) if k > 4 and r == 0.0)
    assert (quiet_after - 4) * POLL <= STALE_MIN_S + 2 * POLL
    assert rates[-1] == 0.0


def test_a_slow_tick_is_not_mistaken_for_a_stopped_sender():
    """At a 5 s tick the counter is legitimately still for four polls; that is not 'stopped'."""
    rates = _rates([0, 0, 0, 0, 5, 5, 5, 5, 10])
    assert min(rates[4:]) >= 0.5, rates


def test_a_reloaded_forwarder_restarts_the_count_without_a_negative_rate():
    rates = _rates([0, 1, 3, 4, 0, 1, 3])
    assert rates[4] == 0.0
    assert all(r >= 0 for r in rates)
    assert rates[-1] > 0


def test_a_router_that_answers_with_no_groups_still_logs_the_leave():
    """The other half: a REAL answer with the group gone is a leave, as it always was."""
    t = McastTracker()
    t.ingest(parse_cp_status(_status(3), "R1"), tnow=1.0, polled={"R1"})
    t.ingest(parse_cp_status("MCAST v1 0\n", "R1"), tnow=2.2, polled={"R1"})
    assert t.groups() == []
    assert [e.kind for e in t.recent_events(1)] == ["leave"]


# ---- a failed query ----------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


def _settle(app, ctl, polls_before: int) -> None:
    deadline = time.monotonic() + 5
    while (ctl._busy or ctl.polls == polls_before) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_a_query_that_fails_does_not_wipe_the_routers_groups(app, theme):
    from PySide6.QtWidgets import QWidget

    from gini.ui.mcast_hud import McastHudController

    answers = {"n": 0}

    def query(name, cmd):
        answers["n"] += 1
        if answers["n"] == 2:
            raise RuntimeError("(query failed: timed out)")       # the docker exec that hiccuped
        return _status(answers["n"])

    parent = QWidget()
    ctl = McastHudController(parent, theme, routers=lambda: ["R1"], query=query)
    ctl.polls = 0
    ctl.rows_ready.connect(lambda *a: setattr(ctl, "polls", ctl.polls + 1))
    for _ in range(3):
        before = ctl.polls
        ctl.refresh()
        _settle(app, ctl, before)
        assert ctl._tracker.groups() == ["239.1.1.1"], f"poll {ctl.polls}"
    assert [e.kind for e in ctl._tracker.events] == ["join"]       # one join, no leave/rejoin
    ctl.close()


def test_the_checked_query_raises_when_the_router_was_not_reached(monkeypatch):
    """`element_query` folds a failure into text for the console; the HUD's variant must not."""
    from gini.ui import main_window as mw

    fake = SimpleNamespace(_workdir="/tmp/x", _exec_argv=lambda svc: ["docker", "exec", svc],
                           ctx=SimpleNamespace(topology=SimpleNamespace(devices={})))
    fake._element_exec = lambda n, c: mw.MainWindow._element_exec(fake, n, c)

    def run(rc, out, err=""):
        monkeypatch.setattr("subprocess.run",
                            lambda *a, **k: SimpleNamespace(returncode=rc, stdout=out, stderr=err))

    run(0, "MCAST v1 0")
    assert mw.MainWindow.element_query_checked(fake, "R1", "gpipe cp status") == "MCAST v1 0"
    run(1, "", "cannot reach router control socket /run/r1.ctl")
    with pytest.raises(RuntimeError):
        mw.MainWindow.element_query_checked(fake, "R1", "gpipe cp status")
    assert "cannot reach" in mw.MainWindow.element_query(fake, "R1", "gpipe cp status")
    fake._workdir = None
    with pytest.raises(RuntimeError):
        mw.MainWindow.element_query_checked(fake, "R1", "gpipe cp status")
