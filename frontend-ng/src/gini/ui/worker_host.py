"""Running slow work off the GUI thread, safely.

Two mechanisms, one subject. `WorkerHost` (rules 1-3) is the QThread + worker-QObject pair the
hardware dialogs use for serial work. `run_off_gui` (rule 4, at the foot of this file) is the
plain daemon thread every lab, HUD and dialog uses for a docker exec or an HTTP read. They share
a file because they share the failure: something outlives the widget that started it.

Rules 1-3 were extracted from board_dialog so that every hardware dialog shares ONE copy. They
are not stylistic — each one is a bug that aborted the whole process when it was found by running
the setup dialog headless, and re-deriving them per dialog is how they come back:

1. **Hold a Python reference to the worker AND the thread.** PySide does not keep a
   worker alive merely because a signal is connected to it, so a worker passed in as a
   temporary is collected the moment the call returns; the thread then sits in its event
   loop forever and nothing is ever emitted.
2. **Connect only to BOUND METHODS of the dialog, never to a closure.** A closure has no
   thread affinity, so Qt chooses a direct connection and the "off-thread" handler runs
   ON the worker thread, touching widgets from outside the GUI thread.
3. **Never parent the QThread to the dialog.** Serial work is slow — a board reboots when
   its port is opened — so the dialog can easily be closed mid-run; destroying a running
   QThread makes Qt abort. Unparented, the module registry holds it until it finishes.

A host must provide `_alive: bool` and a `_worker_failed(str)` slot.
"""
from __future__ import annotations

import itertools
import threading
import traceback
import warnings

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot

# Workers and their threads outlive the dialog that started them, so they need an owner
# that is not the dialog. Entries retire themselves when the thread ends.
_LIVE_THREADS: set = set()          # {(QThread, worker)}


def drain(timeout_ms: int = 3000) -> int:
    """Stop and reap every live worker thread. Returns how many were still running.

    `_detach` makes a running worker harmless to the DIALOG, but the thread itself is
    still owned by the registry and is only retired when its `finished` signal is
    delivered — which needs an event loop to be turning. At application shutdown (or at
    the end of a test run) there may be none left, and Python then garbage-collects a
    QThread that is still running, which is precisely the "Destroyed while thread is
    still running" abort that this module exists to prevent. So ask them to stop, and
    wait.
    """
    stragglers = 0
    for thread, _worker in list(_LIVE_THREADS):
        try:
            if thread.isRunning():
                stragglers += 1
                thread.quit()
                thread.wait(timeout_ms)
        except RuntimeError:
            pass                      # already deleted by Qt; nothing to reap
        _LIVE_THREADS.discard((thread, _worker))
    return stragglers


class WorkerHost:
    """Mixin: `self._run(worker, on_done)` and `self._detach()`."""

    def _run(self, worker: QObject, on_done) -> None:
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        entry = (thread, worker)                 # rule 1
        _LIVE_THREADS.add(entry)
        self._worker = worker

        worker.done.connect(on_done)             # rule 2: a bound method of the dialog
        worker.done.connect(thread.quit)
        self._connections = [(worker.done, on_done)]
        if hasattr(worker, "failed"):
            worker.failed.connect(self._worker_failed)
            worker.failed.connect(thread.quit)
            self._connections.append((worker.failed, self._worker_failed))
        if hasattr(worker, "progress") and hasattr(self, "_worker_progress"):
            worker.progress.connect(self._worker_progress)
            self._connections.append((worker.progress, self._worker_progress))
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: _LIVE_THREADS.discard(entry))
        thread.finished.connect(thread.deleteLater)
        self._thread = thread                    # rule 3: unparented
        thread.start()

    def _detach(self) -> None:
        """Cut every path from a running worker back into this dialog.

        An `_alive` flag alone is not enough: if the dialog is destroyed while a worker is
        still going, the queued call lands on a freed C++ object and takes the process
        with it. Only OUR handlers are disconnected — `thread.quit` stays connected, or
        the thread would never stop.
        """
        self._alive = False
        for signal, slot in getattr(self, "_connections", []):
            # A worker that already finished has dropped its connections, and PySide
            # reports that as a warning rather than an exception. Closing a dialog after
            # a successful run is the common case, so this must be silent.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._connections = []


