"""AOP schema, hash and validator.

The validator is a gate, not a lint, so these tests are mostly *rejection* tests: for each way a
generated plan can be wrong, assert it is refused loudly. A rule that silently passes here becomes
an expectation that reads as "the student didn't do the work".

Qt-free, Docker-free, model-free by construction.
"""
from __future__ import annotations

import pytest

from gini.domain import aop as A


def _exp(**kw):
    base = dict(id="e1", say="Something is true", layer="L3",
                check="exists('router')")
    base.update(kw)
    return A.Expectation(**base)


def _plan(*expectations, **header):
    return A.Aop(header=A.Header(**header),
                 expectations=tuple(expectations or (_exp(),)))


# -- the happy path ---------------------------------------------------------- #
def test_a_well_formed_plan_has_no_defects():
    plan = _plan(
        _exp(id="place", layer="L2", check="count('switch') >= 2 and exists('router')"),
        _exp(id="cross", layer="L3", probe="reach(host -> host, all) == ok",
             check="", requires=("place",)))
    assert A.validate(plan) == []


def test_validate_or_raise_returns_the_plan_when_clean():
    plan = _plan()
    assert A.validate_or_raise(plan) is plan


# -- identity ---------------------------------------------------------------- #
def test_plan_hash_is_immune_to_int_versus_float():
    """1800 and 1800.0 render as different JSON. If that moved the hash, a plan written by one
    code path and read back by another would look like two different plans."""
    a = _plan(deadline_s=3600, observation=A.Observation(cadence_s=20))
    b = _plan(deadline_s=3600.0, observation=A.Observation(cadence_s=20.0))
    assert A.plan_hash(a) == A.plan_hash(b)


def test_plan_hash_survives_a_round_trip():
    """The instructor's machine must hash a plan to what the student's machine did, or a proof
    can never be checked against the plan it was recorded under."""
    plan = _plan(_exp(id="a"), _exp(id="b", check="exists('switch')"))
    assert A.plan_hash(A.Aop.from_dict(plan.to_dict())) == A.plan_hash(plan)


def test_plan_hash_changes_when_an_expectation_changes():
    a = _plan(_exp(check="exists('router')"))
    b = _plan(_exp(check="exists('switch')"))
    assert A.plan_hash(a) != A.plan_hash(b)


def test_plan_hash_ignores_expectation_ordering_only_when_content_is_identical():
    """Reordering IS a different plan — evaluation order is part of the instrument — so the hash
    must move. This documents the choice rather than leaving it to luck."""
    a = _plan(_exp(id="a"), _exp(id="b", check="exists('switch')"))
    b = _plan(_exp(id="b", check="exists('switch')"), _exp(id="a"))
    assert A.plan_hash(a) != A.plan_hash(b)


def test_to_dict_carries_the_hash_but_hashing_excludes_it():
    plan = _plan()
    d = plan.to_dict()
    assert d["plan_hash"] == A.plan_hash(plan)
    assert "plan_hash" not in plan.to_dict(with_hash=False)


# -- structure --------------------------------------------------------------- #
def _rules(plan, **kw):
    return {d.rule for d in A.validate(plan, **kw)}


def test_empty_plan_is_refused():
    assert "empty" in _rules(A.Aop(header=A.Header(), expectations=()))


def test_duplicate_ids_are_refused():
    assert "duplicate-id" in _rules(_plan(_exp(id="same"), _exp(id="same")))


def test_missing_prose_is_refused():
    """`say` is what the teacher reads in the report. An expectation with no prose is a finding
    nobody can act on."""
    assert "say" in _rules(_plan(_exp(say="   ")))


def test_unknown_layer_and_sense_are_refused():
    assert "layer" in _rules(_plan(_exp(layer="L7")))
    assert "sense" in _rules(_plan(_exp(sense="maybe")))


def test_exactly_one_of_probe_or_check():
    both = _plan(_exp(probe="reach(host -> host) == ok", check="exists('router')"))
    neither = _plan(_exp(probe="", check=""))
    assert "probe-xor-check" in _rules(both)
    assert "probe-xor-check" in _rules(neither)


# -- parsing ----------------------------------------------------------------- #
def test_unparseable_probe_is_refused():
    assert "probe-parse" in _rules(_plan(_exp(probe="reach(a ~> b)", check="")))


def test_unparseable_check_is_refused():
    assert "check-parse" in _rules(_plan(_exp(check="exists(")))


