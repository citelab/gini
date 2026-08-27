"""Selection → AOP expansion, plus the two objectives-layer additions the AOP needs.

The property under test throughout is *determinism*: the same selection must always produce the
same plan, because codes are minted against a plan hash and a teacher has to be able to regenerate
a plan and confirm it is the one their students worked to.
"""
from __future__ import annotations

import pytest

from gini.domain import aop as A
from gini.domain import aop_assemble as S
from gini.domain import aop_patterns as P
from gini.domain import objectives as O


def _sel(*keys, **params):
    return S.Selection(intent="build it",
                       patterns=tuple(S.PatternRef(key=k) for k in keys),
                       params=params or {"starting_point": A.BLANK})


# -- expansion ---------------------------------------------------------------- #
def test_a_selection_expands_into_a_valid_plan():
    plan = S.assemble(_sel("multi-lan"))
    assert A.validate(plan) == []
    assert len(plan.expectations) == 6


def test_every_catalogued_pattern_assembles_clean():
    """A pattern that cannot pass the gate is not certified, whatever the catalogue says."""
    for key in P.CATALOGUE:
        assert A.validate(S.assemble(_sel(key))) == [], f"pattern {key} produced a defective plan"


def test_assembly_is_deterministic():
    a = S.assemble(_sel("multi-lan"), created=1.0, gini_version="x")
    b = S.assemble(_sel("multi-lan"), created=1.0, gini_version="x")
    assert A.plan_hash(a) == A.plan_hash(b)


def test_parameters_change_the_plan():
    small = S.Selection(patterns=(S.PatternRef("multi-lan", {"lans": 2, "routers": 1}),))
    big = S.Selection(patterns=(S.PatternRef("multi-lan", {"lans": 9, "routers": 4}),))
    assert A.plan_hash(S.assemble(small)) != A.plan_hash(S.assemble(big))
    assert "count('switch') >= 2" in S.assemble(small).by_id("multi-segments").check


def test_pattern_order_is_preserved():
    plan = S.assemble(_sel("multi-lan", "link-delay"))
    assert plan.header.patterns == ("multi-lan", "link-delay")
    assert [e.pattern for e in plan.expectations][-1] == "link-delay"


# -- selection hygiene -------------------------------------------------------- #
def test_an_uncertified_pattern_is_refused():
    with pytest.raises(KeyError):
        S.assemble(_sel("invent-something"))


def test_an_unknown_parameter_is_refused_rather_than_ignored():
    """A model that invents a parameter believes the plan will honour it. Dropping the key
    silently would show the teacher a plan that means something else."""
    sel = S.Selection(patterns=(S.PatternRef("multi-lan", {"subnets": 4}),))
    with pytest.raises(S.SelectionError) as e:
        S.assemble(sel)
    assert "subnets" in str(e.value)


def test_the_whole_of_chapter_16_is_a_legal_selection():
    """single-lan + multi-lan is how a teacher assigns both halves of the chapter. The patterns
    overlap heavily, and that must merge rather than fail."""
    plan = S.assemble(_sel("single-lan", "multi-lan"))
    assert A.validate(plan) == []
    ids = [e.id for e in plan.expectations]
    assert len(ids) == len(set(ids))


def test_identical_observations_are_merged_not_repeated():
    """Both patterns assert every-station-reaches-every-station. Asserted twice it always agrees
    with itself and costs a second docker exec to learn one fact."""
    plan = S.assemble(_sel("single-lan", "multi-lan"))
    probes = [e.probe for e in plan.expectations if e.probe]
    assert probes.count("reach(host -> host, all) == ok") == 1


def test_merging_never_loses_a_distinct_expectation():
    both = S.assemble(_sel("single-lan", "multi-lan"))
    solo = S.assemble(_sel("multi-lan"))
    distinct = {(e.probe or e.check, e.sense) for e in both.expectations}
    assert distinct >= {(e.probe or e.check, e.sense) for e in solo.expectations}
    assert len(both.expectations) > len(solo.expectations)


def test_dependencies_are_rewired_onto_the_surviving_expectation():
    """An expectation that depended on a merged-away one must point at its survivor, or the plan
    grows a dangling require."""
    plan = S.assemble(_sel("single-lan", "multi-lan"))
    ids = {e.id for e in plan.expectations}
    for e in plan.expectations:
        assert set(e.requires) <= ids, f"{e.id} requires something not in the plan"
    assert A.validate(plan) == []


def test_selecting_one_pattern_twice_collapses_to_one_copy():
    once = S.assemble(_sel("single-lan"))
    twice = S.assemble(_sel("single-lan", "single-lan"))
    assert len(twice.expectations) == len(once.expectations)


def test_selection_round_trips_and_digests_stably():
    sel = S.Selection(intent="i", patterns=(S.PatternRef("multi-lan", {"lans": 3}),),
                      params={"guidance": True}, answers=({"q": "q", "a": "a"},),
                      deadline_s=1800)
    assert S.Selection.from_dict(sel.to_dict()).digest() == sel.digest()


# -- disclosure --------------------------------------------------------------- #
def test_guidance_is_pattern_level_only_and_off_by_default():
    off = S.assemble(_sel("multi-lan"))
    on = S.assemble(_sel("multi-lan", **{"starting_point": A.BLANK, "guidance": True}))
    assert off.header.guidance == ()
    assert on.header.guidance == (P.MULTI_LAN.summary,)


