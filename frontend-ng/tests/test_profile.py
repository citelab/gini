"""The student profile: monotonic transcript (best-band max, attempts accumulate, best time),
mastery derivation, JSON roundtrip, and deterministic union merge (offline reconciliation)."""
from gini.agent.mission import Mission
from gini.domain import lesson as L, objectives as O, probes as P, profile as PR
from gini.domain.topology import Topology


def _lesson():
    return L.from_archetype("reachability-boundary",
                            {"inside": "WEB1", "protected": "DB1", "outsider": "NET", "box": "VPC1"},
                            id="lab03", time_limit="25m")


def _world():
    t = Topology(); v = t.add_device("vpc", "VPC1")
    t.add_device("web_app", "WEB1", parent_id=v.id)
    t.add_device("database", "DB1", parent_id=v.id)
    return O.TopologyWorld(t)


def test_band_ordering():
    assert PR.better_band("partial", "gold") == "gold"
    assert PR.better_band("gold", "pass") == "gold"
    assert PR.band_rank("gold") > PR.band_rank("pass") > PR.band_rank("partial")


def test_record_is_monotonic():
    prof = PR.Profile("s1")
    les = _lesson()
    m1 = Mission(les, now=lambda: 0.0); m1.start(); m1.check(_world())   # behavioral pending → incomplete
    prof.record(les, m1, now=1.0)
    assert not prof.lessons["lab03"].completed

    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True, ("reach", "NET", "DB1", None): False})
    m2 = Mission(les, now=lambda: 0.0); m2.start(); m2.check(_world(), runner)  # gold
    prof.record(les, m2, now=2.0)
    rec = prof.lessons["lab03"]
    assert rec.best_band == "gold" and rec.completed and rec.attempts_used == 2


def test_mastery_and_demonstrated():
    prof = PR.Profile("s1")
    les = _lesson()
    runner = P.FakeRunner({("reach", "WEB1", "DB1", None): True, ("reach", "NET", "DB1", None): False})
    m = Mission(les, now=lambda: 0.0); m.start(); m.check(_world(), runner)
    prof.record(les, m, now=1.0)
    assert prof.mastery() == {"vpc-networking": "proficient"}
    assert prof.demonstrated_concepts() == ["vpc-networking"]


def test_json_roundtrip_and_save_load(tmp_path):
    prof = PR.Profile("s1")
    prof.lessons["x"] = PR.LessonRecord("x", "vpc-networking", best_band="pass", completed=True)
    p = tmp_path / "prof.json"
    prof.save(p)
    back = PR.Profile.load(p)
    assert back.student_id == "s1" and back.lessons["x"].best_band == "pass"
    assert PR.Profile.load(tmp_path / "missing.json", student_id="z").student_id == "z"


def test_merge_is_deterministic_union():
    a = PR.Profile("s"); a.lessons["x"] = PR.LessonRecord("x", "c", best_band="partial",
                                                          attempts_used=2, last_played=1.0)
    b = PR.Profile("s"); b.lessons["x"] = PR.LessonRecord("x", "c", best_band="gold",
                                                          attempts_used=1, completed=True,
                                                          best_time_s=50.0, last_played=3.0)
    b.lessons["y"] = PR.LessonRecord("y", "d", best_band="pass", completed=True)
    m = PR.merge(a, b)
    assert m.lessons["x"].best_band == "gold" and m.lessons["x"].completed
    assert m.lessons["x"].best_time_s == 50.0
    assert "y" in m.lessons                              # union of both
