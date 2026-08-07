"""Fragment authoring — objectives *by demonstration*.

A professor shouldn't hand-write `path(web_app, database)`. Instead they build the winning arrangement
on the real canvas, and this reads it back into candidate objectives they confirm or prune. The
canvas is the predicate builder (GINI_AUTHORING_DESIGN.md, Seam B). Pure logic over a Topology; the UI
in `ui/` just drives it.

What we derive, auto-levelled onto the ladder (place → connect → group):
  * L1 placement  — `exists(type)`, or `count(type) >= n` when the board has several of one type;
  * L2 connection — `link(a, b)` for each *type-pair* that's directly cabled;
  * L3 containment — `contains_type(box, type)` for each element inside a grouping box (VPC/subnet…).

Live (L4) objectives (`reach/http/…`) can't be read off a static picture — the author adds those
explicitly. We offer the structural skeleton; the human supplies judgment and the live checks.
"""
from __future__ import annotations

from . import devices as _devices
from .content import ENGINE_VERSION, FRAGMENT_SCHEMA


def _type(topo, dev_id: str) -> str:
    d = topo.devices.get(dev_id)
    return d.type_key if d else ""


def _spec(topo, dev_id: str) -> str:
    """`type` for a delta element, `type@slot` for a scaffold element tagged with a slot."""
    d = topo.devices.get(dev_id)
    if d is None:
        return ""
    return f"{d.type_key}@{d.slot}" if getattr(d, "slot", "") else d.type_key


def _slot_label(spec: str) -> str:
    """Human label for a possibly slot-scoped type spec, e.g. 'switch (slot A)'."""
    tk, _, slot = spec.partition("@")
    return _label(tk) + (f" (slot {slot})" if slot else "")


def _is_rider(type_key: str) -> bool:
    """Sources/Sinks are I/O ports, not structural elements — kept out of derived objectives."""
    dt = _devices.get(type_key) if type_key else None
    return bool(dt and getattr(dt, "rider", False))


def _is_container(type_key: str) -> bool:
    """Grouping boxes (VPC / Subnet / Region) — the ones containment is about."""
    dt = _devices.get(type_key)
    cat = getattr(dt, "category", None)
    return bool(dt) and (str(getattr(cat, "value", cat)).lower() in ("group", "boundary", "zone")
                         or type_key in ("vpc", "subnet", "region"))


