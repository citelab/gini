"""Board staging — a lesson can open with part of the canvas already built (M3).

Enables scaffolded labs ("here's a half-built network, finish it") and fault-injection ("I broke the
routing — fix it"). A lesson's `stage:` is an authorable, self-contained spec of devices + links; on
mission start the engine builds them onto the canvas. Pure logic (the caller supplies `add_device` /
`add_link` so it works against the live AppContext in the app and against fakes in tests).

Two kinds of fault can be injected, and the difference matters pedagogically:

  * **Missing element** — something isn't there (`fix-the-lan`: two subnets, no router). The student
    diagnoses an absence.
  * **Mis-configuration** — everything is there and *looks* right, but a setting is wrong (a host
    addressed into the wrong subnet). Nothing is visibly absent, so the picture can't reveal the
    bug: only *running it* can. This is the harder and more realistic skill, and it's why a
    mis-config mission must be graded by a live probe, never by structure.

Stage schema (in the lesson YAML):

    stage:
      reset: true                  # default: clear the canvas first, so the board is exactly this
      manual_addressing: true      # turn OFF auto-IP, so the addresses below actually stick
      devices:
        - {ref: r1, type: router}
        - {ref: s1, type: switch}
        - {ref: h1, type: host, x: 100, y: 200,
           ips: {s1: 10.0.0.10},                 # IP on the interface facing s1  (mis-config knob)
           properties: {Note: "was working yesterday"}}
      links:
        - [h1, s1]        # by ref
        - [s1, r1]

`ref` is a local handle used only to wire links and address interfaces; it never has to match the
student's device names. `ips` is keyed by the *peer's ref* — the author says "the leg facing s1" and
staging resolves it to the real link id once the link exists, because link ids don't exist until the
board is built.
"""
from __future__ import annotations


def normalize(stage) -> dict:
    """Coerce a stage spec into a canonical dict (tolerant of shapes)."""
    empty = {"devices": [], "links": [], "reset": True, "manual_addressing": False}
    if not stage or not isinstance(stage, dict):
        return empty
    links = []
    for l in stage.get("links", []) or []:
        if isinstance(l, (list, tuple)) and len(l) >= 2:
            links.append([l[0], l[1]])
        elif isinstance(l, dict) and "source" in l and "target" in l:
            links.append([l["source"], l["target"]])
    return {
        "devices": list(stage.get("devices", []) or []),
        "links": links,
        # a staged board is a *designed* board: by default it replaces whatever was on the canvas,
        # or the mission would grade against elements the author never put there.
        "reset": bool(stage.get("reset", True)),
        "manual_addressing": bool(stage.get("manual_addressing", False)),
    }


def is_staged(lesson) -> bool:
    spec = normalize(getattr(lesson, "stage", None))
    return bool(spec["devices"])


def wants_reset(lesson) -> bool:
    spec = normalize(getattr(lesson, "stage", None))
    return bool(spec["devices"]) and spec["reset"]


def apply(stage, *, add_device, add_link, topology=None) -> dict:
    """Build a stage. `add_device(type_key, x, y) -> instance` (with an `.id`); `add_link(src_id,
    tgt_id)` -> link (with an `.id`; a caller that returns None just forfeits IP injection).
    Returns {ref -> instance}. Links to an unknown ref are skipped (authoring safety). Never raises
    on a bad single row — best-effort, because a typo in one stage row must not stop the lesson.

    If `topology` is given and the stage asks for it, manual addressing is switched on — without
    that the compiler auto-assigns IPs and would silently *repair* the very fault we injected.
    """
    spec = normalize(stage)
    if topology is not None and spec["manual_addressing"]:
        topology.manual_addressing = True

    placed: dict[str, object] = {}
    wanted: dict[str, dict] = {}                  # ref -> {peer_ref: ip}
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
        props = d.get("properties") or {}
        if props and hasattr(inst, "properties"):
            inst.properties.update({str(k): str(v) for k, v in props.items()})
        ips = d.get("ips") or {}
        if ips:
            wanted[ref] = {str(k): str(v) for k, v in ips.items()}

    for a, b in spec["links"]:
        da, db = placed.get(a), placed.get(b)
        if da is None or db is None:
            continue
        try:
            link = add_link(da.id, db.id)
        except Exception:
            continue
        lid = getattr(link, "id", None)
        if lid is None:
            continue
        # now that the link exists we can pin each authored IP to the right leg: the author writes
        # "on h1, the interface facing s1", i.e. wanted[near_ref][far_ref].
        for near_ref, far_ref in ((a, b), (b, a)):
            ip = (wanted.get(near_ref) or {}).get(far_ref)
            near = placed[near_ref]
            if ip and hasattr(near, "static_ips"):
                near.static_ips[lid] = ip

    return placed
