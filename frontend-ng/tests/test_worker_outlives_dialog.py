"""A query thread must not crash when its dialog is destroyed underneath it.

Introduced by the fix for the leaked pollers: _retire_lab closes and DELETES the previous Router
Lab, but a query can be sitting in `docker compose exec` for up to 12 seconds. When it finishes
and emits into the destroyed dialog:

    Exception in thread Thread-176 (work):
      File ".../ui/router_lab.py", line 490, in work
        self.routes_ready.emit(rows)
    RuntimeError: Signal source has been deleted

An unhandled traceback on the console, and the worker dies without finishing its round — so the
in-flight counter is never released and polling stops for good on that dialog.

There is no reliable way to ask from another thread whether a QObject is still alive: checking and
then emitting is a race, because the deletion can land between the two. So the emit itself is the
check, and its failure is a normal outcome rather than an error.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Dev:
    name = "r1"
    id = "d1"
    type_key = "router"


def _lab(app):
    from gini.domain.router_modules import RouterProgram
    from gini.ui.router_lab import RouterLab
    from gini.ui.theme import ThemeManager
    if not hasattr(_lab, "_t"):
        _lab._t = ThemeManager(app)
    return RouterLab(None, _lab._t, _Dev(), RouterProgram(), query_fn=lambda c: "")


def test_emitting_into_a_deleted_dialog_is_not_fatal(app):
    """The reported crash, reproduced exactly: destroy the dialog, then emit as a late worker
    would."""
    from shiboken6 import delete
    lab = _lab(app)
    sig = lab.routes_ready
    emit = lab._emit
    delete(lab)
    assert emit(sig, []) is False, "a late emit into a deleted dialog must report failure"


def test_a_live_dialog_still_receives_the_result(app):
    """The guard must not swallow real results — that would leave every table empty."""
    lab = _lab(app)
    got = []
    lab.routes_ready.connect(lambda rows: got.append(rows))
    assert lab._emit(lab.routes_ready, []) is True
    assert got == [[]], "the result never reached the dialog"
    lab.close()


def test_a_worker_stops_early_when_its_dialog_has_gone(app):
    """_emit returns False so a worker can abandon the rest of its queries. Carrying on would run
    a 12s docker exec for a window nobody can see — the very waste the retire fix removed."""
    import inspect

    from gini.ui.router_lab import RouterLab
    src = inspect.getsource(RouterLab._refresh_routes)
    assert "if not self._emit(" in src and "return" in src, (
        "the routes worker does not bail out when the dialog is gone; it will run its second "
        "query for a destroyed window")


def test_every_worker_emit_goes_through_the_guard(app):
    """One raw .emit() left in a worker reintroduces the crash on exactly the path nobody tests."""
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "gini" / "ui" / "router_lab.py"
    raw = []
    for i, line in enumerate(src.read_text().splitlines(), 1):
        m = re.search(r"self\.(\w*_ready|worker_done)\.emit\(", line)
        if m and "_emit(" not in line:
            raw.append(f"{i}: {line.strip()}")
    assert not raw, "raw signal emits from worker threads:\n  " + "\n  ".join(raw)


# -- the OTHER death mode: deleteLater() under an in-flight emit -------------------------------- #
# Pinning the owner (run_off_gui) stops Python destroying a widget on a worker thread. It cannot
# stop `deleteLater()` destroying the C++ object under an emit already in flight, because C++
# teardown does not consult Python references. Only waiting does. These pin that wait.
#
# The reason this is tested rather than clicked: it is a race. It does not reproduce on demand, so
# "the app did not crash" is not evidence. What IS evidence is that retire demonstrably BLOCKS for
# the duration of a worker that is still running.
import threading
import time


def _slow_worker(owner, seconds, started):
    from gini.ui.worker_host import run_off_gui

    def work():
        started.set()
        time.sleep(seconds)
    run_off_gui(owner, work)


def test_join_owner_waits_for_a_worker_that_is_still_running(app):
    from gini.ui.worker_host import join_owner, owner_thread_count
    w = QtWidgets.QWidget()
    started = threading.Event()
    _slow_worker(w, 0.4, started)
    assert started.wait(2.0), "the worker never started"
    assert owner_thread_count(w) == 1                 # registered while in flight

    t0 = time.monotonic()
    left = join_owner(w, timeout=3.0)
    waited = time.monotonic() - t0

    assert left == 0, "join returned with a worker still running"
    assert waited >= 0.3, f"join did not actually wait (returned in {waited:.2f}s)"
    assert owner_thread_count(w) == 0                 # and it stops advertising them
    w.deleteLater()


def test_join_owner_is_bounded_so_a_wedged_read_cannot_freeze_the_ui(app):
    """An unbounded join would hang the window on any stuck read. The bound is the trade."""
    from gini.ui.worker_host import join_owner
    w = QtWidgets.QWidget()
    started = threading.Event()
    _slow_worker(w, 0.6, started)
    assert started.wait(2.0)
    t0 = time.monotonic()
    left = join_owner(w, timeout=0.1)                 # far shorter than the work
    waited = time.monotonic() - t0
    assert waited < 0.4, f"join ignored its bound ({waited:.2f}s)"
    assert left == 1, "a straggler must be reported, not silently claimed as joined"
    # Reap it before the test returns — conftest's _no_leaked_threads fails any test that leaves a
    # thread running, and it is right to: that guard is what makes the rest of this file mean
    # anything. The straggler is the POINT of the test, so it is waited out here rather than left.
    join_owner(w, timeout=3.0)
    w.deleteLater()


def test_a_worker_is_joinable_from_the_instant_it_is_started(app):
    """Registered BEFORE start(): a retire landing in the gap would see nothing to wait for and
    destroy the owner under a worker that had already begun."""
    from gini.ui.worker_host import join_owner, owner_thread_count, run_off_gui
    w = QtWidgets.QWidget()
    seen = []
    run_off_gui(w, lambda: seen.append(owner_thread_count(w)))
    join_owner(w, timeout=2.0)
    assert seen == [1], f"worker could not see itself as joinable: {seen}"
    w.deleteLater()


def test_join_owner_on_an_owner_with_no_workers_is_a_no_op(app):
    from gini.ui.worker_host import join_owner
    w = QtWidgets.QWidget()
    assert join_owner(w) == 0
    w.deleteLater()


def test_retiring_a_lab_waits_for_its_in_flight_read(app):
    """THE production path, end to end: MainWindow._retire_lab must not reach deleteLater() while
    a worker is still inside a read. Driven through the real helper rather than a copy of it."""
    from gini.ui.worker_host import join_owner
    holder = QtWidgets.QWidget()
    lab = QtWidgets.QWidget(holder)
    started = threading.Event()
    _slow_worker(lab, 0.4, started)
    assert started.wait(2.0)

    # exactly _retire_lab's order: stop, close, JOIN, then destroy
    t0 = time.monotonic()
    lab.close()
    left = join_owner(lab)
    waited = time.monotonic() - t0
    lab.setParent(None)
    lab.deleteLater()
    app.processEvents()

    assert left == 0 and waited >= 0.3, (
        f"retire reached deleteLater() with a worker still running (waited {waited:.2f}s)")
