"""The report a teacher reads.

The property under test is **attribution**: no negative may be charged to the student unless the
expectation was genuinely observable and genuinely unsatisfied. Every other kind of negative —
blocked by a prerequisite, unobservable, defective, unexplained — belongs to the lab or the plan,
and a test here guards each one.
"""
from __future__ import annotations

from gini.domain import aop as A
from gini.domain import aop_report as RPT
from gini.domain import objectives as O


# -- doubles ------------------------------------------------------------------ #
class Dev:
    def __init__(self, name, type_key, properties=None):
        self.name, self.type_key = name, type_key
        self.properties = properties or {}
        self.slot, self.parent_id = "", None


class Topo:
    def __init__(self, *devices):
        self.devices = {d.name: d for d in devices}
        self.links = {}


class Runner:
    """A live runner whose probes all return `verdict`."""
    def __init__(self, verdict=True, available=True):
        self.verdict, self._up = verdict, available

    def available(self):
        return self._up

    def reach(self, *a, **k):
        return self.verdict

    def http(self, *a, **k):
        return self.verdict

    def backends(self, lb):
        return 9

    def flow(self, *a, **k):
        return self.verdict


def world(*devices):
    return O.TopologyWorld(Topo(*devices))


def plan_of(*expectations, **header):
    return A.Aop(header=A.Header(**header), expectations=tuple(expectations))


def exp(**kw):
    base = dict(id="e", say="something", layer="L3", check="exists('router')")
    base.update(kw)
    return A.Expectation(**base)


def verdicts(report):
    return {f.id: f.verdict for f in report.findings}


# -- the two verdicts that are about the student ------------------------------ #
def test_a_satisfied_structural_expectation_is_met():
    r = RPT.build(plan_of(exp()), world(Dev("R1", "router")))
    assert verdicts(r) == {"e": RPT.MET}


def test_an_unsatisfied_structural_expectation_is_unmet():
    r = RPT.build(plan_of(exp()), world(Dev("S1", "switch")))
    assert verdicts(r) == {"e": RPT.UNMET}
    assert r.findings[0].about_the_student


def test_a_behavioural_expectation_resolves_against_a_live_runner():
    p = plan_of(exp(probe="reach(host -> host, all) == ok", check=""))
    assert verdicts(RPT.build(p, world(), Runner(True))) == {"e": RPT.MET}
    assert verdicts(RPT.build(p, world(), Runner(False))) == {"e": RPT.UNMET}


# -- blocked: a consequence, not a finding ------------------------------------ #
def test_a_failed_prerequisite_blocks_rather_than_fails_its_dependents():
    """One broken foundation must produce one finding and N consequences, not N+1 failures —
    otherwise the teacher has to correlate them by hand."""
    p = plan_of(
        exp(id="base", layer="L2", check="exists('switch')"),
        exp(id="mid", layer="L3", check="exists('router')", requires=("base",)),
        exp(id="top", layer="L4", check="exists('router')", requires=("mid",)))
    r = RPT.build(p, world(Dev("R1", "router")))          # no switch: base fails
    assert verdicts(r) == {"base": RPT.UNMET, "mid": RPT.BLOCKED, "top": RPT.BLOCKED}


def test_blocked_findings_name_what_they_were_waiting_on():
    p = plan_of(exp(id="base", layer="L2", check="exists('switch')"),
                exp(id="mid", check="exists('router')", requires=("base",)))
    r = RPT.build(p, world(Dev("R1", "router")))
    assert next(f for f in r.findings if f.id == "mid").blocked_by == ("base",)


def test_blocked_is_not_counted_against_the_student():
    p = plan_of(exp(id="base", layer="L2", check="exists('switch')"),
                exp(id="mid", check="exists('router')", requires=("base",)))
    r = RPT.build(p, world(Dev("R1", "router")))
    assert [f.id for f in r.findings if f.about_the_student] == ["base"]


def test_a_met_prerequisite_lets_its_dependent_run():
    p = plan_of(exp(id="base", layer="L2", check="exists('switch')"),
                exp(id="mid", check="exists('router')", requires=("base",)))
    r = RPT.build(p, world(Dev("S1", "switch"), Dev("R1", "router")))
    assert verdicts(r) == {"base": RPT.MET, "mid": RPT.MET}


# -- unobservable: the lab, not the student ----------------------------------- #
def test_a_behavioural_expectation_with_no_runtime_is_unobservable():
    p = plan_of(exp(probe="reach(host -> host) == ok", check=""))
    f = RPT.build(p, world()).findings[0]
    assert f.verdict == RPT.UNOBSERVABLE and not f.about_the_student
    assert "not running" in f.detail


def test_a_stopped_lab_is_unobservable_not_unmet():
    p = plan_of(exp(probe="reach(host -> host) == ok", check=""))
    assert verdicts(RPT.build(p, world(), Runner(available=False))) == {"e": RPT.UNOBSERVABLE}


def test_the_observability_oracle_can_rule_an_expectation_out():
    class Never:
        def possible(self, _e):
            return False

    r = RPT.build(plan_of(exp()), world(Dev("R1", "router")), observability=Never())
    assert verdicts(r) == {"e": RPT.UNOBSERVABLE}


def test_an_oracle_that_raises_does_not_become_a_verdict():
    class Broken:
        def possible(self, _e):
            raise RuntimeError("boom")

    r = RPT.build(plan_of(exp()), world(Dev("R1", "router")), observability=Broken())
    assert verdicts(r) == {"e": RPT.MET}


