"""Spatial containment for canvas grouping boxes (VPC / Subnet / Region).

Membership is decided by geometry: an element belongs to the *innermost* box whose
rectangle contains the element's centre, mirroring how you'd read a cloud diagram —
a database drawn inside a Subnet drawn inside a VPC belongs to that Subnet (and through
it, that VPC). This module is pure (no Qt) so the rule is unit-testable; the canvas feeds
it scene rectangles and applies the resulting parent_id assignments.
"""
from __future__ import annotations

# The element types drawn as grouping boxes (not every is_container type — k8s_cluster and
# Pod are containers too but are real nodes that take part in the grammar / X-ray).
BOX_TYPES = ("vpc", "cloud_subnet", "region")

# A box on the canvas: (id, x, y, w, h) in scene coordinates.
Box = tuple[str, float, float, float, float]


def _contains(box: Box, cx: float, cy: float) -> bool:
    _id, x, y, w, h = box
    return x <= cx <= x + w and y <= cy <= y + h


def _area(box: Box) -> float:
    return box[3] * box[4]


def innermost_box(cx: float, cy: float, boxes, exclude=(), min_area: float = 0.0) -> str | None:
    """The id of the smallest-area box containing (cx, cy), or None. `exclude` lists ids
    to ignore (never self, nor a descendant). `min_area` filters to boxes strictly larger
    than this — so a container only nests inside a *bigger* container, never a smaller box
    it happens to overlap (a VPC is never captured by a Subnet drawn over its centre)."""
    hits = [b for b in boxes
            if b[0] not in exclude and _area(b) > min_area and _contains(b, cx, cy)]
    if not hits:
        return None
    return min(hits, key=_area)[0]


def _descendants(box_id: str, parent_of: dict) -> set:
    """Every id whose parent chain currently passes through box_id (so an inner box can't
    accidentally 'capture' its own ancestor when boxes overlap)."""
    kids = {box_id}
    while True:
        nxt = {i for i, p in parent_of.items() if p in kids and i not in kids}
        if not nxt:
            return kids - {box_id}
        kids |= nxt


def recompute(centers: dict, boxes, parent_of: dict) -> dict:
    """Compute fresh parent_id for every placeable item.

    centers   : {id: (cx, cy)} for every node AND box on the canvas.
    boxes     : iterable of Box (the grouping rectangles).
    parent_of : current {id: parent_id|None}, used only to skip self/descendants.

    Returns {id: new_parent_id_or_None}.
    """
    boxes = list(boxes)
    area_of = {b[0]: _area(b) for b in boxes}      # a box's own area (0 for plain nodes)
    out: dict = {}
    for iid, (cx, cy) in centers.items():
        exclude = {iid} | _descendants(iid, parent_of)
        out[iid] = innermost_box(cx, cy, boxes, exclude=exclude,
                                 min_area=area_of.get(iid, 0.0))
    return out
