"""The Diagnose game engine — pure state machine + the process-game case source."""
from gini.domain.diagnose import (
    GRADED, PRACTICE, Case, DiagnoseSession, GameSpec, accuracy, confusion_matrix, per_class,
)

SPEC = GameSpec(id="t", title="T", prompt="?", classes=["a", "b", "c"])
CASES = [Case(id="1", signature={}, truth="a", subtitle="one", hint="a"),
         Case(id="2", signature={}, truth="b", subtitle="two", hint="c")]


def test_confusion_matrix_and_accuracy_and_per_class():
    pairs = [("a", "a"), ("b", "b"), ("a", "b"), ("a", "a")]
    m = confusion_matrix(pairs, ["a", "b", "c"])
    assert m[("a", "a")] == 2 and m[("a", "b")] == 1 and m[("b", "b")] == 1
    assert round(accuracy(pairs), 2) == 0.75 and accuracy([]) == 0.0
    pc = per_class(pairs, ["a", "b", "c"])
    assert pc["a"]["n"] == 3 and round(pc["a"]["recall"], 2) == 0.67   # 2 of 3 'a' correct
    assert pc["b"]["precision"] == 0.5                                 # 1 of 2 'b' guesses right


def test_practice_mode_serves_forever_and_reveals_hint():
    s = DiagnoseSession(SPEC, CASES, mode=PRACTICE, seed=1)
    for _ in range(20):
        assert s.next() is not None            # never exhausts in practice
    v = s.guess("a")
    assert set(("correct", "truth", "subtitle", "hint")) <= set(v)
    assert v["hint"] is not None               # hint shown in practice


def test_graded_mode_runs_a_fixed_deck_and_hides_hint():
    s = DiagnoseSession(SPEC, CASES, mode=GRADED, deck=3, seed=1)
    served = 0
    while s.next() is not None:
        served += 1
        v = s.guess("a")
        assert v["hint"] is None               # hint suppressed in graded
    assert served == 3 and s.finished          # exactly the deck size
    assert s.remaining() == 0


def test_selection_is_seeded_and_reproducible():
    a = DiagnoseSession(SPEC, CASES, seed=42)
    b = DiagnoseSession(SPEC, CASES, seed=42)
    seq_a = [a.next().id for _ in range(8)]
    seq_b = [b.next().id for _ in range(8)]
    assert seq_a == seq_b                       # same seed -> same deck (mission replay)


def test_guess_records_pairs_matrix_and_score():
    s = DiagnoseSession(SPEC, CASES, seed=0)
    s.current = CASES[0]                         # truth 'a'
    assert s.guess("a")["correct"] is True
    s.current = CASES[1]                         # truth 'b'
    assert s.guess("a")["correct"] is False
    assert s.pairs == [("a", "a"), ("b", "a")]
    assert s.score() == (1, 2) and round(s.accuracy(), 2) == 0.5
    assert s.matrix()[("b", "a")] == 1          # the confusion cell
    s.reset()
    assert s.pairs == [] and s.current is None and not s.finished


def test_estimate_grader_scores_by_closeness():
    spec = GameSpec(id="e", title="E", prompt="how many?", classes=[], answer="estimate",
                    tolerance=2, unit="faults")
    s = DiagnoseSession(spec, [Case(id="1", signature="x", truth=10)], seed=0)
    s.current = s._cases[0]
    assert s.guess(11)["correct"] is True          # within ±2
    s.current = s._cases[0]
    assert s.guess(20)["correct"] is False         # too far
    assert s.score() == (1, 2) and round(s.accuracy(), 2) == 0.5
    assert s.mean_abs_error() == (1 + 10) / 2       # |11-10| and |20-10|


def test_estimate_relative_tolerance_and_exact():
    rel = GameSpec(id="r", title="R", prompt="?", classes=[], answer="estimate",
                   tolerance=0.1, relative=True)
    s = DiagnoseSession(rel, [Case(id="1", signature="x", truth=100)])
    s.current = s._cases[0]
    assert s.guess(109)["correct"] is True         # within 10%
    s.current = s._cases[0]
    assert s.guess(120)["correct"] is False
    exact = GameSpec(id="x", title="X", prompt="PA?", classes=[], answer="estimate", tolerance=0)
    e = DiagnoseSession(exact, [Case(id="1", signature="x", truth=0x2ABC)])
    e.current = e._cases[0]
    assert e.guess(0x2ABC)["correct"] is True       # exact only
    e.current = e._cases[0]
    assert e.guess(0x2ABD)["correct"] is False


def test_empty_pool_serves_nothing():
    s = DiagnoseSession(SPEC, [], seed=0)
    assert s.next() is None and not s.has_cases()


def test_process_game_cases_are_labeled_from_ground_truth():
    from gini.domain.games.process_game import PROCESS_SPEC, demo_cases
    cases = demo_cases()
    assert PROCESS_SPEC.id == "process-classify"
    names = {c.subtitle for c in cases}
    assert {"spin", "writer", "grind"} <= names
    by_name = {c.subtitle: c for c in cases}
    assert by_name["spin"].truth == "cpu-bound"      # oracle ground truth on the Case
    assert by_name["writer"].truth == "io-bound"
    assert all(c.hint is not None for c in cases)     # rule-classifier baseline present
