"""The Router Lab's live poll must not pile up on a slow machine.

Reported as: the Lab window opens instantly, but the spinner never stops — only on a slower Linux
box, while the router itself is perfectly healthy.

Every tick of the 2.5s timer fires several `docker compose exec` calls (routes + the gpipe chain +
queue stats for a router; three openflow queries for an OVS). Each is a whole docker CLI
invocation plus a round trip to the gRouter's control socket. When a round takes longer than the
interval — which is ordinary on a loaded or slow host — the timer starts another round on top of
it. Threads pile up without bound, each new round re-sets the status to "reading…", and the
status never resolves. The pile-up is self-sustaining: the extra execs are themselves what make
each round slow.

So the poll takes one round at a time, and backs the interval off to fit what the machine can do.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def theme(app):
    from gini.ui.theme import ThemeManager
    return ThemeManager(app)


class _Dev:
    name = "r1"
    id = "d1"
    type_key = "router"


def _lab(theme, query_fn, face="router"):
    from gini.domain.router_modules import RouterProgram
    from gini.ui.router_lab import RouterLab
    lab = RouterLab(None, theme, _Dev(), RouterProgram(), query_fn=query_fn, face=face)
    return lab


def _pump(secs=0.5):
    from PySide6.QtCore import QCoreApplication
    end = time.time() + secs
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)


def test_a_slow_round_does_not_start_another_round(app, theme):
    """The bug itself. A query slower than the poll interval must not be re-entered."""
    calls = []
    gate = threading.Event()

    def slow(cmd):
        calls.append(cmd)
        gate.wait(2.0)                      # hold the round open
        return ""

    lab = _lab(theme, slow)
    lab._live_timer.setInterval(10)         # hammer it: far faster than the query
    _pump(0.4)                              # many ticks would have fired by now
    first = len(calls)
    gate.set()
    _pump(0.2)
    assert first <= 2, (
        f"{first} queries issued while the first round was still out — rounds are overlapping, "
        f"which is what leaves the spinner running forever")
    lab._live_timer.stop()
    lab.close()


def test_the_interval_backs_off_when_a_round_overruns(app, theme):
    """A machine that cannot keep up should be polled less often, not more."""
    def slowish(cmd):
        time.sleep(0.08)
        return ""

    lab = _lab(theme, slowish)
    lab.MIN_POLL_MS = 20                    # keep the test quick; the ratio is what matters
    lab._live_timer.setInterval(20)
    _pump(1.2)
    assert lab._live_timer.interval() > 20, (
        "interval never backed off; the poll will keep outrunning the machine")
    assert lab._live_timer.interval() <= lab.MAX_POLL_MS
    lab._live_timer.stop()
    lab.close()


def test_a_fast_machine_is_not_slowed_down(app, theme):
    """The guard must not punish a host that keeps up — the interval should stay at the floor."""
    lab = _lab(theme, lambda cmd: "")
    _pump(0.4)
    assert lab._live_timer.interval() <= lab.MIN_POLL_MS, "penalised a machine that keeps up"
    lab._live_timer.stop()
    lab.close()


def test_polling_recovers_after_a_round_finishes(app, theme):
    """One-round-at-a-time must not become none-at-all: a finished round has to release the lock,
    or the Lab goes permanently stale after the first slow tick."""
    calls = []
    lab = _lab(theme, lambda cmd: calls.append(cmd) or "")
    lab.MIN_POLL_MS = 10
    lab._live_timer.setInterval(10)
    _pump(0.5)
    n = len(calls)
    _pump(0.5)
    assert len(calls) > n, "polling stopped altogether after the first round"
    lab._live_timer.stop()
    lab.close()


def test_an_ad_hoc_refresh_does_not_release_the_round(app, theme):
    """_refresh_qstats is also called directly after applying a policy. If that path decremented
    the round counter, the next tick could start while the round's own workers were still out —
    reintroducing the overlap through the back door."""
    lab = _lab(theme, lambda cmd: "")
    lab._live_timer.stop()
    # The constructor fires its own first round; let those workers drain, or their worker_done
    # signals arrive mid-test and decrement the round set up below. (They did, first time round.)
    _pump(0.3)
    assert lab._round_begin(2) is True
    lab._refresh_qstats()                   # ad-hoc: counted=False
    _pump(0.2)
    assert lab._inflight == 2, f"ad-hoc refresh released the round (inflight={lab._inflight})"
    lab.close()
