"""The Machine Lab's data faces poll, and do it without hurting anything.

The Virtual Memory and File System faces had no timer at all — they painted in the constructor
and on a button press, so `alloc`, `toucher`, `writer` and `sgrind`, programs written to be
WATCHED, were watched on a still photograph.

Adding a timer is the easy half. These tests pin the four things that made the naive version
wrong: reads that pile up on a slow wire, reads on the GUI thread, a window that keeps polling
after it is closed, and a failed read that blanks a good picture.
"""
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _theme(app):
    from gini.ui.theme import ThemeManager
    if not hasattr(_theme, "_t"):
        _theme._t = ThemeManager(app)
    return _theme._t


class _Dev:
    name = "m1"
    id = "d1"
    type_key = "machine"
    properties: dict = {}


class _Vm:
    """A live-shaped VM reader: no `simulate_fault`, so the lab treats it as real."""

    def __init__(self, delay=0.0):
        self.reads = 0
        self.threads: list = []
        self.delay = delay
        self.fail = False
        self.inflight = 0
        self.peak = 0
        self._lk = threading.Lock()

    def snapshot(self):
        from gini.domain.xv6_vm import DemoVm
        with self._lk:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            self.reads += 1
            self.threads.append(threading.get_ident())
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.fail:
                raise RuntimeError("the container is not answering")
            return DemoVm().snapshot()
        finally:
            with self._lk:
                self.inflight -= 1

    def all_procs(self):
        return {}

    def faults(self):
        return []


def _lab(app, prov, interval=20):
    """A Memory face on `prov`, polling fast enough for a test. The constructor's one synchronous
    read is discounted — every LATER read is what these tests are about."""
    from gini.ui.memory_lab import MemoryLab
    lab = MemoryLab(None, _theme(app), provider=prov)
    prov.reads, prov.threads, prov.peak = 0, [], 0
    lab._interval = interval
    lab._poll.start(interval)
    return lab


def test_the_face_refreshes_without_the_button(app, qtbot):
    """THE fix. `alloc 8 &` must make the page count climb with nothing pressed."""
    prov = _Vm()
    lab = _lab(app, prov)
    qtbot.addWidget(lab)
    try:
        qtbot.waitUntil(lambda: prov.reads >= 3, timeout=4000)
    finally:
        lab.stop_polling()


def test_reads_never_happen_on_the_gui_thread(app, qtbot):
    """Each round is up to three dumps over the serial, tens of ms at best and 0.35 s each at
    worst. On the GUI thread that is a frozen window; the whole point of the worker is to move
    it off. The reads used to sit INSIDE _render and _render_sharing, which is where they would
    have stayed if the timer had simply been added on top."""
    prov = _Vm()
    lab = _lab(app, prov)
    qtbot.addWidget(lab)
    try:
        qtbot.waitUntil(lambda: prov.reads >= 2, timeout=4000)
        gui = threading.get_ident()
        assert gui not in prov.threads, "a poll read ran on the GUI thread"
    finally:
        lab.stop_polling()


def test_a_slow_round_does_not_stack(app, qtbot):
    """One read in flight at a time. The serial line carries one dump at a time and every read
    holds the agent's lock for its duration, so a round that is slow because the wire is busy
    must not have four more started on top of it."""
    prov = _Vm(delay=0.4)
    lab = _lab(app, prov, interval=5)
    qtbot.addWidget(lab)
    try:
        for _ in range(5):
            lab._tick()
        time.sleep(0.15)
        assert prov.peak == 1, f"{prov.peak} reads were in flight at once"
    finally:
        lab.stop_polling()


def test_a_failed_read_keeps_the_last_good_picture(app, qtbot):
    """The one that makes a walking student's face stop being alarming.

    A face that flickers between real data and an error is worse than one that holds still: the
    error is usually a 0.35 s timeout under load, not a machine that has gone away.
    """
    prov = _Vm()
    lab = _lab(app, prov)
    qtbot.addWidget(lab)
    try:
        qtbot.waitUntil(lambda: lab._last_ok is not None, timeout=4000)
        rows = lab._pt_tbl.rowCount()
        assert rows, "nothing was drawn to begin with"
        prov.fail = True
        qtbot.waitUntil(lambda: "stale" in lab._chip.text(), timeout=4000)
        assert lab._pt_tbl.rowCount() == rows, "a failed read blanked a good page table"
    finally:
        lab.stop_polling()