def derive_objectives(topo, exclude=None) -> list[dict]:
    """Read a Topology → ordered candidate objectives (dicts: id, say, check, kind, level).

    Deterministic and type-based (like everything gradable in GINI), so the derived checks match what
    the student is actually graded on. The author confirms/prunes; nothing is auto-committed.

    `exclude` = device ids belonging to a SCAFFOLD (a loaded dependency/provider). Those elements are
    NOT this fragment's — they're provided at composition time — so they're skipped. A link with BOTH
    endpoints in the scaffold is scaffold-internal (skipped); a link from a NEW element to a scaffold
    one (e.g. router→switch) IS the delta and is kept."""
    from .objectives import CONNECTION, CONTAINMENT, PLACEMENT

    exclude = exclude or set()
    out: list[dict] = []
    seen: set[str] = set()

    def add(key, oid, say, check, level):
        if key in seen:
            return
        seen.add(key)
        out.append({"id": oid, "say": say, "check": check, "kind": "structural", "level": level,
                    "key": key})

    # --- L1: placement -------------------------------------------------------
    # `key` is stable across count changes ("place:host"), so the recorder updates one step as more
    # of a type appear rather than spawning a new step per element.
    counts: dict[str, int] = {}
    for d in topo.devices.values():
        if d.id in exclude:                      # a scaffold (provided dependency) element — not ours
            continue
        if getattr(d.type, "rider", False):      # Sources/Sinks are ports, not structure
            continue
        counts[d.type_key] = counts.get(d.type_key, 0) + 1
    for tk, n in sorted(counts.items()):
        label = _label(tk)
        if n >= 2:
            add(f"place:{tk}", f"place-{tk}", f"Place at least {n} {label}(s)",
                f"count({tk}) >= {n}", PLACEMENT)
        else:
            add(f"place:{tk}", f"place-{tk}", f"Place a {label}", f"exists({tk})", PLACEMENT)

    # --- L2: connection (one objective per directly-cabled TYPE pair) --------
    pairs: set[tuple[str, str]] = set()
    for l in topo.links.values():
        if getattr(l, "kind", "link") == "attach":   # a rider mount is not a cable
            continue
        if l.source_id in exclude and l.target_id in exclude:   # scaffold-internal wiring — not ours
            continue
        # a delta→scaffold link scopes the scaffold end by its slot: link(router, switch@A)
        sa, sb = _spec(topo, l.source_id), _spec(topo, l.target_id)
        ta, tb = sa.split("@")[0], sb.split("@")[0]
        if sa and sb and not _is_rider(ta) and not _is_rider(tb):
            pairs.add(tuple(sorted((sa, sb))))
    for a, b in sorted(pairs):
        key = f"link:{a}|{b}"
        oid = f"link-{a}-{b}".replace("@", "-at-")
        add(key, oid, f"Wire the {_slot_label(a)} to the {_slot_label(b)}",
            f"link({a}, {b})", CONNECTION)

    # --- L3: containment (one per element type inside a grouping box) --------
    contained: set[tuple[str, str]] = set()
    for d in topo.devices.values():
        if d.id in exclude or not d.parent_id:
            continue
        box = _type(topo, d.parent_id)
        if box and _is_container(box) and box != d.type_key:
            contained.add((box, d.type_key))
    for box, tk in sorted(contained):
        add(f"in:{box}|{tk}", f"in-{box}-{tk}", f"The {_label(tk)} sits inside the {_label(box)}",
            f"contains_type({box}, {tk})", CONTAINMENT)

    return out


def live_check(src_type: str, dst_type: str, expect_ok: bool) -> dict:
    """Build a LIVE (L4) objective — a runtime probe the recorder can't capture, because it's an
    assertion about behavior, not a drag-and-drop action. Added by hand in the editor."""
    verb = "reaches" if expect_ok else "cannot reach"
    tail = "ok" if expect_ok else "fail"
    oid = f"live-{src_type}-{dst_type}-{tail}".replace("@", "-at-")
    return {"id": oid,
            "say": f"Live: the {_slot_label(src_type)} {verb} the {_slot_label(dst_type)} "
                   f"(Run to check)",
            "check": "", "kind": "behavioral", "level": 4,
            "probe": f"reach({src_type} -> {dst_type}) == {tail}",
            "key": f"live:{src_type}|{dst_type}|{tail}"}


def derive_contract(topo, exclude=None) -> tuple[list[str], list[str]]:
    """Auto-derive a fragment's (provides, requires) capability contract from the built board — no
    teacher typing. `provides` = the roles of everything built; `requires` = roles the board NEEDS
    (a source needs a target; the grammar's required partners) but does NOT build itself. The
    polarity is pure build-vs-consume. Matching is is-a aware, so a router-gateway satisfies a
    required l3-gateway.

    `exclude` = SCAFFOLD device ids (a loaded dependency). They're not part of THIS fragment, so they
    contribute nothing here — the requirement they represent is added by the caller from the provider
    fragment's own `provides` (a clean 'I require what that block provides')."""
    from . import capabilities as _caps
    from . import connection_rules as _cr

    exclude = exclude or set()
    provides: set[str] = set()
    for d in topo.devices.values():
        if d.id in exclude:
            continue
        provides.update(_caps.roles_for(d.type_key))
    # a required role is met if any provided role is that role or a sub-role of it
    provided_closure: set[str] = set()
    for r in provides:
        provided_closure |= _caps.ancestors(r)

    requires: set[str] = set()
    for d in topo.devices.values():
        if d.id in exclude:
            continue
        for p in _cr.required_partners(d.type_key):        # structural needs from the grammar
            requires.update(_caps.roles_for(p.type_key))
        if getattr(d.type, "role", "") == "source":        # a stimulus needs something to hit
            requires.add("traffic-sink")
    requires = {r for r in requires if r not in provided_closure}   # drop anything we build ourselves
    return sorted(provides), sorted(requires)


