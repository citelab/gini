"""xv6 Machine changes the AI modes: Wizard is disabled while an xv6 Machine is present
(signed off), and Coach becomes the state-grounded OS tutor."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


class FakeBackend:
    def available(self):
        return True


class FakeLoop:
    def __init__(self):
        self.backend = FakeBackend()
        self.brief = ""

    def send(self, prompt, on_text=None):
        return ""


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def test_wizard_disabled_when_xv6_present():
    app, w = _win()
    a = w.assistant
    a.set_loop(FakeLoop())
    assert a._wizard_btn.isEnabled()                       # model present, no xv6 -> Wizard on
    w.ctx.add_device("xv6", x=0, y=0)
    assert not a._wizard_btn.isEnabled()                   # xv6 present -> Wizard off
    assert a._coach_btn.isEnabled()                        # Coach stays on (OS tutor)


def test_wizard_mode_falls_back_to_chat_when_xv6_added():
    app, w = _win()
    a = w.assistant
    a.set_loop(FakeLoop())
    a._wizard_btn.setChecked(True)
    assert a.wizard_mode
    w.ctx.add_device("xv6", x=0, y=0)                      # gating kicks in on topology_changed
    assert not a.wizard_mode
    assert a._chat_btn.isChecked()


def test_coach_routes_to_os_coach_for_xv6():
    app, w = _win()
    a = w.assistant
    a.set_loop(FakeLoop())
    did = w.ctx.add_device("xv6", x=0, y=0).id
    # register a live-ish MachineState so the assistant sees an active xv6 focus
    ms = w._machine_state_for(did)
    w.ctx.selected_id = did
    assert a._active_xv6_state() is ms
    before = ms.ledger.used
    a._run_coach()                                         # -> _run_os_coach path
    assert ms.ledger.used == before + 1                   # a measured hint was spent + logged
    assert ms.ledger.log


def _armed(w, a):
    from gini.domain.machine_state import OsEvent
    a.set_loop(FakeLoop())
    did = w.ctx.add_device("xv6", x=0, y=0).id
    ms = w._machine_state_for(did)
    w.ctx.selected_id = did
    return ms, OsEvent


def test_proactive_coach_fires_on_new_teachable_event():
    app, w = _win()
    a = w.assistant
    ms, OsEvent = _armed(w, a)
    a.coach_mode = True
    used0 = ms.ledger.used
    ms._emit([OsEvent("cpu_monopoly", "pid 3 is hogging the CPU", 3)])  # -> bus -> proactive
    assert ms.ledger.used == used0 + 1                     # coached without being asked


def test_xv6_badge_tracks_container_state():
    # regression: role "xv6" was missing from the status mapping, so the badge stuck on "idle"
    from gini.services.compiler import _svc
    app, w = _win()
    dev = w.ctx.add_device("xv6", x=0, y=0)
    node = w.canvas.scene_.nodes[dev.id]
    assert node.status == "idle"
    w._running = True
    w._on_runtime_status({_svc(dev.name): "running", "other": "running"})
    assert node.status == "running"                       # reflects the live container now
    w._on_runtime_status({_svc(dev.name): "exited", "other": "running"})
    assert node.status == "error"                         # a crashed kernel shows error, not idle


def test_peripheral_badge_mirrors_its_wired_xv6():
    # regression: terminal/storage peripherals have no container, so they stuck on "idle" even
    # while their xv6 ran. They now mirror the wired Machine's status.
    from gini.services.compiler import _svc
    app, w = _win()
    dev = w.ctx.add_device("xv6", x=0, y=0)
    term = w.ctx.add_device("terminal", x=200, y=0)
    vol = w.ctx.add_device("storage_volume", x=200, y=200)
    w.ctx.add_link(dev.id, term.id)
    w.ctx.add_link(dev.id, vol.id)
    tnode = w.canvas.scene_.nodes[term.id]
    vnode = w.canvas.scene_.nodes[vol.id]
    assert tnode.status == "idle" and vnode.status == "idle"
    w._running = True
    w._on_runtime_status({_svc(dev.name): "running"})
    assert tnode.status == "running" and vnode.status == "running"   # light up with the Machine


def test_unwired_peripheral_stays_idle_while_xv6_runs():
    from gini.services.compiler import _svc
    app, w = _win()
    dev = w.ctx.add_device("xv6", x=0, y=0)
    term = w.ctx.add_device("terminal", x=200, y=0)     # NOT wired to the xv6
    tnode = w.canvas.scene_.nodes[term.id]
    w._running = True
    w._on_runtime_status({_svc(dev.name): "running"})
    assert tnode.status == "idle"                        # nothing to mirror -> stays idle


def test_no_proactive_coach_when_coach_off_or_control_only():
    app, w = _win()
    a = w.assistant
    ms, OsEvent = _armed(w, a)
    a.coach_mode = False
    ms._emit([OsEvent("starvation", "pid 4 starving", 4)])
    assert ms.ledger.used == 0                             # Coach off -> silent
    a.coach_mode = True
    ms.set_timeslice(50)                                   # a control change -> not a nag
    assert ms.ledger.used == 0