# -- defective: the plan's author --------------------------------------------- #
def test_a_broken_expectation_is_defective_not_unmet():
    r = RPT.build(plan_of(exp(check="exists(")), world())
    f = r.findings[0]
    assert f.verdict == RPT.DEFECTIVE and not f.about_the_student
    assert "plan defect" in f.detail


def test_a_defective_expectation_makes_the_plan_unsound():
    assert not RPT.build(plan_of(exp(check="exists(")), world()).plan_is_sound


# -- negative expectations ---------------------------------------------------- #
def test_a_negative_expectation_is_met_when_the_traffic_does_not_pass():
    p = plan_of(exp(probe="reach(host -> host) == ok", check="", sense=A.NEGATIVE))
    assert verdicts(RPT.build(p, world(), Runner(False))) == {"e": RPT.MET}


def test_a_negative_expectation_is_unmet_when_the_traffic_does_pass():
    p = plan_of(exp(probe="reach(host -> host) == ok", check="", sense=A.NEGATIVE))
    assert verdicts(RPT.build(p, world(), Runner(True))) == {"e": RPT.UNMET}


# -- unexplained work: the fairness backstop ---------------------------------- #
def test_work_the_plan_never_mentions_is_surfaced():
    """A student who builds and exercises a firewall, against a plan with nothing to say about
    firewalls, must not be reported as having done nothing."""
    p = plan_of(exp(check="exists('router')"))
    r = RPT.build(p, world(Dev("R1", "router")), topology=Topo(Dev("R1", "router"),
                                                              Dev("FW1", "firewall"),
                                                              Dev("FW2", "firewall")))
    assert r.unexplained == (("firewall", 2),)
    assert not r.plan_is_sound


def test_types_the_plan_mentions_are_not_unexplained():
    p = plan_of(exp(check="exists('router') and count('switch') >= 1"))
    r = RPT.build(p, world(Dev("R1", "router")),
                  topology=Topo(Dev("R1", "router"), Dev("S1", "switch")))
    assert r.unexplained == ()


def test_types_named_only_in_a_probe_count_as_mentioned():
    p = plan_of(exp(probe="reach(host -> host, all) == ok", check=""))
    r = RPT.build(p, world(), Runner(), topology=Topo(Dev("M1", "host"), Dev("M2", "host")))
    assert r.unexplained == ()


def test_slot_scoped_mentions_still_count():
    p = plan_of(exp(probe="reach(host@a -> host@b) == ok", check=""))
    r = RPT.build(p, world(), Runner(), topology=Topo(Dev("M1", "host")))
    assert r.unexplained == ()


def test_grouping_boxes_are_not_reported_as_unobserved_work():
    """A VPC box is drawn organisation, not an unobserved network function. Flagging it would be
    noise, and a report that cries wolf gets skimmed."""
    p = plan_of(exp(check="exists('router')"))
    r = RPT.build(p, world(Dev("R1", "router")),
                  topology=Topo(Dev("R1", "router"), Dev("VPC1", "vpc"), Dev("SG1",
                                                                             "security_group")))
    assert r.unexplained == ()


def test_unexplained_is_skipped_when_no_topology_is_supplied():
    r = RPT.build(plan_of(exp()), world(Dev("R1", "router")))
    assert r.unexplained == ()


# -- accounting and rendering ------------------------------------------------- #
def test_counts_cover_every_verdict():
    p = plan_of(exp(id="a", layer="L2", check="exists('switch')"),
                exp(id="b", check="exists('router')", requires=("a",)),
                exp(id="c", check="exists("),
                exp(id="d", probe="reach(host -> host) == ok", check=""))
    r = RPT.build(p, world(Dev("R1", "router")))
    assert r.counts == {RPT.MET: 0, RPT.UNMET: 1, RPT.BLOCKED: 1,
                        RPT.UNOBSERVABLE: 1, RPT.DEFECTIVE: 1}


def test_headline_mentions_only_what_happened():
    r = RPT.build(plan_of(exp()), world(Dev("R1", "router")))
    assert r.headline() == "1 met · 0 unmet"


def test_headline_flags_work_past_the_deadline():
    r = RPT.build(plan_of(exp()), world(Dev("R1", "router")), within_deadline=False)
    assert "past the deadline" in r.headline()


def test_render_shows_findings_dependencies_and_unexplained_work():
    p = plan_of(exp(id="a", layer="L2", say="A switch exists", check="exists('switch')"),
                exp(id="b", say="A router exists", check="exists('router')", requires=("a",)))
    text = RPT.build(p, world(Dev("R1", "router")),
                     topology=Topo(Dev("R1", "router"), Dev("FW1", "firewall"))).render()
    assert "A switch exists" in text and "after: a" in text
    assert "1 x firewall" in text and "teacher review" in text


def test_the_report_is_bound_to_the_plan_it_evaluated():
    p = plan_of(exp())
    assert RPT.build(p, world()).plan_hash == A.plan_hash(p)


def test_findings_follow_evaluation_order_not_declaration_order():
    p = plan_of(exp(id="top", layer="L4", check="exists('router')", requires=("base",)),
                exp(id="base", layer="L2", check="exists('switch')"))
    r = RPT.build(p, world(Dev("S1", "switch"), Dev("R1", "router")))
    assert [f.id for f in r.findings] == ["base", "top"]
