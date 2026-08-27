"""Run a callable on the GUI thread and wait for its result.

Qt's rule is absolute: widgets and the objects the GUI thread iterates belong to the GUI
thread. Signals cross threads safely because Qt queues them — but a plain function call
from a worker does not, and neither does a dict insert.

That is the hole this closes. An LLM turn runs on a worker thread (`assistant._ask_async`),
and the tools it calls insert into `topology.devices` / `topology.links`. The GUI thread
iterates those same dicts on every canvas paint, in the advisory lint, in the compiler and
in the minimap. A build landing mid-paint raises `RuntimeError: dictionary changed size
during iteration` — reachable by nothing more exotic than moving the mouse while GINI
assembles a recipe.

The agent needs each tool's RETURN VALUE to continue, so a fire-and-forget queued signal is
not enough: the worker has to block until the GUI thread has run the call and produced a
result. That is what `GuiDispatcher` does — a queued signal carrying a job, plus an Event
the worker waits on.

Deliberately in `ui/`, not `agent/`: the agent package stays free of Qt so it can run
headless (tests, the MCP server), where `ToolRegistry.dispatch` is left None and handlers
run inline.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication

# A wedged GUI thread must not hang the agent forever. Long enough that no honest handler
# hits it (they are dict writes and signal emits), short enough to fail visibly.
_TIMEOUT_S = 10.0


class _Job:
    __slots__ = ("fn", "done", "result", "error")

    def __init__(self, fn):
        self.fn = fn
        self.done = threading.Event()
        self.result = None
        self.error: BaseException | None = None


class GuiDispatcher(QObject):
    """`dispatcher(fn)` runs `fn` on the GUI thread and returns what it returned."""

    _job_ready = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Queued explicitly: the default would run the slot inline when the emitter happens
        # to be the GUI thread, which is right, but being explicit documents that the
        # cross-thread case is the one that matters.
        self._job_ready.connect(self._run, Qt.QueuedConnection)

    def _run(self, job: _Job) -> None:
        try:
            job.result = job.fn()
        except BaseException as e:      # noqa: BLE001 — re-raised on the calling thread
            job.error = e
        finally:
            job.done.set()

    def _on_gui_thread(self) -> bool:
        app = QApplication.instance()
        return app is not None and QThread.currentThread() is app.thread()

    def __call__(self, fn):
        # Already on the GUI thread: call straight through. Going via the queue here would
        # deadlock — the slot cannot run until this call returns and the event loop spins.
        if self._on_gui_thread():
            return fn()

        job = _Job(fn)
        self._job_ready.emit(job)
        if not job.done.wait(_TIMEOUT_S):
            raise TimeoutError(
                "the GUI thread did not run an agent tool call within "
                f"{_TIMEOUT_S:g}s — it is blocked or the event loop has stopped")
        if job.error is not None:
            raise job.error
        return job.result
