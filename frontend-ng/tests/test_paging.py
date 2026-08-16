"""Page-replacement simulator + the thrashing diagnosis game (pure)."""
from gini.domain.paging_sim import (
    fault_rate, locality, peak_working_set, run_features, simulate, unique_pages, ws_growth,
)
from gini.domain.games.thrash_game import (
    THRASH_CLASSES, THRASH_SPEC, classify_thrash, demo_cases,
)


def test_belady_anomaly_for_fifo():
    # the classic string where FIFO faults INCREASE going 3 -> 4 frames
    b = [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]
    assert simulate(b, 3, "fifo").faults == 9
    assert simulate(b, 4, "fifo").faults == 10               # Belady's anomaly


def test_optimal_is_no_worse_than_lru():
    r = [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2, 1, 2, 0, 1, 7, 0, 1]
    assert simulate(r, 3, "opt").faults <= simulate(r, 3, "lru").faults
    # cold misses only: every distinct page faults at least once
    assert simulate(r, 3, "opt").faults >= unique_pages(r)


def test_cold_start_and_full_hit_bounds():
    r = [1, 2, 3, 1, 2, 3]
    assert simulate(r, 3, "lru").faults == 3                 # 3 cold misses, then all hits
    assert simulate(r, 3, "lru").evictions == 0              # never over budget
    r2 = [1, 1, 1, 1]
    assert simulate(r2, 2, "fifo").faults == 1               # one cold miss


def test_metrics_capture_the_signature():
    loop = [p for _ in range(6) for p in range(8)]           # 8-page loop
    assert unique_pages(loop) == 8
    assert peak_working_set(loop, window=10) >= 8
    assert locality(loop, window=8) == 1.0                   # reuse distance == 8, within window
    growing = list(range(4)) * 3 + list(range(20))
    assert ws_growth(growing) > 0.5                          # distinct set expands late


def test_thrash_generators_match_their_labels():
    assert set(THRASH_CLASSES) == {"healthy", "too few frames",
                                   "working set too big", "poor locality"}
    for seed in range(6):
        for c in demo_cases(seed=seed):
            assert classify_thrash(c.signature) == c.truth, (seed, c.truth, c.signature)


def test_thrash_signature_is_real_run_features():
    cases = {c.truth: c for c in demo_cases(seed=0)}
    assert cases["healthy"].signature["fault_rate"] < 0.25            # frames cover the loop
    assert cases["too few frames"].signature["locality"] > 0.8       # good locality, small frames
    assert cases["too few frames"].signature["frames"] < \
        cases["too few frames"].signature["working_set"]
    assert cases["working set too big"].signature["ws_growth"] >= 0.5
    assert cases["poor locality"].signature["locality"] < 0.4
    assert THRASH_SPEC.id == "thrash-diagnose"