# --------------------------------------------------------------------------------------------- #
# Rule 4, and the reason this section exists: a plain `threading.Thread` closure must never be
# the last owner of the widget it reports back to.
#
# Most background work in this app is not a WorkerHost QThread — it is the small, uniform
# `threading.Thread(target=work, daemon=True)` that every lab, HUD and dialog uses to keep a
# docker exec or an HTTP read off the GUI thread. That shape has one flaw, and it is not the
# `self._closed` check those closures all make. It is OWNERSHIP:
#
#     def work():
#         txt = self._src()          # `work` closes over self — a strong reference
#         if not self._closed:
#             self.ready.emit(txt)   # queued to the GUI thread; fine on its own
#     threading.Thread(target=work, daemon=True).start()
#
# A parentless dialog is owned by Python. Close it and drop the last GUI-side reference — which
# is exactly what happens when a lab window is replaced, or when a test function returns — and
# the only reference left is the one inside `work`. The widget's refcount then reaches zero when
# the WORKER THREAD's frame is torn down, so PySide destroys a QWidget off the GUI thread. Qt
# forbids that, and the symptom is a segfault somewhere else entirely: in the next
# `processEvents()`, in an unrelated test, at a different point on every run.
#
# Measured before the fix: a loop of 30 open/close cycles destroyed 30 of 30 widgets on a worker
# thread, 0 on the GUI thread.
#
# `_closed` cannot help — it is a Python attribute and still reads perfectly well after the C++
# object is gone. Nor can a weakref inside the closure, on its own: dereferencing it to emit
# re-creates a strong reference on the worker thread, and the same teardown can land there.
#
# So the fix is to give the GUI side an owner that outlives the worker. `run_off_gui` holds the
# widget in a module-level registry for the duration of the call, and the worker releases it by
# emitting to a singleton reaper that lives on the GUI thread. The worker drops its own reference
# BEFORE it asks for that release, so no interleaving leaves it holding the last one. The widget
# is then destroyed where every widget must be: on the GUI thread.
# --------------------------------------------------------------------------------------------- #

_HELD: dict[int, object] = {}          # token -> owner, kept alive while its worker runs
#: id(owner) -> {threads still running for it}. The SECOND death mode needs this: pinning the
#: owner stops Python destroying it on a worker thread, but it cannot stop `deleteLater()`
#: destroying the C++ object under an in-flight `emit()`, because C++ teardown does not consult
#: Python references. Only waiting does. See `join_owner`.
_BY_OWNER: dict[int, set] = {}
_HELD_LOCK = threading.Lock()
_TOKENS = itertools.count()

#: How long a retire may wait for one face's workers. Long enough for a docker exec or an HTTP
#: read to return, short enough that closing a window never feels stuck. Mirrors live_poll's.
JOIN_TIMEOUT = 2.0


def join_owner(owner: object, timeout: float = JOIN_TIMEOUT) -> int:
    """Wait for every `run_off_gui` worker this owner started. Returns how many were STILL running.

    Call it after the owner has been told to stop (so no further emit can be issued) and BEFORE
    `deleteLater()`. That order is the whole point: the flag stops new emits, and this waits out
    the one already in flight, so nothing is mid-`emit()` when Qt frees the receiver.

    Bounded on purpose. A worker that outlives the bound is not left dangerous — it still holds no
    strong reference to the owner by then (see `guarded`), and the owner's own `_closed` guard
    stops it emitting — so the bound trades a vanishingly rare race for a window that never
    freezes. An unbounded join would hang the GUI on any wedged read.
    """
    with _HELD_LOCK:
        threads = list(_BY_OWNER.get(id(owner), ()))
    left = 0
    for t in threads:
        if t.is_alive():
            t.join(timeout)
            if t.is_alive():
                left += 1
    with _HELD_LOCK:
        _BY_OWNER.pop(id(owner), None)
    return left