def output_check(rider_type: str, metric: str, op: str, value: float) -> dict:
    """Build an OUTPUT objective — an assertion on a Source/Sink's measurement (the gradable output
    of an experiment). Graded via the `measure(...)` probe, so it rides the same runner as reach."""
    from .objectives import LIVE
    v = int(value) if float(value).is_integer() else value
    return {"id": f"out-{rider_type}-{metric}",
            "say": f"{_label(rider_type)}: {metric.replace('_', ' ')} {op} {v}",
            "check": "", "kind": "behavioral", "level": LIVE,
            "probe": f"measure({rider_type}, {metric}) {op} {v}",
            "key": f"measure:{rider_type}|{metric}"}


class Recorder:
    """Scan mode: capture ordered steps as the teacher builds on the canvas. `capture(topology)` is
    called on every canvas change while recording; each new derivable fact becomes a step, IN THE
    ORDER it first appeared. Steps are keyed so counts update in place (a second host bumps the
    existing 'place host' step to count>=2, it doesn't add a new one). Deletes are NOT auto-pruned —
    the teacher removes stray steps by hand (predictable beats clever)."""

    def __init__(self, exclude=None) -> None:
        self.steps: list[dict] = []
        self._by_key: dict[str, dict] = {}
        self.exclude = set(exclude or ())     # scaffold device ids — captured deltas skip these

    def capture(self, topo) -> None:
        for cand in derive_objectives(topo, exclude=self.exclude):
            k = cand["key"]
            existing = self._by_key.get(k)
            if existing is None:
                self.steps.append(cand)
                self._by_key[k] = cand
            else:                                    # same fact, count/label may have grown — refresh
                existing["say"] = cand["say"]
                existing["check"] = cand["check"]

    def result(self) -> list[dict]:
        return list(self.steps)


def _label(type_key: str) -> str:
    dt = _devices.get(type_key)
    return (dt.label if dt else type_key).lower()


def materialize(ctx, objectives, x0: float = 60.0) -> list[str]:
    """Build a minimal board satisfying a provider fragment's STRUCTURAL objectives, as a SCAFFOLD
    the teacher authors a dependent fragment on top of. Returns the created device ids (the scaffold
    set). Behavioral objectives are ignored (a reach can't be placed). Links use a star: every A of a
    `link(a, b)` is wired to the first B, so a LAN provider comes out as hosts-on-a-switch.

    `x0` is the left edge of this scaffold's layout band. Each call otherwise starts at the same x,
    so a fragment with several slots (or several representatives) would stack them all on top of
    each other; the caller passes a per-slot offset to lay them out side by side."""
    import re
    created: list[str] = []
    by_type: dict[str, list[str]] = {}

    def place(type_key: str, n: int) -> None:
        have = by_type.setdefault(type_key, [])
        while len(have) < n:
            inst = ctx.add_device(type_key, x=x0 + len(created) * 70, y=70 + len(by_type) * 90)
            have.append(inst.id)
            created.append(inst.id)

    for o in objectives:                                  # 1. placements
        chk = o.get("check") or ""
        m = re.match(r"^\s*exists\((\w+)\)\s*$", chk)
        if m:
            place(m.group(1), 1); continue
        m = re.match(r"^\s*count\((\w+)\)\s*>=\s*(\d+)", chk)
        if m:
            place(m.group(1), int(m.group(2)))

    for o in objectives:                                  # 2. containment (place T inside a box)
        m = re.match(r"^\s*contains_type\((\w+)\s*,\s*(\w+)\)\s*$", o.get("check") or "")
        if m:
            box, tk = m.group(1), m.group(2)
            place(box, 1)
            place(tk, 1)
            child = by_type[tk][0]
            ctx.topology.devices[child].parent_id = by_type[box][0]

    for o in objectives:                                  # 3. links (star: all A → first B)
        m = re.match(r"^\s*link\((\w+)\s*,\s*(\w+)\)\s*$", o.get("check") or "")
        if m and by_type.get(m.group(2)):
            b0 = by_type[m.group(2)][0]
            for aid in by_type.get(m.group(1), []):
                if aid != b0:
                    try:
                        ctx.add_link(aid, b0)
                    except Exception:                     # noqa: BLE001 — grammar refusal: skip
                        pass
    return created


