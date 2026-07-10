"""M5: the authoring loop — intent -> proposed composition -> playtest -> releasable pack."""
import yaml

from gini.agent import authoring
from gini.domain import composition as comp, lesson as _lesson


def _scripted(resp):
    return lambda _p: resp


def test_propose_emits_a_reference_composition():
    spec = authoring.propose(
        "teach them to build a switched LAN",
        _scripted('{"primary":"basic-lan","secondary":"","genre":"experience","title":"Build a LAN","brief":"x"}'),
        lesson_id="hw1")
    assert spec["id"] == "hw1"
    assert "basic-lan" in spec["fragments"]              # references a LOCAL fragment id
    assert all(isinstance(f, str) for f in spec["fragments"])
    assert spec["title"] == "Build a LAN"


def test_playtest_reports_a_gradable_mission():
    report = authoring.playtest({"id": "hw1", "fragments": ["basic-lan", "service-chain"],
                                 "genre": "expedition"})
    assert report["ok"]
    assert report["objectives"]                          # the teacher sees what's graded
    assert "objectives" in report["summary"]


def test_playtest_catches_a_bad_reference():
    report = authoring.playtest({"id": "x", "fragments": ["no-such"]})
    assert not report["ok"]
    assert any("no-such" in p for p in report["problems"])


def test_to_pack_round_trips_into_a_playable_lesson():
    spec = {"id": "hw1", "fragments": ["basic-lan"], "genre": "experience", "title": "HW1"}
    pack = authoring.to_pack(spec)
    reloaded = yaml.safe_load(pack)
    les = comp.from_composition(reloaded)                # the Center would do exactly this
    assert _lesson.is_valid(les) and les.title == "HW1"


def test_ratify_applies_teacher_edits():
    spec = authoring.ratify({"id": "hw1", "fragments": ["basic-lan"], "genre": "experience"},
                            genre="challenge", title="Harder LAN")
    assert spec["genre"] == "challenge" and spec["title"] == "Harder LAN"
