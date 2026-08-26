"""The whole pipeline, teacher's sentence to teacher's report, with no model and no Docker.

Each module is unit-tested elsewhere. What this file guards is the *seams* — the places where a
change in one module silently violates an assumption in another. Every scenario runs the real path:
selector → assembler → validator → observer → report.

The invariants asserted here are the ones the design exists to provide, and each is written so that
breaking it fails loudly rather than degrading quietly.
"""
from __future__ import annotations

import json

from gini.agent import aop_selector as SEL
from gini.domain import aop as A
from gini.domain import aop_assemble as S
from gini.domain import aop_patterns as P
from gini.domain import aop_report as RPT
from gini.domain import objectives as O
from gini.domain.topology import Topology
from gini.services.aop_observer import AopObserver


# -- doubles ------------------------------------------------------------------ #
class Runner:
    def __init__(self, verdict=True, available=True):
        self.verdict, self._up = verdict, available

    def available(self):
        return self._up

    def reach(self, *a, **k):
        return self.verdict

    http = flow = reach

    def backends(self, lb):
        return 9


def multi_lan(lans=3, hosts=1, routers=2):
    """A topology shaped like the multi-lan pattern expects.

    Built with the REAL `Topology`, not a stand-in. An earlier version of this file used a hand-made
    double and it silently lacked `DeviceInstance.id`, so predicates that walk device identity blew
    up. A seam test whose doubles diverge from the real objects tests the doubles.
    """
    t = Topology(name="e2e")
    switches = []
    for _ in range(lans):
        sw = t.add_device("switch")
        switches.append(sw)
        for _ in range(hosts):
            t.add_link(t.add_device("host").id, sw.id)
    for i in range(routers):
        r = t.add_device("router")
        t.add_link(switches[i].id, r.id)
        t.add_link(r.id, switches[(i + 1) % lans].id)
    return t


def add(topo, type_key):
    return topo.add_device(type_key)


def draft_plan(reply, intent="build a routed network", **params):
    llm = lambda _p: json.dumps(reply)          # noqa: E731
    d = SEL.draft(intent, llm, params=params or {"starting_point": A.BLANK})
    assert d.ok, d.error
    return S.assemble(d.selection, created=1.0, gini_version="test")


# -- the pipeline holds together ---------------------------------------------- #
def test_a_sentence_becomes_a_report():
    plan = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 3, "routers": 2}}],
                       "note": "watching the routed network"})
    topo = multi_lan()
    rep = RPT.build(plan, O.TopologyWorld(topo), Runner(True), topology=topo)
    assert rep.plan_hash == A.plan_hash(plan)
    assert rep.counts[RPT.UNMET] == 0
    assert rep.plan_is_sound


def test_every_catalogued_pattern_survives_the_whole_pipeline():
    """A pattern that cannot be drafted, assembled, validated and evaluated is not certified,
    whatever the catalogue says."""
    for key in P.CATALOGUE:
        plan = draft_plan({"patterns": [{"key": key}]})
        assert A.validate(plan) == []
        topo = multi_lan(lans=5, hosts=2, routers=4)
        rep = RPT.build(plan, O.TopologyWorld(topo), Runner(True), topology=topo)
        assert rep.counts[RPT.DEFECTIVE] == 0, f"{key} produced a defective expectation"


def test_the_plan_the_report_names_is_the_plan_that_was_evaluated():
    """The binding proof-of-activity depends on: a report must not be readable as evidence about
    some other plan."""
    a = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 2}}]})
    b = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 9}}]})
    topo = multi_lan()
    assert (RPT.build(a, O.TopologyWorld(topo), Runner()).plan_hash
            != RPT.build(b, O.TopologyWorld(topo), Runner()).plan_hash)


# -- fairness invariants (design 10) ------------------------------------------ #
def test_identical_work_checked_at_different_moments_gives_the_same_report():
    """Invariant 2. Two students who did the same thing must not diverge because one looked
    earlier than the other."""
    plan = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 3, "routers": 2}}]})
    topo = multi_lan()

    class Clock:
        def __init__(self, t):
            self.t = t

        def __call__(self):
            return self.t

    reports = []
    for start in (0.0, 5_000.0):
        obs = AopObserver(plan, lambda: O.TopologyWorld(topo), lambda: Runner(True),
                          get_topology=lambda: topo, now=Clock(start))
        for _ in range(3):
            obs.on_check()
        reports.append(obs.report().render())
    assert reports[0] == reports[1]


def test_the_same_selection_always_yields_the_same_instrument():
    """Invariant 1. Codes are minted against a plan hash; if assembly drifted, a proof could not
    be verified against the plan it was recorded under."""
    sel = S.Selection(patterns=(S.PatternRef("multi-lan", {"lans": 4}),))
    hashes = {A.plan_hash(S.assemble(sel, created=1.0, gini_version="t")) for _ in range(5)}
    assert len(hashes) == 1


