"""xv6 peripherals on the canvas: hard link-blocking + double-click resolves to the wired xv6."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_peripheral_resolves_to_its_wired_xv6(app):
    w = MainWindow(app)
    k = w.api.add_device("xv6", x=0, y=0)["id"]
    term = w.api.add_device("terminal", x=200, y=0)["id"]
    w.ctx.add_link(k, term)
    assert w._xv6_for_peripheral(term) == k


def test_unwired_peripheral_resolves_to_none(app):
    w = MainWindow(app)
    term = w.api.add_device("terminal", x=0, y=0)["id"]
    assert w._xv6_for_peripheral(term) is None


def test_open_unwired_peripheral_logs_hint_not_crash(app):
    # double-clicking a peripheral with no xv6 should log a friendly hint, not raise
    w = MainWindow(app)
    term = w.api.add_device("terminal", x=0, y=0)["id"]
    w._open_peripheral(term)            # must not raise


class _Kernel:
    """A live-shaped provider: the Terminal only needs a console to be openable."""

    def console(self):
        return "xv6 kernel is booting\n$ "

    def console_since(self, cur):
        return ("", cur)

    def snapshot(self):
        from gini.domain.xv6 import Snapshot
        return Snapshot(procs=[], running_pid=None, ticks=0)


def _wired(app, kernel=None):
    """A Machine with a Terminal wired to it. With `kernel`, the bridge is registered BEFORE the
    MachineState is created — which is how the running app does it, and the only way the state
    comes up in Real mode (`_machine_state_for`: Real when a live bridge exists at open)."""
    w = MainWindow(app)
    k = w.api.add_device("xv6", x=0, y=0)["id"]
    term = w.api.add_device("terminal", x=200, y=0)["id"]
    w.ctx.add_link(k, term)
    if kernel is not None:
        w._running = True
        w._xv6_providers = {k: kernel}
    return w, k, term


def test_double_clicking_a_terminal_opens_it_once_a_kernel_is_attached(app):
    """The guard here read `getattr(ms, "live", False)`, and MachineState has never had a `live`
    attribute — `live` is a WIDGET flag that MachineLab, CpuLab and LockLab each set on
    themselves. So the expression was always False, `not False` was always True, and the `or`
    short-circuited: this returned EVERY time, however healthy the machine. A double-click opened
    nothing and logged "Start the topology (Run)" at a topology that was already running.

    It survived several minor versions because the message reads like an explanation.
    """
    w, k, term = _wired(app, _Kernel())
    ms = w._machine_state_for(k)
    assert ms.has_real() and hasattr(ms.provider, "console"), "test setup is not live"
    w._peripheral = None
    w._open_peripheral(term)
    assert w._peripheral is not None, "a wired Terminal on a running kernel did not open"
    w._peripheral.close()


def test_a_terminal_with_no_kernel_still_says_to_run_the_topology(app):
    """The other half — the guard must keep refusing when there IS nothing to talk to."""
    w, k, term = _wired(app)
    said = []
    w.ctx.log = lambda msg, lvl="info": said.append(msg)
    w._peripheral = None
    w._open_peripheral(term)
    assert w._peripheral is None
    assert any("Run" in m for m in said), said


def test_demo_mode_is_not_reported_as_a_topology_that_needs_running(app):
    """Two causes, two sentences. A kernel IS attached and the Machine is merely being viewed in
    Demo mode — telling somebody to Run a topology that is already running sends them to fix the
    wrong thing."""
    w, k, term = _wired(app, _Kernel())
    ms = w._machine_state_for(k)
    ms.set_mode("demo")
    said = []
    w.ctx.log = lambda msg, lvl="info": said.append(msg)
    w._peripheral = None
    w._open_peripheral(term)
    assert w._peripheral is None
    assert any("Demo" in m for m in said), said
    assert not any("Run)" in m for m in said), said
