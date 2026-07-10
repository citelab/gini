"""M2: behavioral probes wired for name-agnostic missions — TypeRunner resolves type tokens to the
student's devices, the 'private database' mission completes offline (structural) and turns fully green
on Run, and the panel exposes a Run button only when there's something to run."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gini.agent.mission import Mission
from gini.domain import catalog as C, fragment_yaml as FY, fragments as F, lesson as L
from gini.domain import objectives as O, probes as P
from gini.domain.topology import Topology


def _private_db():
    t = Topology()
    vpc = t.add_device("vpc", "V")
    w = t.add_device("web_app", "Wname", parent_id=vpc.id)
    d = t.add_device("database", "Dname", parent_id=vpc.id)
    t.add_link(w.id, d.id)
    return t, w, d


def test_type_runner_resolves_tokens_existentially():
    t, w, d = _private_db()
    base = P.FakeRunner({("reach", "Wname", "Dname", None): True})
    tr = P.TypeRunner(base, lambda: t)
    assert tr.reach("web_app", "database")           # SOME web_app reaches SOME database
    assert not tr.reach("cloud", "database")          # no cloud device present → unreachable


def test_private_db_completes_offline_but_goes_full_on_run():
    les = L.from_archetype("reachability-boundary", {}, id="rb")
    m = Mission(les); m.start()
    t, w, d = _private_db()
    res = m.evaluate(O.TopologyWorld(t))              # offline, no runner
    assert m.complete                                 # structural at_least(3) → completable offline
    assert sum(r.met for r in res) == 3
    runner = P.TypeRunner(P.FakeRunner({("reach", "Wname", "Dname", None): True}), lambda: t)
    res2 = m.evaluate(O.TopologyWorld(t), runner)     # Run → behavioral resolve
    assert all(r.met for r in res2)                   # reach/shield turned green


def test_behavioral_probes_are_validated_on_load():
    loaded = FY.load_dir(os.path.join(os.path.dirname(F.__file__), "missions", "networking"))
    rb = loaded["reachability-boundary"]
    kinds = {o.kind for o in rb.objectives}
    assert "behavioral" in kinds
    bad = FY.fragment_from_dict({"id": "x", "layer": "core",
                                 "objectives": [{"id": "b", "kind": "behavioral", "probe": "nonsense("}]})
    assert FY.validate(bad)                           # unparseable probe is caught


def test_panel_shows_run_button_only_for_behavioral_missions():
    from PySide6.QtWidgets import QApplication
    from gini.ui.mission_panel import MissionPanel
    app = QApplication.instance() or QApplication([])
    panel = MissionPanel()
    panel.set_mission(Mission(L.from_archetype("reachability-boundary", {}, id="rb")))
    assert panel._run_btn.isVisible() or not panel._run_btn.isHidden()   # has behavioral → Run shown
    panel2 = MissionPanel()
    panel2.set_mission(Mission(L.from_archetype("basic-lan", {}, id="lan")))
    assert panel2._run_btn.isHidden()                 # structural-only → no Run button
