"""The observation schedule — when a plan is evaluated, and what gets recorded.

Two properties are load-bearing. **The schedule is the instrument**: a caller that ticks eagerly
must not sample one student's lab more often than another's. And **only transitions are recorded**:
a chain logging "still unmet" every twenty seconds would be unreadable, and the moment worth keeping
is the one where something started working.

Time is injected, so none of this needs a running lab or a Qt timer.
"""
from __future__ import annotations

from gini.domain import aop as A
from gini.domain import aop_report as RPT
from gini.domain import objectives as O
from gini.services.aop_observer import AopObserver


class Dev:
    def __init__(self, name, type_key):
        self.name, self.type_key = name, type_key
        self.properties, self.slot, self.parent_id = {}, "", None


class Topo:
    def __init__(self, *devices):
        self.devices = {d.name: d for d in devices}
        self.links = {}


class Runner:
    def __init__(self, verdict=True, available=True):
        self.verdict, self._up, self.calls = verdict, available, 0

    def available(self):
        return self._up

    def reach(self, *a, **k):
        self.calls += 1
        return self.verdict

    http = flow = reach

    def backends(self, lb):
        return 9


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class Recorder:
    """Enough of `proof_recorder` to see what would be written."""
    def __init__(self, armed=True):
        self.armed = armed
        self.checks = []

    def note_check(self, results, objectives=None):
        self.checks.append([(r.id, r.status) for r in results])


STRUCT = A.Expectation(id="s", say="A router exists", layer="L2", check="exists('router')")
BEHAV = A.Expectation(id="b", say="Everyone reaches everyone", layer="L3",
                      probe="reach(host -> host, all) == ok", requires=("s",))


def observer(*expectations, topo=None, runner=None, recorder=None, clock=None, cadence=20.0):
    plan = A.Aop(header=A.Header(observation=A.Observation(cadence_s=cadence)),
                 expectations=tuple(expectations or (STRUCT, BEHAV)))
    t = topo if topo is not None else Topo(Dev("R1", "router"))
    return AopObserver(plan, lambda: O.TopologyWorld(t), lambda: runner,
                       recorder=recorder, get_topology=lambda: t,
                       now=clock or Clock()), plan


# -- the cost split ----------------------------------------------------------- #
def test_a_topology_change_evaluates_structural_only():
    r = Runner()
    obs, _ = observer(runner=r)
    obs.on_topology_changed()
    assert r.calls == 0                       # no container was touched
    assert obs.verdicts["s"] == RPT.MET


def test_a_structural_pass_does_not_overwrite_a_measured_behavioural_verdict():
    """Otherwise every canvas edit would downgrade a probe result to 'unobservable', and the
    student would appear to lose work they had already demonstrated."""
    r = Runner(True)
    obs, _ = observer(runner=r)
    obs.on_check()
    assert obs.verdicts["b"] == RPT.MET
    obs.on_topology_changed()
    assert obs.verdicts["b"] == RPT.MET


def test_check_always_runs_a_full_pass():
    r = Runner()
    obs, _ = observer(runner=r)
    obs.on_check()
    assert r.calls > 0


def test_a_run_state_change_re_evaluates_immediately():
    r = Runner()
    obs, _ = observer(runner=r)
    obs.on_run_state(True)
    assert r.calls > 0


# -- the schedule is part of the instrument ----------------------------------- #
def test_the_first_tick_is_always_due():
    r = Runner()
    obs, _ = observer(runner=r)
    obs.tick()
    assert r.calls > 0


def test_an_eager_caller_cannot_sample_more_often_than_the_cadence():
    clock = Clock()
    r = Runner()
    obs, _ = observer(runner=r, clock=clock, cadence=20.0)
    obs.tick()
    calls = r.calls
    for _ in range(50):                       # a caller ticking every frame
        obs.tick()
    assert r.calls == calls


def test_the_cadence_comes_from_the_plan_not_the_observer():
    obs, _ = observer(cadence=5.0)
    assert obs.cadence_s == 5.0


def test_a_tick_after_the_cadence_elapses_runs():
    clock = Clock()
    r = Runner()
    obs, _ = observer(runner=r, clock=clock, cadence=20.0)
    obs.tick()
    calls = r.calls
    clock.advance(21)
    obs.tick()
    assert r.calls > calls


# -- first satisfaction ------------------------------------------------------- #
def test_first_satisfaction_is_when_it_first_flipped():
    clock = Clock()
    r = Runner(False)
    obs, _ = observer(runner=r, clock=clock, cadence=1.0)
    obs.on_check()
    assert "b" not in obs.first_satisfied      # the probe fails; the structural one already passed
    assert obs.first_satisfied["s"] == clock.t
    clock.advance(2)
    r.verdict = True
    obs.tick()
    assert obs.first_satisfied["b"] == clock.t


