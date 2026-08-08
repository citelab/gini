"""Running slow hardware work off the GUI thread, safely.

Extracted from board_dialog so that every hardware dialog shares ONE copy. The rules
below are not stylistic — each one is a bug that aborted the whole process when it was
found by running the setup dialog headless, and re-deriving them per dialog is how they
come back:

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

import warnings

from PySide6.QtCore import QObject, QThread

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
