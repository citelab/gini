"""Sweep planning and the measured reachability relation.

The property under test is that nothing is inferred: every ordered pair appears in the relation
because it was measured. The scenarios below are the ones the real harness produced on Docker —
healthy, partitioned, and endpoint-filtered — plus the failure modes a measurement can have.
"""
from __future__ import annotations

import itertools

from gini.domain import reach_strategy as R

HOSTS = [f"M{i}" for i in range(1, 10)]
LAN = {h: (int(h[1:]) - 1) // 3 for h in HOSTS}         # M1-3, M4-6, M7-9


def rel_from(blocked=(), hosts=HOSTS) -> R.Relation:
    """A relation where `blocked` (unordered pairs) fail both ways, measured completely."""
    bad = {frozenset(p) for p in blocked}
    facts = {(a, b): frozenset((a, b)) not in bad
             for a, b in itertools.permutations(hosts, 2)}
    return R.measure(hosts, lambda s, ds: {d: facts[(s, d)] for d in ds})


# -- the plan ----------------------------------------------------------------- #
def test_one_sweep_per_host_covering_everyone_else():
    plan = R.sweep_plan(HOSTS)
    assert len(plan) == len(HOSTS)
    for src, dsts in plan:
        assert src not in dsts
        assert set(dsts) == set(HOSTS) - {src}


def test_the_plan_is_deterministic_regardless_of_input_order():
    """Fairness invariant: two students with the same topology must be probed identically, and
    set/dict iteration order is not a promise."""
    assert R.sweep_plan(HOSTS) == R.sweep_plan(list(reversed(HOSTS)))
    assert R.sweep_plan(set(HOSTS)) == R.sweep_plan(HOSTS)


def test_cost_is_linear_in_execs_and_quadratic_in_coverage():
    execs, pairs = R.probe_count(HOSTS)
    assert execs == 9 and pairs == 72


def test_every_ordered_pair_is_measured_not_inferred():
    r = rel_from()
    assert len(r.pairs) == 9 * 8
    assert all((a, b) in r.pairs for a, b in itertools.permutations(HOSTS, 2))


# -- the scenarios the harness ran on real Docker ----------------------------- #
def test_healthy_network():
    r = rel_from()
    assert r.all_reach()
    assert r.islands() == [sorted(HOSTS)]
    assert r.failures == [] and r.counterexamples() == []


def test_partitioned_network_names_the_isolated_group():
    lan3 = [h for h in HOSTS if LAN[h] == 2]
    cut = [(a, b) for a in HOSTS for b in lan3 if LAN[a] != 2]
    r = rel_from(cut)
    assert not r.all_reach()
    assert r.islands() == [["M1", "M2", "M3", "M4", "M5", "M6"], ["M7", "M8", "M9"]]


def test_a_clean_partition_is_still_transitive():
    """Two fully-connected islands remain an equivalence relation — which is why a partition alone
    could never have exposed the unsoundness that filtering does."""
    lan3 = [h for h in HOSTS if LAN[h] == 2]
    r = rel_from([(a, b) for a in HOSTS for b in lan3 if LAN[a] != 2])
    assert r.counterexamples() == []


def test_endpoint_filtering_is_detected_although_it_is_not_transitive():
    """The measured case: M7 drops traffic from LAN 1. Spanning from 5 of 9 representatives called
    this 'all reachable'; measuring every pair cannot."""
    r = rel_from([(a, "M7") for a in ("M1", "M2", "M3")])
    assert not r.all_reach()
    assert ("M1", "M4", "M7") in r.counterexamples(limit=100)


def test_a_single_blocked_pair_is_caught():
    """The case that defeated every sampling scheme: one pair, between two non-representatives."""
    r = rel_from([("M2", "M8")])
    assert not r.all_reach()
    assert ("M2", "M8") in r.failures and ("M8", "M2") in r.failures


# -- asymmetry ---------------------------------------------------------------- #
def test_one_way_reachability_is_reported():
    facts = {(a, b): True for a, b in itertools.permutations(HOSTS, 2)}
    facts[("M8", "M1")] = False                      # M1 -> M8 works, M8 -> M1 does not
    r = R.measure(HOSTS, lambda s, ds: {d: facts[(s, d)] for d in ds})
    assert r.asymmetric() == [("M1", "M8")]
    assert not r.all_reach()


def test_islands_require_mutual_reachability():
    facts = {(a, b): True for a, b in itertools.permutations(HOSTS, 2)}
    for h in HOSTS:
        if h != "M9":
            facts[("M9", h)] = False                 # M9 can be reached but cannot reply
    r = R.measure(HOSTS, lambda s, ds: {d: facts[(s, d)] for d in ds})
    assert ["M9"] in r.islands()


# -- measurement faults are not network faults -------------------------------- #
def test_a_sweep_that_returns_nothing_is_unknown_not_unreachable():
    r = R.measure(HOSTS, lambda s, ds: {} if s == "M1" else {d: True for d in ds})
    assert all(r.get("M1", d) is R.UNKNOWN for d in HOSTS if d != "M1")
    assert ("M1", "M2") not in r.failures
    assert len(r.unknown) == 8


def test_unknown_never_counts_as_reachable():
    """Silence must not become a pass — that is how a broken probe becomes an undeserved tick."""
    r = R.measure(HOSTS, lambda s, ds: {} if s == "M1" else {d: True for d in ds})
    assert not r.all_reach()


def test_a_sweep_that_raises_loses_only_that_host():
    def sweep(s, ds):
        if s == "M5":
            raise OSError("container went away")
        return {d: True for d in ds}

    r = R.measure(HOSTS, sweep)
    assert len(r.unknown) == 8
    assert r.get("M1", "M2") is True


def test_a_partial_sweep_leaves_the_missing_ones_unknown():
    r = R.measure(HOSTS, lambda s, ds: {d: True for d in ds[:3]})
    assert r.get("M1", "M2") is True
    assert len(r.unknown) == 9 * 8 - 9 * 3


def test_non_boolean_results_are_treated_as_unknown():
    r = R.measure(HOSTS, lambda s, ds: {d: "yes" for d in ds})
    assert not r.all_reach() and len(r.unknown) == 72


# -- reporting ---------------------------------------------------------------- #
def test_summary_reads_as_a_finding():
    lan3 = [h for h in HOSTS if LAN[h] == 2]
    r = rel_from([(a, b) for a in HOSTS for b in lan3 if LAN[a] != 2])
    s = r.summary()
    assert "ordered pairs reachable" in s and "2 islands" in s and "M7" in s


def test_empty_topology_does_not_claim_success():
    r = R.measure([], lambda s, ds: {})
    assert not r.all_reach() and r.islands() == []


def test_single_host_has_nothing_to_prove():
    """One station cannot demonstrate reachability, so `all_reach` must not be vacuously true."""
    r = R.measure(["M1"], lambda s, ds: {})
    assert r.pairs == {} and not r.all_reach()
