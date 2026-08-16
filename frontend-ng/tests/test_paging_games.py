"""Paging-cluster + address-translation games — all truth computed exactly (no guessing)."""
from gini.domain.diagnose import DiagnoseSession
from gini.domain.games.paging_games import (
    BELADY_SPEC, FAULTCOUNT_SPEC, SHOWDOWN_SPEC, belady_cases, faultcount_cases, showdown_cases,
)
from gini.domain.games.translate_game import PGSIZE, TRANSLATE_SPEC, demo_cases, live_cases
from gini.domain.paging_sim import simulate


def test_faultcount_truth_matches_the_simulator():
    for c in faultcount_cases():
        s = c.signature
        assert c.truth == simulate(s["refs"], s["frames"], "lru").faults
    assert FAULTCOUNT_SPEC.answer == "estimate" and FAULTCOUNT_SPEC.tolerance == 2


def test_faultcount_grades_within_tolerance():
    s = DiagnoseSession(FAULTCOUNT_SPEC, faultcount_cases())
    c = s._cases[0]; s.current = c
    assert s.guess(c.truth + 1)["correct"] is True          # ±2 forgiving
    s.current = c
    assert s.guess(c.truth + 5)["correct"] is False


def test_belady_labels_match_fifo_behavior():
    for c in belady_cases():
        s = c.signature
        f0 = simulate(s["refs"], s["frames"], "fifo").faults
        f1 = simulate(s["refs"], s["frames"] + 1, "fifo").faults
        assert c.truth == ("fewer faults" if f1 < f0 else "same or more")
    # the classic Belady string is present and correctly labeled 'same or more' (anomaly)
    anomaly = [c for c in belady_cases() if c.signature["refs"][:4] == [1, 2, 3, 4]][0]
    assert anomaly.truth == "same or more"


def test_showdown_labels_match_fifo_vs_lru():
    for c in showdown_cases():
        s = c.signature
        fifo = simulate(s["refs"], s["frames"], "fifo").faults
        lru = simulate(s["refs"], s["frames"], "lru").faults
        expect = "FIFO" if fifo < lru else "LRU" if lru < fifo else "tie"
        assert c.truth == expect
    assert set(SHOWDOWN_SPEC.classes) == {"FIFO", "LRU", "tie"}


def test_translate_demo_truth_is_the_real_pa():
    for c in demo_cases(seed=1):
        va = c.signature["va"]
        row = next(r for r in c.signature["rows"] if r[0] <= va < r[0] + PGSIZE)
        assert c.truth == row[1] + (va - row[0])            # PA base + offset
    assert TRANSLATE_SPEC.answer == "estimate" and TRANSLATE_SPEC.tolerance == 0  # exact


def test_translate_live_from_real_leaves():
    class Pte:
        def __init__(self, va, pa, perms):
            self.va, self.pa, self.perms = va, pa, perms
    leaves = [Pte(0x1000, 0x87000, "r-x u"), Pte(0x2000, 0x88000, "rw- u"),
              Pte(0x3000, 0x89000, "rw- u")]
    cases = live_cases(leaves, seed=0)
    assert cases
    for c in cases:
        va = c.signature["va"]
        row = next(r for r in c.signature["rows"] if r[0] <= va < r[0] + PGSIZE)
        assert c.truth == row[1] + (va - row[0])            # exact PA from the real mapping
    assert live_cases([], seed=0) == []                     # no page table -> no cases


def test_translate_grades_exactly():
    s = DiagnoseSession(TRANSLATE_SPEC, demo_cases(seed=0))
    c = s._cases[0]; s.current = c
    assert s.guess(c.truth)["correct"] is True
    s.current = c
    assert s.guess(c.truth + 1)["correct"] is False         # tolerance 0 -> exact only
