"""M4: composition-by-reference — the Teaching Center distributes missions that REFERENCE local
fragments, assembled locally, with a version/existence check so an incompatible pack fails gracefully."""
import yaml

from gini.agent.teaching_center import TeachingCenterClient
from gini.domain import composition as comp, lesson as _lesson


def test_from_composition_assembles_from_local_fragments():
    les = comp.from_composition({"id": "hw1", "fragments": ["basic-lan", "service-chain"],
                                 "genre": "expedition", "title": "Homework 1"})
    assert les.id == "hw1" and les.title == "Homework 1"
    assert {"basic-lan", "service-chain"} <= set(les.fragments)
    assert _lesson.is_valid(les)


def test_missing_refs_is_the_version_check():
    assert comp.missing_refs({"fragments": ["basic-lan"]}) == []
    assert comp.missing_refs({"fragments": ["no-such-fragment"]}) == ["fragment:no-such-fragment"]
    assert comp.missing_refs({"fragments": ["basic-lan"], "requires_roles": ["not-a-role"]}) == [
        "role:not-a-role"]


def test_incompatible_composition_fails_loudly():
    try:
        comp.from_composition({"id": "x", "fragments": ["no-such-fragment"]})
        assert False, "should have raised"
    except comp.CompositionError as e:
        assert "no-such-fragment" in str(e)


def test_inline_lesson_escape_hatch_is_validated():
    les = comp.from_composition({"id": "inline",
                                 "objectives": [{"id": "o", "say": "a switch", "check": "exists(switch)"}]})
    assert _lesson.is_valid(les)
    try:
        comp.from_composition({"id": "bad",
                               "objectives": [{"id": "o", "check": "exists(not_real)"}]})
        assert False
    except comp.CompositionError:
        pass


def _client(pack_text, tmp_path):
    def transport(method, path, body=None):
        if path.endswith("/pack"):
            return 200, pack_text
        return 0, None
    return TeachingCenterClient("http://x", course="c", student_id="s",
                                cache_dir=str(tmp_path), transport=transport)


def test_center_serves_a_composition_pack(tmp_path):
    pack = yaml.safe_dump({"id": "hw1", "fragments": ["basic-lan"], "title": "HW1"})
    les = _client(pack, tmp_path).fetch_lesson("hw1")
    assert les is not None and les.title == "HW1" and "basic-lan" in les.fragments


def test_center_rejects_a_pack_referencing_a_missing_fragment(tmp_path):
    pack = yaml.safe_dump({"id": "hw2", "fragments": ["totally-made-up"]})
    assert _client(pack, tmp_path).fetch_lesson("hw2") is None   # graceful, not a broken mission
