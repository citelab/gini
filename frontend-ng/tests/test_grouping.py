"""Pure containment rule for canvas grouping boxes (no Qt)."""
from gini.domain import grouping


def test_innermost_box_wins_for_nesting():
    # a small subnet box drawn inside a big vpc box: a point in the subnet belongs to it
    boxes = [("vpc", 0, 0, 400, 300), ("sub", 50, 50, 150, 120)]
    assert grouping.innermost_box(100, 100, boxes) == "sub"     # inside both -> innermost
    assert grouping.innermost_box(350, 250, boxes) == "vpc"     # only in the vpc
    assert grouping.innermost_box(900, 900, boxes) is None      # outside everything


def test_recompute_assigns_service_to_subnet_and_subnet_to_vpc():
    boxes = [("vpc", 0, 0, 400, 300), ("sub", 50, 50, 150, 120)]
    centers = {
        "vpc": (200, 150), "sub": (125, 110),     # box centres
        "db": (120, 100),                          # a service inside the subnet
        "loose": (700, 700),                       # outside any box
    }
    parents = {"vpc": None, "sub": None, "db": None, "loose": None}
    out = grouping.recompute(centers, boxes, parents)
    assert out["db"] == "sub"                       # innermost box
    assert out["sub"] == "vpc"                      # subnet nests in the vpc
    assert out["vpc"] is None                       # the vpc is top-level
    assert out["loose"] is None


def test_a_larger_box_is_never_captured_by_a_smaller_one_it_overlaps():
    # the vpc's centre falls inside the (smaller) subnet drawn over it; the area rule keeps
    # the big VPC top-level and nests only the smaller subnet inside it.
    boxes = [("vpc", 0, 0, 400, 400), ("sub", 50, 50, 150, 150)]
    centers = {"vpc": (200, 200), "sub": (125, 125)}   # vpc centre lands inside sub
    parents = {"vpc": None, "sub": "vpc"}
    out = grouping.recompute(centers, boxes, parents)
    assert out["vpc"] is None                          # a VPC is nobody's child here
    assert out["sub"] == "vpc"                          # the smaller subnet nests in it


def test_no_boxes_means_no_parents():
    centers = {"db": (10, 10), "cache": (20, 20)}
    assert grouping.recompute(centers, [], {"db": None, "cache": None}) == {
        "db": None, "cache": None}
