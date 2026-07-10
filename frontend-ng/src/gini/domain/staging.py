"""Board staging — a lesson can open with part of the canvas already built (M3).

Enables scaffolded labs ("here's a half-built network, finish it") and fault-injection ("I broke the
routing — fix it"). A lesson's `stage:` is an authorable, self-contained spec of devices + links; on
mission start the engine builds them onto the canvas. Pure logic (the caller supplies `add_device` /
`add_link` so it works against the live AppContext in the app and against fakes in tests).

Stage schema (in the lesson YAML):

    stage:
      devices:
        - {ref: r1, type: router}
        - {ref: s1, type: switch}
        - {ref: h1, type: host, x: 100, y: 200}
      links:
        - [h1, s1]        # by ref
        - [s1, r1]

`ref` is a local handle used only to wire links; it never has to match the student's device names.
"""
from __future__ import annotations


def normalize(stage) -> dict:
    """Coerce a stage spec into {'devices': [...], 'links': [[a,b], ...]} (tolerant of shapes)."""
    if not stage:
        return {"devices": [], "links": []}
    if isinstance(stage, dict):
        devices = list(stage.get("devices", []) or [])
        links = []
        for l in stage.get("links", []) or []:
            if isinstance(l, (list, tuple)) and len(l) >= 2:
                links.append([l[0], l[1]])
            elif isinstance(l, dict) and "source" in l and "target" in l:
                links.append([l["source"], l["target"]])
        return {"devices": devices, "links": links}
    return {"devices": [], "links": []}


def is_staged(lesson) -> bool:
    spec = normalize(getattr(lesson, "stage", None))
    return bool(spec["devices"])


def apply(stage, *, add_device, add_link) -> dict:
    """Build a stage. `add_device(type_key, x, y) -> instance` (with an `.id`); `add_link(src_id,
    tgt_id)`. Returns {ref -> instance} so the caller can reference the placed devices. Links to an
    unknown ref are skipped (authoring safety). Never raises on a bad single row — best-effort."""
    spec = normalize(stage)
    placed: dict[str, object] = {}
    for i, d in enumerate(spec["devices"]):
        tk = d.get("type")
        if not tk:
            continue
        ref = d.get("ref") or d.get("name") or f"{tk}{i}"
        try:
            inst = add_device(tk, float(d.get("x", 0) or 0), float(d.get("y", 0) or 0))
        except Exception:
            continue
        placed[ref] = inst
    for a, b in spec["links"]:
        da, db = placed.get(a), placed.get(b)
        if da is None or db is None:
            continue
        try:
            add_link(da.id, db.id)
        except Exception:
            continue
    return placed
