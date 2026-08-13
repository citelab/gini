"""Guess-the-policy game — the pure case source + timeline heuristic."""
from gini.domain.games.policy_game import (
    POLICY_CLASSES, POLICY_SPEC, classify_timeline, demo_cases, live_cases,
)


def test_demo_deck_has_all_three_policies_labeled():
    cases = demo_cases(seed=3, instances=2)
    assert len(cases) == 6                               # 2 per policy
    truths = [c.truth for c in cases]
    for pol in POLICY_CLASSES:
        assert truths.count(pol) == 2
    assert all(c.truth in POLICY_CLASSES for c in cases)


def test_timeline_heuristic_reads_the_patterns():
    assert classify_timeline([3, 4, 5, 3, 4, 5, 3, 4, 5]) == "round-robin"   # strict cycle
    assert classify_timeline([3, 3, 3, 3, 3, 3, 3, 4, 3]) == "priority"      # one dominates
    assert classify_timeline([]) == "lottery"
    # the seeded demo hints agree with the ground truth
    for c in demo_cases(seed=7):
        assert c.hint == c.truth


def test_demo_spec_and_abbrev():
    assert POLICY_SPEC.id == "guess-policy"
    assert set(POLICY_SPEC.abbrev) == set(POLICY_CLASSES)


def test_live_cases_need_a_window_and_a_policy():
    assert live_cases([3, 4, 5, 3, 4, 5, 3], "round-robin")[0].truth == "round-robin"
    assert live_cases([3, 4], "priority") == []          # too short
    assert live_cases([3, 4, 5, 3, 4, 5], None) == []    # no known policy
