"""Xv6Pack — the OS domain pack: fragments load, register, and grade through the SHARED engine.

Proves the unification: an OS assignment authored as a YAML fragment is graded by the same
objective/probe evaluator as networking, with Xv6Runner as the behavioral oracle. No new engine.
"""
from gini.agent import domains
from gini.agent.xv6_pack import Xv6Pack
from gini.domain.objectives import evaluate
from gini.domain.xv6 import Proc, SchedTimeline, ShadowStatus, Snapshot
from gini.domain.xv6_runner import Xv6Runner


def _timeline(pids):
    tl = SchedTimeline()
    for i, p in enumerate(pids):
        tl.add_run(p, i)
    return tl


def test_os_pack_registers_beside_networking():
    assert "os" in domains.names()
    assert domains.get("os") is not None
    assert "networking" in domains.names()          # didn't break the existing pack


def test_os_assignments_load_as_fragments():
    frags = {f.id: f for f in Xv6Pack().fragments()}
    assert "xv6-priority-fix" in frags and "xv6-lottery-fix" in frags
    prio = frags["xv6-priority-fix"]
    probes = [o.probe for o in prio.instantiate() if o.is_behavioral()]
    assert "measure(max_wait_slices, any) <= 8" in probes
    assert "measure(cpu_share, highest_priority) >= 0.40" in probes


def _grade(fragment, snapshot, timeline, shadows):
    runner = Xv6Runner(snapshot, timeline, shadows)
    return {o.id: evaluate(o, world=None, runner=runner).status
            for o in fragment.instantiate()}


def test_priority_assignment_grades_flawed_vs_fixed():
    frag = {f.id: f for f in Xv6Pack().fragments()}["xv6-priority-fix"]
    shadows = {"prio_sched": ShadowStatus("prio_sched", present=True, enabled=True,
                                          active=True, faults=0, hash="abc")}
    procs = [Proc(3, "running", "spin", priority=5, wait_ticks=0),
             Proc(4, "runnable", "spin", priority=10, wait_ticks=12),
             Proc(5, "runnable", "spin", priority=10, wait_ticks=2)]

    # FLAWED: pid 3 hogs the CPU -> pid 4 starved (wait 12 > 8) -> no-starvation UNMET
    flawed = _grade(frag, Snapshot(procs=procs), _timeline([3] * 6), shadows)
    assert flawed["shadow-wired"] == "met"
    assert flawed["high-priority-served"] == "met"
    assert flawed["no-starvation"] == "unmet"

    # FIXED: aging keeps everyone's wait low and the CPU shared -> all objectives met
    fixed_procs = [Proc(3, "running", "spin", priority=5, wait_ticks=0),
                   Proc(4, "runnable", "spin", priority=10, wait_ticks=3),
                   Proc(5, "runnable", "spin", priority=10, wait_ticks=2)]
    fixed = _grade(frag, Snapshot(procs=fixed_procs), _timeline([3, 3, 4, 3, 5, 3]), shadows)
    assert all(v == "met" for v in fixed.values()), fixed


def test_shadow_not_wired_fails_liveness():
    # no shadow active -> the liveness objective is unmet (student hasn't wired their fix)
    frag = {f.id: f for f in Xv6Pack().fragments()}["xv6-lottery-fix"]
    res = _grade(frag, Snapshot(procs=[Proc(3, "running", "a", tickets=1)]),
                 _timeline([3]), shadows={})
    assert res["shadow-wired"] == "unmet"
