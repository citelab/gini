"""Agent tool calls must not mutate the topology from a worker thread.

An LLM turn runs on a worker (`assistant._ask_async`) and its tools insert into
`topology.devices` / `topology.links`. The GUI thread iterates those same dicts on every
canvas paint, so a build landing mid-paint raises `RuntimeError: dictionary changed size
during iteration` — reachable by moving the mouse while GINI assembles a recipe.
"""
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6.QtCore import QThread, QTimer                       # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from gini.agent.tools.registry import ToolRegistry, ToolSpec     # noqa: E402
from gini.ui.gui_dispatch import GuiDispatcher                   # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _from_worker(fn, timeout=5.0):
    """Run `fn` on a worker thread while the GUI thread spins its event loop."""
    box = {}

    def work():
        try:
            box["result"] = fn()
        except BaseException as e:            # noqa: BLE001
            box["error"] = e
        finally:
            box["done"] = True

    t = threading.Thread(target=work, daemon=True)
    t.start()
    # Spin the event loop until the worker finishes — the dispatcher needs it to run jobs.
    deadline = QtCore.QDeadlineTimer(int(timeout * 1000))
    while not box.get("done") and not deadline.hasExpired():
        QApplication.processEvents(QtCore.QEventLoop.AllEvents, 10)
    t.join(timeout)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def test_the_handler_runs_on_the_gui_thread_not_the_worker(app):
    """The whole point: whatever thread calls the tool, the handler executes on the GUI one."""
    gui_thread = app.thread()
    d = GuiDispatcher()
    seen = {}

    def handler():
        seen["ran_on"] = QThread.currentThread()
        return "ok"

    worker_thread = {}

    def call_from_worker():
        worker_thread["t"] = QThread.currentThread()
        return d(handler)

    assert _from_worker(call_from_worker) == "ok"
    assert seen["ran_on"] is gui_thread, "handler must run on the GUI thread"
    assert worker_thread["t"] is not gui_thread, "the test must actually call from a worker"


def test_the_return_value_comes_back_to_the_caller(app):
    """A fire-and-forget queued signal would not do: the agent needs each tool's result to
    decide what to do next, so the worker has to block for it."""
    d = GuiDispatcher()
    assert _from_worker(lambda: d(lambda: {"devices": [1, 2, 3]})) == {"devices": [1, 2, 3]}


def test_an_exception_is_re_raised_on_the_calling_thread(app):
    """`ToolRegistry.execute` turns handler errors into a result the model can read; that
    only works if the exception survives the thread hop."""
    d = GuiDispatcher()

    def boom():
        raise ValueError("handler blew up")

    with pytest.raises(ValueError, match="handler blew up"):
        _from_worker(lambda: d(boom))


def test_calling_from_the_gui_thread_does_not_deadlock(app):
    """The dangerous case: dispatching from the GUI thread itself. Going through the queue
    would wait for a slot that cannot run until this call returns."""
    d = GuiDispatcher()
    done = {}

    def on_gui():
        done["value"] = d(lambda: 42)          # must run inline, not deadlock

    QTimer.singleShot(0, on_gui)
    deadline = QtCore.QDeadlineTimer(3000)
    while "value" not in done and not deadline.hasExpired():
        QApplication.processEvents(QtCore.QEventLoop.AllEvents, 10)
    assert done.get("value") == 42, "dispatching from the GUI thread must not deadlock"


def test_registry_routes_handlers_through_the_dispatcher(app):
    """The wiring, not just the mechanism: ToolRegistry.execute must honour `dispatch`."""
    d = GuiDispatcher()
    gui_thread = app.thread()
    seen = {}

    reg = ToolRegistry()
    reg.register(ToolSpec(name="add_device", description="", parameters={},
                          handler=lambda **kw: seen.setdefault("ran_on",
                                                               QThread.currentThread())))
    reg.dispatch = d
    _from_worker(lambda: reg.execute("add_device", {}))
    assert seen["ran_on"] is gui_thread


def test_without_a_dispatcher_handlers_run_inline(app):
    """Headless callers — tests, the MCP server — must keep working with no Qt involved."""
    reg = ToolRegistry()
    reg.register(ToolSpec(name="ping", description="", parameters={},
                          handler=lambda **kw: "pong"))
    assert reg.dispatch is None
    assert reg.execute("ping", {}) == "pong"


def test_a_handler_error_still_reaches_the_model_as_a_result(app):
    """execute() must keep converting errors into `{"error": …}` across the hop, so a bad
    tool call is something the model can see and correct rather than a dead turn."""
    d = GuiDispatcher()
    reg = ToolRegistry()

    def bad(**kw):
        raise RuntimeError("no such device")

    reg.register(ToolSpec(name="connect", description="", parameters={}, handler=bad))
    reg.dispatch = d
    out = _from_worker(lambda: reg.execute("connect", {}))
    assert out == {"error": "no such device"}