# -- assemble a fragment from authored parts + save it ----------------------- #
def build_fragment_dict(*, frag_id: str, teaches: str, summary: str, spirit: str,
                        objectives: list[dict], provides=None, requires=None,
                        forks: list[dict] | None = None, stage: dict | None = None,
                        author: str = "", certified: bool = False, slots=None, peerings=None) -> dict:
    """Assemble the fragment YAML dict from authored parts, stamped with the engine version so it can
    be version-gated downstream. Returns a dict ready for `fragment_yaml.fragment_from_dict`."""
    d: dict = {"id": frag_id, "layer": "core", "engine_version": ENGINE_VERSION,
               "schema_version": FRAGMENT_SCHEMA}
    if teaches:
        d["teaches"] = teaches
    if summary:
        d["summary"] = summary
    if spirit:
        d["spirit"] = spirit
    if provides:
        d["provides"] = list(provides)
    if requires:
        d["requires"] = list(requires)
    if author:
        d["author"] = author
    d["objectives"] = [_clean_obj(o) for o in objectives]
    if forks:
        d["forks"] = [{"id": f["id"], "label": f.get("label", ""),
                       "difficulty": int(f.get("difficulty", 2)), "kind": f.get("kind", "converge"),
                       "objectives": [_clean_obj(o) for o in f.get("objectives", [])]}
                      for f in forks]
    if stage:
        d["stage"] = stage
    if slots:
        d["slots"] = [dict(s) for s in slots]
    if peerings:
        d["peerings"] = [dict(p) for p in peerings]
    if certified:
        d["certified"] = True
    return d


def slug(text: str) -> str:
    """A safe fragment/lesson id: lowercase, spaces→hyphens, only [a-z0-9-]. Ids travel in URL routes
    (e.g. /lessons/<id>/pack, matched by [\\w-]+), so a space or punctuation would break the fetch. A
    teacher types 'Simple LAN'; we store 'simple-lan'."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "fragment"


def _clean_obj(o: dict) -> dict:
    out = {"id": o["id"], "say": o.get("say", o["id"])}
    if o.get("kind", "structural") != "structural":
        out["kind"] = o["kind"]
    if o.get("check"):
        out["check"] = o["check"]
    if o.get("probe"):
        out["probe"] = o["probe"]
    if o.get("level"):
        out["level"] = o["level"]
    if o.get("stars"):
        out["stars"] = int(o["stars"])
    return out


def validate_dict(d: dict) -> list[str]:
    """Problems that would make the authored fragment unloadable (empty = good)."""
    from . import fragment_yaml as _fy
    return _fy.validate(_fy.fragment_from_dict(d))


def save_fragment(d: dict) -> str:
    """Write a blessed fragment to the user content layer. Returns the path. Raises if it doesn't
    validate — we never write an ungradable fragment to disk."""
    from . import content as _content
    from . import fragment_yaml as _fy
    problems = validate_dict(d)
    if problems:
        raise ValueError("; ".join(problems))
    frag = _fy.fragment_from_dict(d)
    path = _content.ensure_user_content_dir() / f"{frag.id}.yaml"
    path.write_text(_fy.to_yaml(frag), encoding="utf-8")
    return str(path)
