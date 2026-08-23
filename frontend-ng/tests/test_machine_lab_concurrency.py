"""Two concurrency bugs that made the Machine Lab flaky under load.

Several threads converge on one MachineState: the Lab's poll timer, the worker that re-reads
after a launch or kill, the OS HUD, and the Ask GINI agent. The agent behind them is
single-threaded, so reads queue and, under load, time out.

  1. A read that RAISED never emitted `snap_ready`, and `_busy` is only cleared in that slot — so
     one failed read left `_busy` set forever and the Lab silently stopped updating until it was
     closed and reopened. No error, no clue.

  2. `MachineState` had no lock, and `_act_then_refresh` (launch/kill) bypassed the `_busy` guard
     entirely — so two threads could be inside `refresh()` together. Reads take different amounts
     of time, so an OLDER snapshot could land after a newer one and send `latest` backwards.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

import pytest

from gini.domain.machine_state import MachineState
from gini.domain.xv6 import Proc, Snapshot

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _snap(n):
    return Snapshot(procs=[Proc(pid=1, state="running", name="init")], ticks=n)


class Provider:
    """Records overlap: how many callers were inside snapshot() at the same time."""

    def __init__(self, delay=0.02, fail=False):
        self.delay = delay
        self.fail = fail
        self.inside = 0
        self.max_inside = 0
        self.calls = 0
        self._m = threading.Lock()

    def snapshot(self):
        with self._m:
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
            self.calls += 1
            n = self.calls
        try:
            time.sleep(self.delay)
            if self.fail:
                raise TimeoutError("agent did not answer")
            return _snap(n)
        finally:
            with self._m:
                self.inside -= 1

    def step(self):
        return self.snapshot()

    def set_timeslice(self, _v):
        pass


# -- 2. one reader at a time ------------------------------------------------------------------- #
def test_concurrent_refreshes_do_not_overlap():
    """The lock spans the provider READ, not just the ingest — guarding only the write would
    still let two reads finish out of order."""
    p = Provider(delay=0.02)
    st = MachineState(p, device_id="m1", mode="real")
    ts = [threading.Thread(target=st.refresh) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in ts), "refresh() deadlocked"
    assert p.max_inside == 1, f"{p.max_inside} readers were inside snapshot() at once"


def test_latest_never_goes_backwards_under_concurrency():
    """The visible symptom of the race: the Lab going strange right after a launch, which is
    exactly when the launch worker and the poll timer overlap."""
    p = Provider(delay=0.005)
    st = MachineState(p, device_id="m1", mode="real")
    seen = []

    def pump():
        for _ in range(15):
            st.refresh()
            seen.append(st.latest.ticks)

    ts = [threading.Thread(target=pump) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
    # every observation must be a tick the provider actually produced, and the newest snapshot
    # must be the highest one it produced — never an older read landing last
    assert st.latest.ticks == p.calls, f"latest is {st.latest.ticks}, provider produced {p.calls}"
    assert seen, "nothing was observed"


def test_step_and_refresh_share_the_lock():
    p = Provider(delay=0.02)
    st = MachineState(p, device_id="m1", mode="real")
    ts = [threading.Thread(target=st.refresh if i % 2 else st.step) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert p.max_inside == 1


def test_a_reentrant_lock_does_not_deadlock_itself():
    """refresh() and step() both go on to call _ingest(); a plain Lock would deadlock."""
    p = Provider(delay=0)
    st = MachineState(p, device_id="m1", mode="real")
    for _ in range(3):
        assert st.refresh() is not None
        assert st.step() is not None


# -- 1. a failed read must not wedge the poll --------------------------------------------------- #
class _Dev:
    type_key = "xv6"
    name = "M1"
    id = "m1"
    properties = {"Timeslice": "1"}


def _lab(app, provider):
    from gini.ui.machine_lab import MachineLab
    from gini.ui.theme import ThemeManager
    st = MachineState(provider, device_id="m1", mode="real")
    lab = MachineLab(None, ThemeManager(app), _Dev(), state=st, live=True)
    return lab


def _settle(app, lab, tries=200):
    for _ in range(tries):
        app.processEvents()
        if not lab._busy:
            return True
        time.sleep(0.01)
    return False


def test_a_failing_read_does_not_wedge_the_poll(app):
    """THE bug. One timed-out read used to leave _busy set forever, so every later poll returned
    at the guard and the Lab stopped updating — silently, until reopened."""
    lab = _lab(app, Provider(delay=0, fail=True))
    try:
        for _ in range(3):
            lab._on_poll()
            assert _settle(app, lab), "_busy was never cleared after a failed read"
        assert lab._read_fails >= 3          # the failures were counted, not swallowed
    finally:
        lab._closed = True
        lab.close()


def test_the_poll_recovers_once_reads_succeed_again(app):
    p = Provider(delay=0, fail=True)
    lab = _lab(app, p)
    try:
        lab._on_poll()
        _settle(app, lab)
        p.fail = False                       # the machine comes back
        lab._on_poll()
        assert _settle(app, lab)
        assert lab.state.latest is not None, "the Lab did not resume reading"
        assert lab._read_fails == 0, "the failure run was not reset by a good read"
    finally:
        lab._closed = True
        lab.close()


def test_a_sustained_failure_is_reported_once(app):
    said = []
    lab = _lab(app, Provider(delay=0, fail=True))
    lab.on_log = lambda lvl, msg: said.append((lvl, msg))
    try:
        for _ in range(9):
            lab._on_poll()
            _settle(app, lab)
        errs = [m for lvl, m in said if lvl == "error"]
        assert len(errs) == 1, f"expected one report, got {len(errs)}"
        assert "readings are failing" in errs[0]
    finally:
        lab._closed = True
        lab.close()