def test_no_expectation_text_leaks_into_guidance():
    """The instrument stays hidden: guidance may never contain an expectation's prose."""
    plan = S.assemble(_sel("multi-lan", **{"starting_point": A.BLANK, "guidance": True}))
    says = {e.say for e in plan.expectations}
    assert not (says & set(plan.header.guidance))


# -- the authoring loop ------------------------------------------------------- #
def test_dry_run_reports_defects_instead_of_raising():
    assert S.dry_run(_sel("multi-lan")) == []
    bad = S.Selection(patterns=(S.PatternRef("nope"),))
    assert [d.rule for d in S.dry_run(bad)] == ["selection"]


def test_describe_renders_the_plan_for_review():
    text = S.describe(S.assemble(_sel("multi-lan")))
    assert "expectations" in text
    assert "reach(host -> host, all) == ok" in text


# --------------------------------------------------------------------------- #
# objectives.property_type — the predicate the Ch.16 delay pattern needed
# --------------------------------------------------------------------------- #
class _Dev:
    def __init__(self, name, type_key, properties=None, slot=""):
        self.name, self.type_key = name, type_key
        self.properties = properties or {}
        self.slot, self.parent_id = slot, None


class _Topo:
    def __init__(self, *devices):
        self.devices = {d.name: d for d in devices}
        self.links = {}


def _world(*devices):
    return O.TopologyWorld(_Topo(*devices))


def test_property_type_finds_a_configured_device_by_type_not_name():
    """The whole point: the plan says 'router', the student called it R2."""
    w = _world(_Dev("R2", "router", {"delay": "40"}))
    assert O.evaluate_check("property_type('router', 'delay')", w) is True


def test_property_type_is_false_when_nothing_of_that_type_is_configured():
    w = _world(_Dev("R2", "router"), _Dev("R3", "router", {"other": "x"}))
    assert O.evaluate_check("property_type('router', 'delay')", w) is False


def test_property_type_is_existential():
    """Some router has delay — which is what 'they added delay on the path' means."""
    w = _world(_Dev("R1", "router"), _Dev("R2", "router", {"delay": "40"}))
    assert O.evaluate_check("property_type('router', 'delay')", w) is True


def test_property_type_can_pin_a_value():
    w = _world(_Dev("S1", "switch", {"mode": "hub"}))
    assert O.evaluate_check("property_type('switch', 'mode', 'hub')", w) is True
    assert O.evaluate_check("property_type('switch', 'mode', 'bridge')", w) is False


def test_property_type_treats_an_empty_setting_as_unconfigured():
    w = _world(_Dev("R1", "router", {"delay": ""}))
    assert O.evaluate_check("property_type('router', 'delay')", w) is False


def test_property_type_ignores_other_types():
    w = _world(_Dev("S1", "switch", {"delay": "40"}))
    assert O.evaluate_check("property_type('router', 'delay')", w) is False


def test_property_type_second_argument_is_not_read_as_an_element_type():
    """`property_type('router','delay')` must not report 'delay' as an unknown element."""
    assert O.unknown_element_types("property_type('router', 'delay')") == []
    assert O.unknown_element_types("property_type('flux', 'delay')") == ["flux"]


def test_property_type_is_a_type_predicate_so_the_aop_accepts_it_free_form():
    plan = A.Aop(header=A.Header(),
                 expectations=(A.Expectation(id="d", say="A router has delay",
                                             layer="policy",
                                             check="property_type('router', 'delay')"),))
    assert A.validate(plan) == []


# --------------------------------------------------------------------------- #
# objectives: a broken objective is `defective`, never `unmet`
# --------------------------------------------------------------------------- #
class _UpRunner:
    def available(self):
        return True

    def reach(self, *a, **k):
        return True


def test_an_unparseable_probe_is_defective_not_unmet():
    """With machine-generated plans, a malformed probe reported as `unmet` reads as 'the student
    didn't do the work'. The fault is the author's and must be shown as such."""
    obj = O.Objective(id="x", say="s", kind="behavioral", probe="reach(a ~> b)")
    assert O.evaluate(obj, None, _UpRunner()).status == O.DEFECTIVE


def test_a_broken_probe_is_defective_even_with_no_runtime():
    """It is broken whether or not anything is running, so say so immediately rather than
    hiding it behind `pending` until someone presses Run."""
    obj = O.Objective(id="x", say="s", kind="behavioral", probe="reach(a ~> b)")
    assert O.evaluate(obj, None, None).status == O.DEFECTIVE


def test_an_unparseable_check_is_defective_not_unmet():
    obj = O.Objective(id="x", say="s", check="exists(")
    assert O.evaluate(obj, _world()).status == O.DEFECTIVE


def test_a_valid_probe_with_no_runtime_is_still_pending():
    obj = O.Objective(id="x", say="s", kind="behavioral", probe="reach(host -> host) == ok")
    assert O.evaluate(obj, None, None).status == O.PENDING


def test_a_valid_probe_with_a_runtime_still_resolves():
    obj = O.Objective(id="x", say="s", kind="behavioral", probe="reach(host -> host) == ok")
    assert O.evaluate(obj, None, _UpRunner()).status == O.MET


def test_an_honestly_unsatisfied_check_is_still_unmet():
    """The new status must not swallow real failures."""
    obj = O.Objective(id="x", say="s", check="exists('router')")
    assert O.evaluate(obj, _world(_Dev("S1", "switch"))).status == O.UNMET
