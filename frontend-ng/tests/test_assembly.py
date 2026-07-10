"""The composable-missions engine: capability satisfaction, graph closure, layer filling, and the
genre/level defaults — all deterministic, all producing valid, completable-without-Docker lessons."""
from gini.domain import assembly as A, capabilities as C, fragments as F, lesson as L


def test_capability_satisfaction_walks_the_is_a_hierarchy():
    assert C.satisfies("web-endpoint", "traffic-sink")          # leaf satisfies its super-role
    assert C.satisfies("traffic-sink", "traffic-sink")          # reflexive
    assert not C.satisfies("traffic-sink", "web-endpoint")      # super does NOT satisfy a leaf
    assert C.satisfies("web-tier", "compute-node")              # multi-parent: web-tier is both
    assert C.satisfies("web-tier", "traffic-sink")
    assert not C.unknown_roles(["web-endpoint", "traffic-sink"])


def test_close_graph_pulls_in_a_provider_for_an_unmet_requirement():
    # drive-load requires a traffic-sink; closure PULLS IN a provider so nothing dangles
    chosen, unmet = A.close_graph([F.get("drive-load")])
    assert unmet == []
    assert len(chosen) > 1
    assert any(C.any_satisfies(f.provides, "traffic-sink") for f in chosen)
    # when a core already provides a web-endpoint (a traffic-sink), no extra pull is needed
    chosen2, unmet2 = A.close_graph([F.get("load-balanced-web"), F.get("drive-load")])
    assert unmet2 == [] and len(chosen2) == 2


def test_assemble_fills_exercise_and_observe_for_an_engaged_genre():
    les = A.assemble(["load-balanced-web"], lesson_id="lb")
    layers = {F.get(fid).layer for fid in les.fragments}
    assert {"core", "exercise", "observe"} <= layers
    assert les.genre == "expedition" and les.level == 2
    assert L.is_valid(les)


def test_assemble_dedups_objectives_and_stays_gradable_now():
    les = A.assemble(["load-balanced-web"], lesson_id="lb2")
    ids = [o.id for o in les.objectives]
    assert len(ids) == len(set(ids))                    # unique ids after the merge
    assert all(o.kind == "structural" and o.check for o in les.objectives)   # completable w/o Docker


def test_experience_pin_is_core_only_and_low_level():
    les = A.assemble(["basic-lan"], genre="experience", lesson_id="exp")
    assert les.fragments == ["basic-lan"]
    assert les.genre == "experience" and les.level == 0
    assert les.help == "full_tutor_logged"


def test_pinned_level_overrides_the_default_score():
    les = A.assemble(["load-balanced-web"], level=3, lesson_id="pin")
    assert les.level == 3


def test_combining_two_cores_raises_the_level():
    les = A.assemble(["basic-lan", "service-chain"], lesson_id="combo")
    assert L.is_valid(les)
    assert les.level >= 2                                # two cores + filled layers
    assert {"basic-lan", "service-chain"} <= set(les.fragments)