def test_no_model_is_consulted_after_the_selection():
    """Invariant 3. The deterministic half must not import an LLM, now or by accident later."""
    import gini.domain.aop_assemble as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("import llm", "from ..agent", "from .agent", "aop_selector"):
        assert banned not in src


def test_a_student_is_never_charged_for_a_plan_gap():
    """Invariant 4. Work the plan does not cover is reported as the plan's gap, and does not
    appear as anything the student failed."""
    plan = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 3, "routers": 2}}]})
    topo = multi_lan()
    add(topo, "firewall")
    rep = RPT.build(plan, O.TopologyWorld(topo), Runner(True), topology=topo)
    assert rep.unexplained == (("firewall", 1),)
    assert rep.counts[RPT.UNMET] == 0
    assert not rep.plan_is_sound                # the PLAN is what is flagged


# -- the instrument stays hidden (design 11) ---------------------------------- #
def test_guidance_never_leaks_an_expectation():
    plan = draft_plan({"patterns": [{"key": "single-lan"}, {"key": "multi-lan"}]},
                      **{"starting_point": A.BLANK, "guidance": True})
    says = {e.say for e in plan.expectations}
    assert plan.header.guidance
    assert not (says & set(plan.header.guidance))
    for g in plan.header.guidance:
        for probe_ish in ("reach(", "count(", "==", "all_linked"):
            assert probe_ish not in g


def test_guidance_is_off_unless_the_teacher_asks():
    plan = draft_plan({"patterns": [{"key": "multi-lan"}]})
    assert plan.header.guidance == ()


# -- diagnosis rather than a wall of failures --------------------------------- #
def test_a_missing_foundation_yields_one_finding_and_consequences():
    plan = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 3, "routers": 2}}]})
    bare = Topology(name="bare"); bare.add_device("host")   # nothing built
    rep = RPT.build(plan, O.TopologyWorld(bare), Runner(True), topology=bare)
    assert rep.counts[RPT.BLOCKED] >= 1
    # every blocked finding names what it was waiting on, so the teacher can follow the chain
    assert all(f.blocked_by for f in rep.findings if f.verdict == RPT.BLOCKED)


def test_a_stopped_lab_is_unobservable_across_the_whole_plan():
    plan = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 3, "routers": 2}}]})
    topo = multi_lan()
    rep = RPT.build(plan, O.TopologyWorld(topo), Runner(available=False), topology=topo)
    assert rep.counts[RPT.UNOBSERVABLE] >= 1
    assert rep.counts[RPT.UNMET] == 0          # nothing is charged to the student


# -- the report reads like something a person can act on ---------------------- #
def test_a_correct_submission_scores_clean():
    """Regression: `through('router','host','host')` reads like "traffic crosses a router" but
    means EVERY path does, and two stations on one switch do not — so a correctly-built multi-LAN
    network scored MISS. A pattern nobody can satisfy is worse than no pattern."""
    plan = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 3, "routers": 2}}]})
    topo = multi_lan(lans=3, hosts=2, routers=2)
    rep = RPT.build(plan, O.TopologyWorld(topo), Runner(True), topology=topo)
    assert rep.counts[RPT.UNMET] == 0, rep.render()
    assert rep.counts[RPT.BLOCKED] == 0, rep.render()


def test_a_never_run_lab_is_distinguishable_from_a_wrong_one():
    """The two must not produce identical reports: 'built it but never demonstrated it' and 'built
    the wrong thing' call for different conversations with the student."""
    plan = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 3, "routers": 2}}]})
    good = multi_lan(lans=3, hosts=2, routers=2)
    flat = multi_lan(lans=1, hosts=3, routers=0)
    never_ran = RPT.build(plan, O.TopologyWorld(good), Runner(available=False), topology=good)
    wrong = RPT.build(plan, O.TopologyWorld(flat), Runner(True), topology=flat)
    assert never_ran.render() != wrong.render()
    assert never_ran.counts[RPT.UNOBSERVABLE] >= 1
    assert never_ran.counts[RPT.UNMET] == 0        # nothing is charged to the student
    assert wrong.counts[RPT.UNMET] >= 1


def test_the_rendered_report_names_the_activity_and_the_gaps():
    plan = draft_plan({"patterns": [{"key": "multi-lan", "params": {"lans": 3, "routers": 2}}]})
    topo = multi_lan()
    add(topo, "firewall")
    text = RPT.build(plan, O.TopologyWorld(topo), Runner(True), topology=topo).render()
    assert "met" in text
    assert "stranded" in text                  # an expectation's prose, for the teacher
    assert "1 x firewall" in text
