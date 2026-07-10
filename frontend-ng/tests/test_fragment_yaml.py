"""The YAML migration: foundational fragments are now data. Guards that the packs load + validate,
round-trip losslessly, and reproduce the pre-migration catalog exactly."""
import os

from gini.domain import catalog as C
from gini.domain import fragment_yaml as FY
from gini.domain import fragments as F

_DIR = os.path.join(os.path.dirname(F.__file__), "missions", "networking")


def test_all_packs_load_and_validate():
    loaded = FY.load_dir(_DIR)
    assert len(loaded) == 15
    for frag in loaded.values():
        assert FY.validate(frag) == []


def test_every_fragment_round_trips_losslessly():
    for frag in F.all_fragments():
        assert FY.from_yaml(FY.to_yaml(frag)) == frag


def test_catalog_archetypes_reproduce_the_pre_migration_set():
    ids = {a.id for a in C.all_archetypes()}
    assert len(ids) == 12                                # 11 cores + observe-it
    assert "observe-it" in ids                           # standalone mission AND observe companion
    assert not ({"drive-load", "send-request", "inspect-flows"} & ids)   # pure layers excluded
    assert {f.id for f in F.all_fragments() if not f.catalog} == {
        "drive-load", "send-request", "inspect-flows"}


def test_the_k8s_fix_lives_in_yaml_now():
    k = C.get("k8s-autoscale")
    check = next(o.check for o in k.objectives if o.id == "pod-in-cluster")
    assert "link(k8s_cluster, pod)" in check             # accepts the grammar-taught link


def test_validate_rejects_a_bad_pack():
    bad = FY.fragment_from_dict({"id": "x", "layer": "core",
                                 "objectives": [{"id": "o", "check": "exists(not_a_real_element)"}]})
    assert FY.validate(bad)                              # unknown element type is caught
    bad2 = FY.fragment_from_dict({"id": "y", "layer": "core", "provides": ["not-a-role"]})
    assert FY.validate(bad2)                             # unknown capability role is caught
