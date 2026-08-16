"""Xv6Runner — OS behavioral probes reuse the existing probe grammar/evaluator (no fork)."""
from gini.domain.probes import evaluate, parse
from gini.domain.xv6 import Proc, SchedTimeline, ShadowStatus, Snapshot
from gini.domain.xv6_runner import Xv6Runner


def _timeline(pids):
    tl = SchedTimeline()
    for i, p in enumerate(pids):
        tl.add_run(p, i)
    return tl


def _runner(procs, pids, shadows=None):
    return Xv6Runner(Snapshot(procs=procs), _timeline(pids), shadows or {})


def test_os_winconditions_use_the_existing_measure_grammar():
    # the OS probes are ordinary measure(...) strings — the shared parser accepts them unchanged
    for s in ("measure(cpu_share, highest_priority) >= 0.40",
              "measure(max_wait_slices, any) <= 8",
              "measure(share_ratio, tickets) <= 0.10",
              "measure(shadow_active, prio_sched) == 1"):
        assert parse(s).kind == "measure"


def test_cpu_share_of_highest_priority():
    procs = [Proc(3, "running", "spin", priority=5, tickets=1),
             Proc(4, "runnable", "spin", priority=10, tickets=1),
             Proc(5, "runnable", "spin", priority=10, tickets=1)]
    r = _runner(procs, [3, 3, 3, 4, 5, 3])          # pid 3 (highest priority) held CPU 4/6
    assert abs(r.measure("cpu_share", "highest_priority") - 4 / 6) < 1e-9
    assert evaluate("measure(cpu_share, highest_priority) >= 0.40", r) is True
    assert evaluate("measure(cpu_share, highest_priority) >= 0.90", r) is False


def test_max_wait_and_share_ratio():
    procs = [Proc(3, "running", "spin", priority=5, tickets=1, wait_ticks=0),
             Proc(4, "runnable", "spin", priority=10, tickets=1, wait_ticks=9),
             Proc(5, "runnable", "spin", priority=10, tickets=1, wait_ticks=2)]
    r = _runner(procs, [3, 3, 3, 3, 3, 3])          # pid 3 hogs -> 4 starves
    assert r.measure("max_wait_slices", "any") == 9.0
    assert evaluate("measure(max_wait_slices, any) <= 8", r) is False   # starvation -> fail


def test_share_ratio_tracks_tickets():
    # tickets 1:2:4; make the observed CPU split match closely -> small deviation -> passes
    procs = [Proc(3, "runnable", "a", tickets=1),
             Proc(4, "runnable", "b", tickets=2),
             Proc(5, "runnable", "c", tickets=4)]
    pids = [3] * 1 + [4] * 2 + [5] * 4               # 1/7, 2/7, 4/7 exactly
    r = _runner(procs, pids)
    assert r.measure("share_ratio", "tickets") < 1e-9
    assert evaluate("measure(share_ratio, tickets) <= 0.10", r) is True


def test_shadow_liveness_metrics():
    shadows = {"prio_sched": ShadowStatus("prio_sched", present=True, enabled=True,
                                          active=True, faults=0, hash="abc"),
               "lottery_sched": ShadowStatus("lottery_sched", faults=3)}
    r = _runner([Proc(3, "running", "x", priority=5)], [3], shadows)
    assert evaluate("measure(shadow_active, prio_sched) == 1", r) is True
    assert evaluate("measure(shadow_active, lottery_sched) == 1", r) is False
    assert r.measure("shadow_faults", "lottery_sched") == 3.0


def test_no_runtime_returns_none_not_a_crash():
    r = Xv6Runner(snapshot=None)
    assert r.available() is False
    assert r.measure("cpu_share", "highest_priority") is None
    assert evaluate("measure(cpu_share, highest_priority) >= 0.4", r) is False  # None -> not satisfied