def owner_thread_count(owner: object) -> int:
    """How many workers this owner has in flight. For tests and leak checks; never for logic."""
    with _HELD_LOCK:
        return len(_BY_OWNER.get(id(owner), ()))


class _Reaper(QObject):
    """Drops a held reference ON THE GUI THREAD.

    A module-level singleton, deliberately: it must outlive every widget it releases, and it must
    live in the GUI thread so the queued `released` emission is delivered there. Nothing else in
    the process holds a reference to it, which is also why it can never be the object whose
    destruction is being made safe.
    """

    released = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.released.connect(self._drop)    # cross-thread emit -> queued -> runs on the GUI thread

    @Slot(int)
    def _drop(self, token: int) -> None:
        with _HELD_LOCK:
            owner = _HELD.pop(token, None)
        del owner                            # the last reference may die here, and here is correct


_reaper: "_Reaper | None" = None


def _the_reaper() -> "_Reaper | None":
    """The singleton, built lazily on the GUI thread (every caller of `run_off_gui` is on it)."""
    global _reaper
    if _reaper is None and QCoreApplication.instance() is not None:
        _reaper = _Reaper()
    return _reaper


def held_count() -> int:
    """How many owners are currently pinned. For tests and for a leak check; never for logic."""
    with _HELD_LOCK:
        return len(_HELD)


def run_off_gui(owner: QObject, work, *args) -> None:
    """Run `work(*args)` on a daemon thread, keeping `owner` alive and GUI-thread-owned.

    A drop-in for `threading.Thread(target=work, daemon=True).start()` in any method of a QObject
    or QWidget — same thread, same daemon semantics, same `self._closed` guards inside `work`. The
    only difference is who holds the last reference to `owner`; see the note above for why that is
    the whole bug.

    Exceptions out of `work` are printed rather than propagated. That is not a style choice: an
    escaping exception keeps a traceback, a traceback keeps `work`'s frame, and that frame is the
    strong reference this function exists to get rid of — so it would be released on the worker
    thread after all, which is the crash.
    """
    reaper = _the_reaper()
    if reaper is None:                       # no QApplication: no widgets, nothing to protect
        threading.Thread(target=work, args=args, daemon=True).start()
        return

    token = next(_TOKENS)
    with _HELD_LOCK:
        _HELD[token] = owner
    box = [work, args]                       # the worker's ONLY strong path to `owner`
    key = id(owner)
    cell: list = []                          # holds this thread, so `guarded` can retire itself

    def guarded() -> None:
        try:
            box[0](*box[1])
        except Exception:                    # noqa: BLE001 — see the docstring: never propagate
            traceback.print_exc()
        finally:
            box[0] = box[1] = None           # drop the closure, and the widget with it …
            with _HELD_LOCK:                 # … stop advertising ourselves as joinable …
                s = _BY_OWNER.get(key)
                if s is not None:
                    s.discard(cell[0] if cell else None)
                    if not s:
                        _BY_OWNER.pop(key, None)
            reaper.released.emit(token)      # … and only then ask the GUI thread to let go

    # Named after the owner, because `_no_leaked_threads` in conftest reports the thread NAME
    # when something outlives its test — and "Thread-47" tells nobody which face to go and look at.
    th = threading.Thread(target=guarded, daemon=True, name=f"{type(owner).__name__}-bg")
    cell.append(th)
    # Registered BEFORE start(): a retire that lands between start() and registration would see
    # nothing to join and destroy the owner under a worker that had already begun.
    with _HELD_LOCK:
        _BY_OWNER.setdefault(key, set()).add(th)
    th.start()