def test_disallowed_predicate_is_refused():
    """The check language is a whitelist; a generated plan must not smuggle anything else in."""
    assert "check-parse" in _rules(_plan(_exp(check="__import__('os').system('x')")))


# -- the no-time rule (design 6.1) ------------------------------------------- #
@pytest.mark.parametrize("check_expr", [
    "converged('router')",
    "exists('router') and within(30)",
])
def test_temporal_predicates_are_refused(check_expr):
    assert "no-temporal" in _rules(_plan(_exp(check=check_expr)))


def test_temporal_prose_is_refused_even_when_the_predicate_is_clean():
    """A model asked for 'converge within 30 seconds' will put the time in the prose when it can't
    put it in the predicate. Catch it there too."""
    plan = _plan(_exp(say="Routes settle within 30 seconds", check="exists('router')"))
    assert "no-temporal" in _rules(plan)


def test_a_plain_expectation_is_not_mistaken_for_a_temporal_one():
    plan = _plan(_exp(say="Every machine reaches every other machine",
                      probe="reach(host -> host, all) == ok", check=""))
    assert "no-temporal" not in _rules(plan)


def test_port_numbers_are_not_read_as_durations():
    plan = _plan(_exp(say="The web server answers", probe="http(host -> web_app:8080) == ok",
                      check=""))
    assert "no-temporal" not in _rules(plan)


# -- type tokens ------------------------------------------------------------- #
def test_unknown_element_type_is_refused():
    assert "unknown-type" in _rules(_plan(_exp(check="exists('flux_capacitor')")))


def test_known_element_type_passes():
    assert "unknown-type" not in _rules(_plan(_exp(check="exists('router')")))


def test_unscoped_tokens_are_the_free_form_idiom():
    """What a free-form plan actually says: every host reaches every host, no slots involved."""
    plan = _plan(_exp(probe="reach(host -> host, all) == ok", check=""))
    assert _rules(plan) == set()


def test_slot_scoped_token_is_refused_where_resolution_is_missing():
    """`through()` compares type keys raw, so a scoped token would silently match nothing. Refusing
    it is better than a quiet false — remove this test when through_types() is slot-aware."""
    plan = _plan(_exp(check="through('router', 'host@lanA', 'host@lanB')"))
    assert "slot-unsupported" in _rules(plan)


def test_name_based_predicates_are_refused_on_a_free_form_activity():
    """`linked` takes device NAMES the student chose. A free-form plan is written before any
    device exists, so naming one silently matches nothing and reports unmet — blaming the student
    for the plan's mistake."""
    assert "names-on-free-form" in _rules(_plan(_exp(check="linked('M1', 'S1')")))


def test_property_is_caught_too_since_it_looks_deceptively_type_like():
    """`property('router', 'delay')` reads as a type predicate and is not one. This is the exact
    mistake hand-assembling the Ch.16 delay pattern produced."""
    assert "names-on-free-form" in _rules(_plan(_exp(check="property('router', 'delay')")))


def test_name_based_predicates_are_allowed_when_gini_composed_the_topology():
    """A composed starter topology was named by GINI, so the plan legitimately knows the names."""
    plan = _plan(_exp(check="linked('M1', 'S1')"), params={"starting_point": A.COMPOSED})
    assert "names-on-free-form" not in _rules(plan)


def test_name_based_arguments_are_never_type_checked():
    plan = _plan(_exp(check="linked('M1', 'S1')"), params={"starting_point": A.COMPOSED})
    assert "unknown-type" not in _rules(plan)


def test_slot_scoped_token_is_refused_on_a_free_form_activity():
    """Slots are a composition artifact — only compose.py tags a device — so on a blank canvas
    every device has slot="" and a scoped token matches nothing."""
    plan = _plan(_exp(probe="reach(host@lanA -> host@lanB) == ok", check=""))
    assert "slot-on-free-form" in _rules(plan)


def test_slot_scoped_token_is_fine_when_gini_composed_the_topology():
    plan = _plan(_exp(probe="reach(host@lanA -> host@lanB) == ok", check=""),
                 params={"starting_point": A.COMPOSED})
    assert _rules(plan) == set()


def test_known_types_can_be_overridden_for_another_domain():
    plan = _plan(_exp(check="exists('quark')"))
    assert "unknown-type" not in _rules(plan, known_types={"quark"})