def test_first_satisfaction_is_not_overwritten_by_later_passes():
    clock = Clock()
    r = Runner(True)
    obs, _ = observer(runner=r, clock=clock, cadence=1.0)
    obs.on_check()
    first = obs.first_satisfied["b"]
    clock.advance(60)
    obs.tick()
    assert obs.first_satisfied["b"] == first


def test_a_regression_is_recorded_separately():
    """Working and then breaking it is a different story from never getting there, and only the
    transitions tell them apart."""
    clock = Clock()
    r = Runner(True)
    obs, _ = observer(runner=r, clock=clock, cadence=1.0)
    obs.on_check()
    clock.advance(2)
    r.verdict = False
    obs.tick()
    assert [eid for eid, _t in obs.regressions] == ["b"]


# -- what reaches the proof chain --------------------------------------------- #
def test_only_transitions_are_recorded():
    clock = Clock()
    rec = Recorder()
    r = Runner(True)
    obs, _ = observer(runner=r, recorder=rec, clock=clock, cadence=1.0)
    obs.on_check()
    assert len(rec.checks) == 1
    clock.advance(2)
    obs.tick()                                 # nothing changed
    assert len(rec.checks) == 1


def test_a_change_is_recorded():
    clock = Clock()
    rec = Recorder()
    r = Runner(False)
    obs, _ = observer(runner=r, recorder=rec, clock=clock, cadence=1.0)
    obs.on_check()
    clock.advance(2)
    r.verdict = True
    obs.tick()
    assert len(rec.checks) == 2


def test_nothing_is_recorded_when_the_chain_is_not_armed():
    rec = Recorder(armed=False)
    obs, _ = observer(runner=Runner(), recorder=rec)
    obs.on_check()
    assert rec.checks == []


def test_a_defective_expectation_never_enters_the_students_chain():
    """It is a fault in the plan. Writing it into the chain would leave a permanent record
    implying they failed something that was never askable."""
    rec = Recorder()
    bad = A.Expectation(id="x", say="broken", layer="L2", check="exists(")
    obs, _ = observer(bad, runner=Runner(), recorder=rec)
    obs.on_check()
    assert obs.verdicts["x"] == RPT.DEFECTIVE
    assert rec.checks == []


def test_a_recorder_that_raises_does_not_break_observation():
    class Boom(Recorder):
        def note_check(self, results, objectives=None):
            raise RuntimeError("disk full")

    obs, _ = observer(runner=Runner(), recorder=Boom())
    obs.on_check()                             # must not propagate
    assert obs.verdicts["s"] == RPT.MET


# -- the deadline ------------------------------------------------------------- #
def test_work_inside_the_deadline():
    clock = Clock()
    plan = A.Aop(header=A.Header(deadline_s=60), expectations=(STRUCT,))
    obs = AopObserver(plan, lambda: O.TopologyWorld(Topo(Dev("R1", "router"))), now=clock)
    clock.advance(30)
    assert obs.within_deadline()


def test_work_past_the_deadline_is_flagged_not_discarded():
    clock = Clock()
    topo = Topo(Dev("R1", "router"))
    plan = A.Aop(header=A.Header(deadline_s=60), expectations=(STRUCT,))
    obs = AopObserver(plan, lambda: O.TopologyWorld(topo), get_topology=lambda: topo, now=clock)
    clock.advance(120)
    assert not obs.within_deadline()
    changed = obs.on_topology_changed()        # still recorded
    assert changed == {"s": RPT.MET}
    assert not obs.report().within_deadline


def test_no_deadline_is_always_within_it():
    obs, _ = observer()
    assert obs.within_deadline()


# -- the report --------------------------------------------------------------- #
def test_the_report_runs_a_fresh_full_pass():
    r = Runner(True)
    obs, plan = observer(runner=r)
    rep = obs.report()
    assert rep.plan_hash == A.plan_hash(plan)
    assert rep.counts[RPT.MET] == 2


def test_the_report_carries_unexplained_work():
    topo = Topo(Dev("R1", "router"), Dev("FW1", "firewall"))
    obs, _ = observer(STRUCT, topo=topo, runner=Runner())
    assert obs.report().unexplained == (("firewall", 1),)


def test_blocked_dependencies_propagate_during_observation():
    topo = Topo(Dev("S1", "switch"))            # no router: the structural expectation fails
    obs, _ = observer(topo=topo, runner=Runner(True))
    obs.on_check()
    assert obs.verdicts["s"] == RPT.UNMET
    assert obs.verdicts["b"] == RPT.BLOCKED
