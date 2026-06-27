"""Deleting devices from the canvas — idle elements are removable, running ones are not."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gini.ui.main_window import MainWindow


def _win() -> MainWindow:
    app = QApplication.instance() or QApplication([])
    return MainWindow(app)


def test_delete_idle_device_and_its_links():
    w = _win()
    r = w.api.add_device("router")["id"]
    h = w.api.add_device("host")["id"]
    w.api.connect(r, h)
    assert len(w.ctx.topology.links) == 1

    w.canvas.scene_.nodes[h].setSelected(True)
    w._delete_selected()

    assert h not in w.ctx.topology.devices          # gone from the model
    assert h not in w.canvas.scene_.nodes           # gone from the canvas
    assert len(w.ctx.topology.links) == 0           # its link was pruned too


def test_cannot_delete_while_running():
    w = _win()
    h = w.api.add_device("host")["id"]
    w.canvas.scene_.nodes[h].setSelected(True)
    w._running = True                               # topology is live
    w._update_delete_enabled()                      # (what _update_status does on Run)
    w._delete_selected()
    assert h in w.ctx.topology.devices              # running element survived
    assert not w._delete_act.isEnabled()            # and the action is disabled

    w._running = False
    w._update_delete_enabled()
    assert w._delete_act.isEnabled()                # idle + selected -> enabled


def test_right_click_delete_path():
    w = _win()
    r = w.api.add_device("router")["id"]
    w.ctx.bus.device_delete_requested.emit(r)       # what the node's context menu emits
    assert r not in w.ctx.topology.devices


def test_delete_action_disabled_without_selection():
    w = _win()
    w.api.add_device("host")
    w.canvas.scene_.clearSelection()
    w._update_delete_enabled()
    assert not w._delete_act.isEnabled()
