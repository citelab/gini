"""Scoring: completion via complete_when, and the broad bands (gold/pass/partial/incomplete)
with pending behavioral objectives counting as not-yet-met."""
from gini.domain import scoring as S
from gini.domain.objectives import MET, PENDING, UNMET, ObjectiveResult


def _r(*statuses):
    return [ObjectiveResult(f"o{i}", "", "structural", s) for i, s in enumerate(statuses)]


def test_complete_when_all_any_atleast():
    assert S.is_complete(_r(MET, MET), "all")
    assert not S.is_complete(_r(MET, UNMET), "all")
    assert S.is_complete(_r(MET, UNMET), "any")
    assert not S.is_complete(_r(UNMET, UNMET), "any")
    assert S.is_complete(_r(MET, MET, UNMET), "at_least(2)")
    assert not S.is_complete(_r(MET, UNMET, UNMET), "at_least(2)")


def test_bands():
    assert S.score(_r(MET, MET), on_time=True).band == S.GOLD
    assert S.score(_r(MET, MET), on_time=False).band == S.PASS
    assert S.score(_r(MET, UNMET)).band == S.PARTIAL
    assert S.score(_r(UNMET, UNMET)).band == S.INCOMPLETE


def test_pending_counts_as_unmet_and_is_reported():
    sc = S.score(_r(MET, PENDING), complete_when="all", on_time=True)
    assert not sc.complete and sc.band == S.PARTIAL
    assert sc.pending == 1
    assert "awaiting a run" in sc.summary


def test_summary_counts():
    sc = S.score(_r(MET, MET, UNMET))
    assert sc.met == 2 and sc.total == 3
    assert sc.summary == "2/3 objectives"
