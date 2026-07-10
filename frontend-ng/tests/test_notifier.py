"""P2: rules-only salience — verdict flips become salient notifications, and the MissionMonitor ties
world-change → re-verify → notify."""
from gini.agent.notifier import MissionMonitor, SALIENCE, top
from gini.domain import assembly as A, lesson as _lesson
from gini.domain.topology import Topology


def _lan(hosts=2, wire=True, extra=None):
    t = Topology()
    sw = t.add_device("switch", "S"); r = t.add_device("router", "R")
    hs = [t.add_device("host", f"H{i}") for i in range(hosts)]
    if wire:
        for h in hs:
            t.add_link(h.id, sw.id)
        t.add_link(sw.id, r.id)
    if extra:
        t.add_device(extra, extra.upper())
    return t


def _mon():
    m = MissionMonitor()
    m.load(A.assemble(["basic-lan"], genre="experience", lesson_id="t"))
    return m


def test_building_the_lan_notifies_objectives_met_and_completion():
    m = _mon()
    notes = m.on_world_change(_lan())
    changes = {n.change for n in notes}
    assert "objective_met" in changes
    assert "mission_complete" in changes
    assert top(notes).salience == SALIENCE["mission_complete"]


def test_off_task_drop_is_high_salience():
    m = _mon()
    m.on_world_change(_lan())                       # settle
    notes = m.on_world_change(_lan(extra="k8s_cluster"))
    off = [n for n in notes if n.change == "off_task_added"]
    assert off and off[0].salience == SALIENCE["off_task_added"]
    assert off[0].subjects                          # carries the offending device id(s)


def test_no_change_emits_nothing():
    m = _mon()
    m.on_world_change(_lan())
    assert m.on_world_change(_lan()) == []          # identical board → no flips → no notifications


def test_forbid_trip_is_maximally_salient():
    les = _lesson.from_dict({
        "id": "fb", "objectives": [{"id": "has-db", "say": "db", "check": "exists(database)"}],
        "forbid": [{"say": "DB must not reach the Internet", "check": "link(database, cloud)"}],
    })
    m = MissionMonitor(); m.load(les)
    t = Topology()
    db = t.add_device("database", "DB"); net = t.add_device("cloud", "NET")
    m.on_world_change(t)
    t.add_link(db.id, net.id)                       # trips the forbid rule
    notes = m.on_world_change(t)
    trip = [n for n in notes if n.change == "forbid_tripped"]
    assert trip and trip[0].salience == 1.0


def test_question_and_run_events():
    m = _mon()
    assert m.on_question("why is this red?").change == "question"
    assert m.on_run_complete().change == "run_complete"
