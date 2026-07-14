"""The Mission state machine: lifecycle, injected-clock timing, live completion → auto-witness,
attempts/retry, and save/resume — all deterministic (no wall clock, no Qt)."""
from gini.agent import mission as M
from gini.domain import lesson as L, objectives as O
from gini.domain.topology import Topology


def _lesson(**over):
    return L.from_archetype("basic-lan", {"h1": "M1", "h2": "M2", "sw": "S1", "gw": "R1"},
                            id="lab01", **over)


def _complete_world():
    t = Topology()
    m1 = t.add_device("host", "M1"); m2 = t.add_device("host", "M2")
    s1 = t.add_device("switch", "S1"); r1 = t.add_device("router", "R1")
    t.add_link(m1.id, s1.id); t.add_link(m2.id, s1.id); t.add_link(s1.id, r1.id)
    return O.TopologyWorld(t)


def test_lifecycle_and_life_spend():
    clock = [0.0]
    m = M.Mission(_lesson(time_limit="20m", attempts=3), now=lambda: clock[0])
    assert m.state == M.STAGED
    m.brief(); assert m.state == M.BRIEFED
    m.start(); assert m.state == M.PLAYING and m.attempt == 1 and m.lives_left() == 2


def test_live_completion_auto_witnesses_gold():
    clock = [0.0]
    m = M.Mission(_lesson(time_limit="20m"), now=lambda: clock[0])
    m.start()
    m.evaluate(O.TopologyWorld(Topology()))          # empty → not complete
    assert m.state == M.PLAYING and not m.complete
    clock[0] = 120.0
    m.evaluate(_complete_world())                    # complete within time → gold, done
    assert m.complete and m.state == M.DONE and m.last_band == "gold"


def test_timeout_witnesses_incomplete_and_allows_retry():
    clock = [0.0]
    m = M.Mission(_lesson(time_limit="10m", attempts=2), now=lambda: clock[0])
    m.start()
    clock[0] = 601.0                                 # past the 600s limit
    m.evaluate(O.TopologyWorld(Topology()))
    assert m.expired() and m.state == M.WITNESSED and m.last_band == "incomplete"
    assert m.can_retry() and m.lives_left() == 1
    m.retry()
    assert m.state == M.PLAYING and m.attempt == 2


def test_out_of_lives_is_done():
    clock = [0.0]
    m = M.Mission(_lesson(time_limit="1m", attempts=1), now=lambda: clock[0])
    m.start()
    clock[0] = 61.0
    m.evaluate(O.TopologyWorld(Topology()))
    assert m.state == M.DONE and not m.can_retry()   # last life spent


def test_check_forces_a_witness():
    clock = [0.0]
    m = M.Mission(_lesson(), now=lambda: clock[0])   # untimed
    m.start()
    sc = m.check(_complete_world())
    assert sc.band == "gold" and m.state == M.DONE


def test_untimed_has_no_deadline():
    m = M.Mission(_lesson(), now=lambda: 999.0)
    m.start()
    assert m.remaining() is None and not m.expired() and m.on_time()


def test_save_resume_roundtrip():
    clock = [0.0]
    m = M.Mission(_lesson(time_limit="20m", attempts=3), now=lambda: clock[0])
    m.start(); clock[0] = 90.0
    d = m.to_dict()
    m2 = M.Mission.from_dict(d, m.lesson, now=lambda: clock[0])
    assert m2.state == M.PLAYING and m2.attempt == 1
    assert m2.lesson.id == "lab01"
