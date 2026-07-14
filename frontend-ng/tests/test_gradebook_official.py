"""M6: authoritative gradebook — the server re-grades structural objectives, so a tampered client
band is downgraded; re-grade also works through compositions."""
from gini.domain import grader as G, lesson as _lesson
from gini.domain.topology import Topology


def _lesson_basic():
    return _lesson.from_archetype("basic-lan", {}, id="basic-lan")


def _complete_lan():
    t = Topology()
    sw = t.add_device("switch", "S"); r = t.add_device("router", "R")
    for i in range(2):
        h = t.add_device("host", f"H{i}"); t.add_link(h.id, sw.id)
    t.add_link(sw.id, r.id)
    return t


def _submission(topology, *, student, band):
    return {"student": student, "lesson_id": "basic-lan", "band": band,
            "snapshot": G.snapshot_of(topology), "objective_results": [], "time_taken": 1}


def test_honest_completion_is_confirmed():
    r = G.official_result(_lesson_basic(), _submission(_complete_lan(), student="a", band="gold"))
    assert r.band in ("gold", "pass") and not r.regraded_down


def test_tampered_band_is_downgraded():
    empty = Topology()                                    # nothing built, but the client claims gold
    r = G.official_result(_lesson_basic(), _submission(empty, student="b", band="gold"))
    assert r.regraded_down and r.band == "incomplete"


def test_official_gradebook_uses_the_regraded_band():
    subs = [_submission(_complete_lan(), student="a", band="gold"),
            _submission(Topology(), student="b", band="gold")]     # b tampered
    gb = G.gradebook_official(subs, resolve_lesson=lambda lid: _lesson_basic())
    assert G.band_rank(gb.band("a", "basic-lan")) >= G.band_rank("pass")
    assert gb.band("b", "basic-lan") == "incomplete"      # the lie is corrected
    # the naive gradebook would have trusted the claim
    assert G.gradebook(subs).band("b", "basic-lan") == "gold"


def test_regrade_through_a_composition():
    spec = {"id": "hw", "fragments": ["basic-lan"], "genre": "experience"}
    r = G.official_from_composition(spec, _submission(_complete_lan(), student="a", band="gold"))
    assert r.band in ("gold", "pass")
