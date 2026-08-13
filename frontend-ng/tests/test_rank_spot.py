"""Rank + spot grader types (engine) and the two paging games that use them."""
from gini.domain.diagnose import Case, DiagnoseSession, GameSpec, order_score
from gini.domain.games.paging_games import (
    NEXTEVICT_SPEC, POLICYRANK_SPEC, nextevict_cases, policyrank_cases,
)
from gini.domain.paging_sim import resident_state, simulate


def test_order_score_pairwise_agreement():
    assert order_score(["a", "b", "c"], ["a", "b", "c"]) == 1.0        # perfect
    assert order_score(["c", "b", "a"], ["a", "b", "c"]) == 0.0        # fully reversed
    assert round(order_score(["a", "c", "b"], ["a", "b", "c"]), 2) == 0.67  # 2 of 3 pairs right


def test_rank_session_hit_requires_perfect_order():
    spec = GameSpec("r", "R", "order", classes=[], answer="rank")
    s = DiagnoseSession(spec, [Case("1", "x", ["a", "b", "c"], options=["a", "b", "c"])])
    s.current = s._cases[0]
    v = s.guess(["a", "b", "c"])
    assert v["correct"] is True and v["partial"] == 1.0
    s.current = s._cases[0]
    v = s.guess(["a", "c", "b"])
    assert v["correct"] is False and round(v["partial"], 2) == 0.67   # partial credit tracked
    assert round(s.mean_order_score(), 2) == round((1.0 + 0.67) / 2, 2)


def test_spot_session_scores_exact_pick():
    spec = GameSpec("s", "S", "pick", classes=[], answer="spot")
    s = DiagnoseSession(spec, [Case("1", "x", "P2", options=["P1", "P2", "P3"])])
    s.current = s._cases[0]
    assert s.guess("P2")["correct"] is True
    s.current = s._cases[0]
    assert s.guess("P1")["correct"] is False
    assert s.score() == (1, 2)


def test_next_evict_truth_is_the_policy_victim():
    assert NEXTEVICT_SPEC.answer == "spot"
    for c in nextevict_cases():
        s = c.signature
        resident, order = resident_state(s["refs"], s["frames"], s["policy"].lower())
        assert c.truth == order[0]                       # next victim = head of the policy order
        assert c.truth in c.options                       # the victim is one of the candidates
        assert set(c.options) == resident                 # candidates are exactly the resident set


def test_policy_rank_truth_orders_by_faults():
    assert POLICYRANK_SPEC.answer == "rank"
    cases = policyrank_cases()
    assert cases                                          # at least one strictly-ordered string
    for c in cases:
        s = c.signature
        counts = {p.upper(): simulate(s["refs"], s["frames"], p).faults
                  for p in ("fifo", "lru", "opt")}
        assert c.truth == sorted(counts, key=lambda k: counts[k])   # fewest faults first
        assert counts[c.truth[0]] <= counts[c.truth[-1]]
