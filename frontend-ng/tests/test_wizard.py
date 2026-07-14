"""The LLM-driven guided Wizard: requires a model, picks a starter, and walks the build
with model-filtered ghosts. Off-goal drops are flagged; the palette dims off-goal items."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from gini.agent.llm.backend import Chunk
from gini.domain import missions
from gini.ui.canvas import NODE_W
from gini.ui.palette import KEY_ROLE
from gini.ui.main_window import MainWindow


class FakeBackend:
    """Returns scripted replies in call order (one Chunk each)."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.i = 0

    def chat(self, messages, tools=None, stream=False):
        text = self.replies[min(self.i, len(self.replies) - 1)] if self.replies else ""
        self.i += 1
        yield Chunk(text=text)

    def available(self):
        return True


class FakeLoop:
    def __init__(self, backend):
        self.backend = backend

    def send(self, prompt, on_text=None):
        return ""


def _win():
    app = QApplication.instance() or QApplication([])
    return app, MainWindow(app)


def _pump(app, predicate, tries=400):
    for _ in range(tries):
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_wizard_button_disabled_without_a_model():
    app, w = _win()
    a = w.assistant
    assert not a._wizard_btn.isEnabled()              # no model -> Wizard off
    a.set_loop(FakeLoop(FakeBackend([])))
    assert a._wizard_btn.isEnabled()                  # model connected -> Wizard on
    a.set_loop(None)
    assert not a._wizard_btn.isEnabled()


def test_set_goal_without_a_model_does_nothing():
    app, w = _win()
    a = w.assistant
    a._set_mission("a multi-LAN IP network")
    assert w.ctx.mission is None                       # refused — needs a model


def test_wizard_picks_a_starter_and_shows_model_filtered_ghosts():
    app, w = _win()
    a = w.assistant
    a.set_loop(FakeLoop(FakeBackend([
        "K8s Cluster - the foundation everything runs in.",   # starter pick
        "Pod - the workload that runs in the cluster.",       # neighbour filter
    ])))
    a._wizard_btn.setChecked(True)
    a._wz_goal.setText("a cloud service on kubernetes")
    a._set_goal_from_input()

    assert _pump(app, lambda: any(d.type_key == "k8s_cluster"
                                  for d in w.ctx.topology.devices.values()))
    assert _pump(app, lambda: bool(w.canvas._ghosts))
    ghost_types = {g.type_key for g in w.canvas._ghosts}
    assert "pod" in ghost_types                        # model-filtered, grammar-valid neighbour
    # the starter the Wizard placed must NOT be flagged off-goal (single source of truth)
    cl = next(d for d in w.ctx.topology.devices.values() if d.type_key == "k8s_cluster")
    assert not w.canvas.scene_.nodes[cl.id]._off_goal()


def test_tapping_a_ghost_walks_to_the_next_step():
    app, w = _win()
    a = w.assistant
    a.set_loop(FakeLoop(FakeBackend([
        "K8s Cluster - foundation.",
        "Pod - the workload.",
        "Pod Autoscaler - scales the pod.",            # ghosts for the new Pod
    ])))
    a._wizard_btn.setChecked(True)
    a._wz_goal.setText("a cloud service on kubernetes")
    a._set_goal_from_input()
    assert _pump(app, lambda: bool(w.canvas._ghosts))
    pod_ghost = next(g for g in w.canvas._ghosts if g.type_key == "pod")
    w.canvas._activate_ghost(pod_ghost)                # tap -> add Pod + walk
    assert _pump(app, lambda: any(d.type_key == "pod"
                                  for d in w.ctx.topology.devices.values()))
    # the auto-walk requested ghosts for the new Pod
    assert _pump(app, lambda: bool(w.canvas._ghosts))


def test_off_goal_drop_is_flagged_and_removable():
    app, w = _win()
    w.ctx.set_mission(missions.keyword_mission("a multi-LAN IP network"))
    db = w.api.add_device("database", x=200, y=200)["id"]
    app.processEvents()
    node = w.canvas.scene_.nodes[db]
    assert node._off_goal()
    assert node._on_offgoal_badge(QPointF(NODE_W - 15, 14))
    w.ctx.bus.device_delete_requested.emit(db)
    app.processEvents()
    assert db not in w.ctx.topology.devices


def test_clearing_the_goal_resets():
    app, w = _win()
    a = w.assistant
    a.set_loop(FakeLoop(FakeBackend(["Router - separates LANs.", "Switch - a LAN."])))
    a._wizard_btn.setChecked(True)
    a._wz_goal.setText("a multi-LAN IP network")
    a._set_goal_from_input()
    assert _pump(app, lambda: w.ctx.mission is not None)
    a._clear_mission()
    assert w.ctx.mission is None
