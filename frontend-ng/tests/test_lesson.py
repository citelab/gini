"""Lesson parsing + validation: durations, intent block, archetype instantiation, and the
authoring safety net (bad predicates / unknown elements / bad enums are caught)."""
from gini.domain import lesson as L


def test_parse_duration():
    assert L.parse_duration("25m") == 1500
    assert L.parse_duration("90s") == 90
    assert L.parse_duration("1h") == 3600
    assert L.parse_duration(1500) == 1500
    assert L.parse_duration(None) is None


def test_from_dict_carries_intent_and_objectives():
    d = {
        "id": "lab03", "title": "Private DB", "time_limit": "25m", "attempts": 3,
        "intent": {"concept": "vpc-networking", "goal": "reachability boundary",
                   "spirit": "any mechanism", "misconceptions": ["private==unreachable"]},
        "objectives": [
            {"id": "in", "say": "in vpc", "kind": "structural", "check": "contains(VPC1, DB1)"},
            {"id": "shield", "say": "shielded", "kind": "behavioral",
             "probe": "reach(NET -> DB1) == fail"},
        ],
        "complete_when": "all",
    }
    les = L.from_dict(d)
    assert les.time_limit_s == 1500
    assert les.intent.concept == "vpc-networking" and les.intent.spirit == "any mechanism"
    assert les.behavioral_ids() == ["shield"]
    assert L.is_valid(les)


def test_from_archetype_builds_valid_lesson_with_spirit():
    les = L.from_archetype("basic-lan", {}, id="lab01", time_limit="20m")
    assert L.is_valid(les)
    assert les.archetype == "basic-lan"
    assert les.intent.spirit                       # inherited from the archetype
    assert len(les.objectives) == 5


def test_from_archetype_unknown_id_raises():
    import pytest
    with pytest.raises(L.LessonError):
        L.from_archetype("no-such-archetype", {}, id="x")


def test_validation_catches_bad_lessons():
    bad = L.from_dict({"id": "x", "help": "sometimes", "complete_when": "mostly",
                       "objectives": [
                           {"id": "o1", "kind": "structural", "check": "exists(frobnicator)"},
                           {"id": "o1", "kind": "structural", "check": "this is not valid ("},
                           {"id": "o3", "kind": "behavioral"},
                       ]})
    probs = L.validate(bad)
    joined = " ".join(probs)
    assert "unknown element" in joined
    assert "does not parse" in joined
    assert "duplicate objective" in joined
    assert "behavioral objective 'o3' has no probe" in joined
    assert "bad help" in joined and "bad complete_when" in joined


def test_empty_objectives_invalid():
    assert not L.is_valid(L.from_dict({"id": "x", "objectives": []}))
