"""Per-step star ratings — the progressive-pass difficulty model that replaces the fork.

A step's stars are its difficulty PASS: 0 = the base experiment, 1+ = harder steps the student
unlocks on later passes. This locks the data model + serialization + the pass-selection logic.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp()

from gini.domain import authoring as AU
from gini.domain import fragment_yaml as FY
from gini.domain import objectives as OBJ


def test_objective_has_stars_defaulting_to_zero():
    assert OBJ.Objective(id="a", say="a").stars == 0


def test_objectives_for_pass_selects_by_star_on_objects_and_dicts():
    objs = [OBJ.Objective(id="a", say="a", stars=0),
            OBJ.Objective(id="b", say="b", stars=1),
            OBJ.Objective(id="c", say="c", stars=2)]
    assert [o.id for o in OBJ.objectives_for_pass(objs, 0)] == ["a"]           # base pass
    assert [o.id for o in OBJ.objectives_for_pass(objs, 1)] == ["a", "b"]      # + first harder pass
    assert OBJ.max_stars(objs) == 2
    dicts = [{"id": "a", "stars": 0}, {"id": "b", "stars": 1}]                 # the editor uses dicts
    assert [d["id"] for d in OBJ.objectives_for_pass(dicts, 0)] == ["a"]
    assert OBJ.max_stars(dicts) == 1


def test_stars_survive_build_and_yaml_roundtrip_and_stay_off_the_base():
    d = AU.build_fragment_dict(
        frag_id="s", teaches="x", summary="s", spirit="sp",
        objectives=[{"id": "a", "say": "base", "check": "exists(host)", "level": 1, "stars": 0},
                    {"id": "b", "say": "harder", "check": "exists(router)", "level": 1, "stars": 1}])
    frag = FY.fragment_from_dict(d)
    assert [o.stars for o in frag.objectives] == [0, 1]
    frag2 = FY.from_yaml(FY.to_yaml(frag))                     # roundtrip preserves it
    assert [o.stars for o in frag2.objectives] == [0, 1]
    yaml_text = FY.to_yaml(frag)
    assert yaml_text.count("stars:") == 1                     # star-0 steps stay clean (field omitted)