# -- dependency graph -------------------------------------------------------- #
def test_two_expectations_making_the_same_observation_are_refused():
    """The assembler merges these; a hand-written plan that still has them is telling you it
    meant something temporal, which v1 cannot express."""
    plan = _plan(_exp(id="a", check="exists('router')"), _exp(id="b", check="exists('router')"))
    assert "duplicate-observation" in _rules(plan)


def test_the_same_observation_with_opposite_senses_is_allowed():
    """`must reach` and `must not reach` are genuinely different claims."""
    plan = _plan(_exp(id="a", probe="reach(host -> host) == ok", check="", sense=A.POSITIVE),
                 _exp(id="b", probe="reach(host -> host) == ok", check="", sense=A.NEGATIVE))
    assert "duplicate-observation" not in _rules(plan)


def test_dangling_requires_is_refused():
    assert "dangling-requires" in _rules(_plan(_exp(id="a", requires=("nope",))))


def test_self_dependency_is_refused():
    assert "self-dependency" in _rules(_plan(_exp(id="a", requires=("a",))))


def test_dependency_cycle_is_refused():
    plan = _plan(_exp(id="a", requires=("b",)), _exp(id="b", requires=("a",)))
    assert "cyclic-requires" in _rules(plan)


def test_long_dependency_cycle_is_refused():
    plan = _plan(_exp(id="a", requires=("c",)), _exp(id="b", requires=("a",)),
                 _exp(id="c", requires=("b",)))
    assert "cyclic-requires" in _rules(plan)


def test_a_diamond_is_not_a_cycle():
    plan = _plan(_exp(id="base"), _exp(id="l", requires=("base",)),
                 _exp(id="r", requires=("base",)), _exp(id="top", requires=("l", "r")))
    assert "cyclic-requires" not in _rules(plan)


# -- header ------------------------------------------------------------------ #
def test_non_positive_cadence_is_refused():
    plan = _plan(observation=A.Observation(cadence_s=0))
    assert "cadence" in _rules(plan)


def test_deadline_is_optional_but_must_be_positive():
    assert "deadline" not in _rules(_plan(deadline_s=None))
    assert "deadline" not in _rules(_plan(deadline_s=3600))
    assert "deadline" in _rules(_plan(deadline_s=0))


def test_header_round_trips():
    h = A.Header(intent="i", params={"guidance": True}, patterns=("p",),
                 answers=({"q": "how many LANs?", "a": "two"},), deadline_s=1800,
                 observation=A.Observation(cadence_s=15.0), guidance=("Build two LANs",))
    back = A.Header.from_dict(h.to_dict())
    assert back == h


# -- ordering ---------------------------------------------------------------- #
def test_evaluation_order_puts_dependencies_first():
    plan = _plan(_exp(id="top", layer="L4", requires=("mid",), check="exists('router')"),
                 _exp(id="mid", layer="L3", requires=("base",), check="exists('router')"),
                 _exp(id="base", layer="L2", check="exists('switch')"))
    assert [e.id for e in A.evaluation_order(plan)] == ["base", "mid", "top"]


def test_independent_expectations_are_ordered_by_layer_then_id():
    plan = _plan(_exp(id="z", layer="L2", check="exists('switch')"),
                 _exp(id="a", layer="L4", check="exists('router')"),
                 _exp(id="m", layer="L2", check="exists('hub')"))
    assert [e.id for e in A.evaluation_order(plan)] == ["m", "z", "a"]


def test_evaluation_order_terminates_on_a_cyclic_plan():
    """validate() rejects cycles, so this never runs in production — but degrading to 'emit the
    rest' beats hanging if a caller skips the gate."""
    plan = _plan(_exp(id="a", requires=("b",)), _exp(id="b", requires=("a",)))
    assert {e.id for e in A.evaluation_order(plan)} == {"a", "b"}


# -- the optional observability oracle --------------------------------------- #
def test_an_expectation_nothing_could_ever_satisfy_is_refused():
    class NoneAreObservable:
        def possible(self, _expectation):
            return False

    assert "unobservable" in _rules(_plan(), observability=NoneAreObservable())


def test_the_oracle_is_optional():
    assert "unobservable" not in _rules(_plan())


# -- errors carry the reasons ------------------------------------------------ #
def test_aop_error_names_every_defect_and_where_it_is():
    plan = _plan(_exp(id="bad", check="exists('flux_capacitor')", requires=("ghost",)))
    with pytest.raises(A.AopError) as exc:
        A.validate_or_raise(plan)
    assert {d.rule for d in exc.value.defects} == {"unknown-type", "dangling-requires"}
    assert "bad" in str(exc.value)