def test_the_backoff_widens_then_recovers(app, qtbot):
    """A wedged or saturated machine must not be hammered — but a face must not go so quiet it
    looks dead either, so one good round restores the rate."""
    prov = _Vm()
    lab = _lab(app, prov)
    qtbot.addWidget(lab)
    try:
        prov.fail = True
        qtbot.waitUntil(lambda: lab._backoff >= 2, timeout=4000)
        assert lab._poll.interval() > lab._interval
        prov.fail = False
        qtbot.waitUntil(lambda: lab._backoff == 0, timeout=8000)
        assert lab._poll.interval() == lab._interval
    finally:
        lab.stop_polling()


def test_a_paused_face_does_not_read(app, qtbot):
    """Pedagogical, not a nicety: a student reading a page table wants it to hold still."""
    prov = _Vm()
    lab = _lab(app, prov)
    qtbot.addWidget(lab)
    try:
        lab.set_paused(True)
        before = prov.reads
        for _ in range(3):
            lab._tick()
        time.sleep(0.1)
        assert prov.reads == before
        assert not lab._poll.isActive()
        lab.set_paused(False)
        assert lab._poll.isActive(), "pausing must not be one-way"
    finally:
        lab.stop_polling()


def test_closing_waits_for_the_worker(app, qtbot):
    """The join is the load-bearing part of stop_polling().

    Without it, `_retire` calls deleteLater() while a worker may be a microsecond past its
    `if not self._closed` check and about to emit into a QObject being destroyed — the shape
    that was crashing pytest-qt on half of all runs before the runtime stop() work. The test
    suite's autouse thread-leak guard would catch the same thing from the other side.
    """
    prov = _Vm(delay=0.3)
    lab = _lab(app, prov, interval=5)
    qtbot.addWidget(lab)
    lab._tick()
    worker = lab._poll_thread
    assert worker is not None and worker.is_alive()

    t0 = time.time()
    lab.stop_polling()
    waited = time.time() - t0
    assert not worker.is_alive(), "close returned while a read was still running"
    assert waited > 0.1, "it did not actually wait — the thread just happened to finish"


def test_a_closed_face_stops_polling(app, qtbot):
    prov = _Vm()
    lab = _lab(app, prov)
    qtbot.addWidget(lab)
    lab.show()
    lab.close()
    assert not lab._poll.isActive()
    before = prov.reads
    time.sleep(0.1)
    assert prov.reads == before, "a CLOSED face is still reading the serial line"


def test_a_hidden_face_costs_nothing_and_resumes(app, qtbot):
    prov = _Vm()
    lab = _lab(app, prov)
    qtbot.addWidget(lab)
    try:
        lab.show()
        lab.hide()
        assert not lab._poll.isActive()
        lab.show()
        assert lab._poll.isActive(), "reopening a face must not show a frozen picture"
    finally:
        lab.stop_polling()


def test_the_storage_face_polls_too(app, qtbot):
    """Same mixin, so the same four rules — but wired to its own reader and interval."""
    from gini.ui.storage_lab import STORAGE_POLL_MS, StorageLab

    class _Fs:
        def __init__(self):
            self.reads = 0

        def snapshot(self):
            from gini.domain.xv6_fs import DemoDisk
            self.reads += 1
            return DemoDisk().snapshot()

    prov = _Fs()
    lab = StorageLab(None, _theme(app), provider=prov)
    qtbot.addWidget(lab)
    assert lab._interval == STORAGE_POLL_MS
    prov.reads = 0
    lab._interval = 20
    lab._poll.start(20)
    try:
        qtbot.waitUntil(lambda: prov.reads >= 2, timeout=4000)
    finally:
        lab.stop_polling()


def test_opening_a_second_face_retires_the_first(app):
    """Ten double-clicks used to leave ten pollers on one serial line. These dialogs are parented
    to MachineLab, so rebinding the attribute does NOT free the old one."""
    from gini.domain.machine_state import MachineState
    from gini.domain.xv6 import DemoScheduler
    from gini.ui.machine_lab import MachineLab
    ms = MachineState(DemoScheduler(timeslice=1), device_id="d")
    hub = MachineLab(None, _theme(app), _Dev(), state=ms)
    try:
        hub._open_memory_lab()
        first = hub._memory
        assert first is not None
        hub._open_memory_lab()
        assert hub._memory is not first, "did not open a new face"
        try:
            still_polling = first._poll.isActive()
        except RuntimeError:
            still_polling = False                 # C++ object gone: retired properly
        assert not still_polling, "the previous Memory face is still polling"
    finally:
        for attr in ("_memory",):
            w = getattr(hub, attr, None)
            if w is not None:
                w.stop_polling()
                w.close()
        hub.close()
