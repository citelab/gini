"""Objective predicate engine — the GINI-checkable win conditions behind Missions.

An Objective is one win condition. Two kinds:

  • **structural** — a boolean predicate over the topology GRAPH, evaluated instantly and
    offline (no run). Written in a tiny, safe expression language:
        exists(<type>) · count(<type>) >= n · linked(a, b) · connected(a, b) ·
        contains(box, member) · property(dev, key) == 'val'
    combined with `and` / `or` / `not` and comparisons.
  • **behavioral** — a probe over the RUNNING system ("reach(a -> b) == ok"). GINI's runtime
    is the oracle; these need a run, so in Phase 1 they evaluate to **pending** (Phase 2 wires
    the probe harness). The string is stored opaquely here.

Design notes:
- The expression evaluator uses Python's `ast` in a *whitelist* — no `eval`, no attribute
  access, no arbitrary calls — so a lesson file can't execute anything. Only the handful of
  predicate functions below are callable.
- Everything is pure and evaluated against a `World` (duck-typed), so it's unit-testable with
  a real `Topology` or a fake. `TopologyWorld` adapts the live canvas model.

Convention: `exists`/`count` take an element **type_key**; `linked`/`connected`/`contains`/
`property` take element **names** (as placed on the canvas).
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass

from .devices import REGISTRY

# ---- results -------------------------------------------------------------- #
MET = "met"
UNMET = "unmet"
PENDING = "pending"          # behavioral, awaiting a run (Phase 2)
DEFECTIVE = "defective"      # the OBJECTIVE is broken, not the student's work — see below

# `defective` exists because the alternative is a silent misattribution. A probe or predicate that
# does not parse is an authoring bug, but reporting it as `unmet` puts it in the same column as
# work the student failed to do — and with machine-generated Activity Observation Plans, a single
# hallucinated probe string would quietly read as "they didn't do it". Never fold a broken
# objective into a verdict about a person.


@dataclass
class Objective:
    id: str
    say: str                 # student-facing one-liner
    kind: str = "structural"  # structural | behavioral
    check: str = ""          # structural predicate expression
    probe: str = ""          # behavioral probe (opaque in Phase 1)
    level: int | None = None  # explicit ladder tier; None = derived from the predicate
    stars: int = 0           # difficulty PASS: 0 = base experiment, 1+ = harder progressive passes

    def is_behavioral(self) -> bool:
        return self.kind == "behavioral"


def _stars_of(o) -> int:
    """Star rating of an objective given as either an Objective or a plain dict."""
    v = o.get("stars", 0) if isinstance(o, dict) else getattr(o, "stars", 0)
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def objectives_for_pass(objectives, pass_level: int) -> list:
    """The objectives active at star-pass `pass_level` — everything rated at or below it. Pass 0 is
    the base experiment; each higher pass switches on the next tier of harder (starred) steps, so the
    student walks the experiment progressively rather than facing it all at once."""
    return [o for o in objectives if _stars_of(o) <= pass_level]


def max_stars(objectives) -> int:
    """The deepest star-pass this set defines (0 = no harder passes, just the base)."""
    return max((_stars_of(o) for o in objectives), default=0)


@dataclass
class ObjectiveResult:
    id: str
    say: str
    kind: str
    status: str              # met | unmet | pending
    level: int = 1           # which rung of the progressive ladder this sits on

    @property
    def met(self) -> bool:
        return self.status == MET


# ---- the safe expression evaluator ---------------------------------------- #
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Gt: operator.gt,
        ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le}

# name-based predicates take element NAMES; type-based ones take element TYPE_KEYS. Type-based
# predicates are what missions should use, so an objective matches what the student built
# regardless of the (auto-generated) device names.
_PREDICATES = {"exists", "count", "linked", "connected", "contains", "property", "prop",
               "link", "path", "contains_type", "through", "all_linked", "property_type"}
_TYPE_ARG_FUNCS = {"exists", "count", "link", "path", "contains_type", "through",
                   "all_linked", "property_type"}  # first arg is a type_key

# How many LEADING arguments of a type-based predicate are type_keys. Every argument is one by
# default; `property_type(type_key, key[, value])` is the exception — only its first arg names an
# element, and the rest are a property key and value. Without this, validation would report
# `property_type('router', 'delay')` as referring to an element type called "delay".
_TYPE_ARITY = {"property_type": 1}


class PredicateError(ValueError):
    """A structural check that doesn't parse or uses an unknown function."""


def slot_match(dev_slot: str, spec_slot: str) -> bool:
    """Does a device tagged `dev_slot` fall under the scope `spec_slot`? An empty scope matches any
    device (unscoped type predicate). Otherwise it matches the exact label OR anything NESTED beneath
    it: composition labels are hierarchical with '_' segments (`pods0_lans1` is inside `pods0`), so a
    `host@pods0` predicate reaches every host in pod 0's sub-networks. This is what makes recursive,
    multi-level compositions gradable by the same `type@slot` oracle."""
    dev_slot = dev_slot or ""
    if not spec_slot:
        return True
    return dev_slot == spec_slot or dev_slot.startswith(spec_slot + "_")


# ---- the progressive ladder ------------------------------------------------ #
# Objectives are ordered basic → advanced so a student always has an obvious next move: place the
# elements, wire them, group them, then prove it live. The tier is DERIVED from the predicate, so
# every mission (existing and future) gets the ladder for free — no hand-tagging.
PLACEMENT, CONNECTION, CONTAINMENT, LIVE = 1, 2, 3, 4

_PRED_LEVEL = {
    "exists": PLACEMENT, "count": PLACEMENT, "property": PLACEMENT, "prop": PLACEMENT,
    "property_type": PLACEMENT,
    "link": CONNECTION, "path": CONNECTION, "through": CONNECTION, "all_linked": CONNECTION,
    "linked": CONNECTION, "connected": CONNECTION,
    "contains": CONTAINMENT, "contains_type": CONTAINMENT,
}

LEVEL_NAME = {PLACEMENT: "Place the elements", CONNECTION: "Make the connections",
              # L3 carries both containment (inside a VPC) and isolation (shielded / no bypass) —
              # the "get the structure right" rung, not merely grouping.
              CONTAINMENT: "Group & isolate", LIVE: "Prove it live (Run / Check)"}


def check_level(expr: str) -> int:
    """The tier of a structural check — the HARDEST predicate it uses (a compound is as advanced as
    its most advanced part)."""
    try:
        tree = ast.parse(expr or "", mode="eval")
    except SyntaxError:
        return PLACEMENT
    levels = [_PRED_LEVEL.get(n.func.id, PLACEMENT)
              for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    return max(levels) if levels else PLACEMENT


def level_of(objective) -> int:
    """Which rung of the ladder an objective sits on. An explicit `level:` (authored in the YAML)
    wins — the derived tier is only a sensible default, and some checks are semantically more
    advanced than their predicate suggests (an isolation check reads as `path`, but it's an
    advanced idea)."""
    explicit = getattr(objective, "level", None)
    if explicit:
        return int(explicit)
    if getattr(objective, "kind", "structural") == "behavioral":
        return LIVE
    return check_level(getattr(objective, "check", ""))


def by_level(objectives) -> list:
    """Objectives ordered basic → advanced (stable within a tier, so authored order is preserved)."""
    return sorted(objectives, key=level_of)


def _arg(node) -> object:
    """A predicate argument is a bare identifier (element name/type), a literal, or a SLOT-scoped
    type `type@slot`. We reuse Python's `@` (matmul) operator for scoping, so `switch@A` parses as
    BinOp(Name, MatMult, Name) and we render it back to the string 'switch@A' for the World."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.MatMult)
            and isinstance(node.left, ast.Name) and isinstance(node.right, ast.Name)):
        return f"{node.left.id}@{node.right.id}"
    raise PredicateError(f"unsupported argument: {ast.dump(node)}")


def _eval(node, world) -> object:
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, world) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, world)
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1:
            raise PredicateError("chained comparisons are not supported")
        left = _eval(node.left, world)
        right = _eval(node.comparators[0], world)
        op = _CMP.get(type(node.ops[0]))
        if op is None:
            raise PredicateError("unsupported comparison operator")
        return op(left, right)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _PREDICATES:
            raise PredicateError(f"unknown predicate: {ast.dump(node.func)}")
        args = [_arg(a) for a in node.args]
        return _call(node.func.id, args, world)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    raise PredicateError(f"unsupported expression: {ast.dump(node)}")


def _call(fn: str, args: list, world) -> object:
    if fn == "exists":
        return world.exists(str(args[0]))
    if fn == "count":
        return world.count(str(args[0]))
    if fn == "linked":
        return world.linked(str(args[0]), str(args[1]))
    if fn == "connected":
        return world.connected(str(args[0]), str(args[1]))
    if fn == "contains":
        return world.contains(str(args[0]), str(args[1]))
    if fn in ("property", "prop"):
        return world.prop(str(args[0]), str(args[1]))
    if fn == "property_type":           # SOME device of this type carries the property
        want = args[2] if len(args) > 2 else None
        return world.prop_type(str(args[0]), str(args[1]), want)
    if fn == "link":                    # a link between ANY typeA device and ANY typeB device
        return world.link_types(str(args[0]), str(args[1]))
    if fn == "path":                    # a path between some typeA and some typeB device
        return world.path_types(str(args[0]), str(args[1]))
    if fn == "contains_type":           # a memberType device inside a boxType box
        return world.contains_types(str(args[0]), str(args[1]))
    if fn == "through":                 # every src->dst path crosses a gate (a chokepoint)
        return world.through_types(str(args[0]), str(args[1]), str(args[2]))
    if fn == "all_linked":              # EVERY type_a device has a direct link to SOME type_b device
        return world.all_linked_types(str(args[0]), str(args[1]))
    raise PredicateError(f"unknown predicate: {fn}")


def parse_check(expr: str) -> ast.AST:
    """Parse+validate a structural predicate (raises PredicateError). Used by lesson
    validation so a bad predicate fails at authoring time, not mid-mission."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise PredicateError(f"syntax error in check {expr!r}: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _PREDICATES:
                raise PredicateError(f"unknown predicate in {expr!r}")
        elif isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda, ast.comprehension)):
            raise PredicateError(f"disallowed syntax in {expr!r}")
    return tree.body


def check_ok(expr: str) -> bool:
    try:
        parse_check(expr)
        return True
    except PredicateError:
        return False


def evaluate_check(expr: str, world) -> bool:
    """Evaluate a structural predicate against a world → bool."""
    return bool(_eval(parse_check(expr), world))


def evaluate(obj: Objective, world, runner=None) -> ObjectiveResult:
    """One objective → a result.

    Structural objectives evaluate against the graph instantly. Behavioral objectives need a
    live `runner` (GINI's runtime, the oracle): with one available they resolve to met/unmet via
    the probe; without one they stay `pending` (Phase 1 behavior, and whenever nothing is running).

    An objective that does not parse resolves to `defective`, never to `unmet`: the fault is the
    author's and must not be shown in the same column as the student's work."""
    def result(status):
        return ObjectiveResult(obj.id, obj.say, obj.kind, status, level_of(obj))

    if obj.is_behavioral():
        from . import probes as _probes
        if not _probes.probe_ok(obj.probe):
            # Checked BEFORE availability: a probe that cannot parse is broken whether or not
            # anything is running, and saying so straight away is what turns a machine-generated
            # typo into an authoring failure instead of a student's missing tick.
            return result(DEFECTIVE)
        if runner is None or not getattr(runner, "available", lambda: False)():
            return result(PENDING)
        try:
            met = _probes.evaluate(obj.probe, runner)
        except _probes.ProbeError:
            return result(DEFECTIVE)
        return result(MET if met else UNMET)
    try:
        met = evaluate_check(obj.check, world)
    except PredicateError:
        return result(DEFECTIVE)
    return result(MET if met else UNMET)


def evaluate_all(objectives, world, runner=None) -> list[ObjectiveResult]:
    return [evaluate(o, world, runner) for o in objectives]


# ---- the World adapter over a live Topology ------------------------------- #
class TopologyWorld:
    """Adapts a `domain.topology.Topology` to the predicate World interface. Duck-typed:
    anything exposing `.devices` (values with .name/.type_key/.parent_id/.properties) and
    `.links` (values with .source_id/.target_id) works, so tests can use a real Topology."""

    def __init__(self, topology) -> None:
        self.t = topology

    def _by_name(self, name: str):
        for d in self.t.devices.values():
            if d.name == name:
                return d
        return None

    @staticmethod
    def _match(d, spec: str) -> bool:
        """A device matches `type` or a slot-scoped `type@slot` (a scaffold/bound-dependency tag)."""
        type_key, _, slot = str(spec).partition("@")
        return d.type_key == type_key and slot_match(getattr(d, "slot", ""), slot)

    def exists(self, type_key: str) -> bool:
        return any(self._match(d, type_key) for d in self.t.devices.values())

    def count(self, type_key: str) -> int:
        return sum(1 for d in self.t.devices.values() if self._match(d, type_key))

    def _adjacency(self) -> dict:
        adj: dict[str, set] = {}
        for l in self.t.links.values():
            adj.setdefault(l.source_id, set()).add(l.target_id)
            adj.setdefault(l.target_id, set()).add(l.source_id)
        return adj

    def linked(self, a: str, b: str) -> bool:
        da, db = self._by_name(a), self._by_name(b)
        if da is None or db is None:
            return False
        return any((l.source_id, l.target_id) in ((da.id, db.id), (db.id, da.id))
                   for l in self.t.links.values())

    def connected(self, a: str, b: str) -> bool:
        """A path exists between a and b over links (undirected BFS)."""
        da, db = self._by_name(a), self._by_name(b)
        if da is None or db is None:
            return False
        if da.id == db.id:
            return True
        adj = self._adjacency()
        seen, frontier = {da.id}, [da.id]
        while frontier:
            nxt = []
            for node in frontier:
                for peer in adj.get(node, ()):
                    if peer == db.id:
                        return True
                    if peer not in seen:
                        seen.add(peer)
                        nxt.append(peer)
            frontier = nxt
        return False

    def contains(self, box: str, member: str) -> bool:
        """Is `member` inside the box named `box` (walking the parent_id chain, so nested
        VPC→Subnet→member counts)?"""
        boxdev, md = self._by_name(box), self._by_name(member)
        if boxdev is None or md is None:
            return False
        cur = md
        seen = set()
        while cur is not None and cur.parent_id and cur.id not in seen:
            seen.add(cur.id)
            parent = self.t.devices.get(cur.parent_id)
            if parent is not None and parent.id == boxdev.id:
                return True
            cur = parent
        return False

    def prop(self, dev: str, key: str):
        d = self._by_name(dev)
        if d is None:
            return None
        return self._prop_of(d, key)

    @staticmethod
    def _prop_of(d, key: str):
        props = getattr(d, "properties", {}) or {}
        if key in props:
            return props[key]
        low = {k.lower(): v for k, v in props.items()}      # forgiving on case
        return low.get(key.lower())

    def prop_type(self, type_key: str, key: str, want=None) -> bool:
        """Does SOME device of this type carry `key` (equal to `want`, when given)?

        The type-based counterpart to `prop`. A plan written before the student builds anything
        cannot know that their router is called R2, so a *name*-based property check silently
        matches nothing and reads as "they didn't configure it". This is the same
        name-agnostic reasoning behind `link_types`/`path_types`, applied to configuration:
        `property_type('router', 'delay')` asks whether any router has delay set at all, and
        `property_type('switch', 'mode', 'hub')` pins the value.

        Existential, like `exists` — "some router has delay" is what an activity means when it
        says the student configured delay somewhere on the path. Use a `count`-style universal
        only if a future activity genuinely needs every one of them.

        Truthiness is deliberate: an unset property reads as absent, so `property_type(x, 'k')`
        answers "is it configured", not "does the key appear in the dict".
        """
        for d in self.t.devices.values():
            if not self._match(d, type_key):
                continue
            got = self._prop_of(d, key)
            if want is None:
                if got not in (None, "", False):
                    return True
            elif str(got) == str(want):
                return True
        return False

    # -- type-based (name-agnostic) predicates: match what the student built, not the names -- #
    def link_types(self, type_a: str, type_b: str) -> bool:
        """A link directly connecting some device matching `type_a` to some matching `type_b`
        (each may be slot-scoped as `type@slot`)."""
        for l in self.t.links.values():
            s = self.t.devices.get(l.source_id)
            d = self.t.devices.get(l.target_id)
            if s is None or d is None:
                continue
            if ((self._match(s, type_a) and self._match(d, type_b))
                    or (self._match(s, type_b) and self._match(d, type_a))):
                return True
        return False

    def all_linked_types(self, type_a: str, type_b: str) -> bool:
        """EVERY device matching `type_a` has a direct link to SOME device matching `type_b` — the
        universal that makes an open-N win condition N-independent ("every LAN is wired to the
        router", any number of LANs). Vacuously true with no type_a devices; a `count(...)>=K` floor
        guards emptiness separately. Each arg may be slot-scoped as `type@slot`."""
        a_devs = [d for d in self.t.devices.values() if self._match(d, type_a)]
        b_ids = {d.id for d in self.t.devices.values() if self._match(d, type_b)}
        if not a_devs or not b_ids:
            return not a_devs                    # no A → vacuously true; A but no B → false
        adj = self._adjacency()
        return all(any(peer in b_ids for peer in adj.get(da.id, ())) for da in a_devs)

    def path_types(self, type_a: str, type_b: str) -> bool:
        """Is there a path (over links) between some device of type_a and some device of type_b?"""
        dsts = {d.id for d in self.t.devices.values() if self._match(d, type_b)}
        if not dsts:
            return False
        adj = self._adjacency()
        for src in [d.id for d in self.t.devices.values() if self._match(d, type_a)]:
            seen, frontier = {src}, [src]
            while frontier:
                nxt = []
                for node in frontier:
                    for peer in adj.get(node, ()):
                        if peer in dsts and peer != src:
                            return True
                        if peer not in seen:
                            seen.add(peer)
                            nxt.append(peer)
                frontier = nxt
        return False

    def through_types(self, gate_type: str, src_type: str, dst_type: str) -> bool:
        """Chokepoint: some src-type reaches some dst-type, and EVERY such path crosses a device of
        `gate_type` — i.e. removing all gate-type devices disconnects the src tier from the dst tier.
        This is the correct "traffic passes THROUGH the gate" semantic (rejects a parallel bypass),
        as opposed to `path` (any route counts) or `link` (a direct cable)."""
        if not self.path_types(src_type, dst_type):
            return False                      # no traffic flows at all → not "through" anything
        gates = {d.id for d in self.t.devices.values() if d.type_key == gate_type}
        if not gates:
            return False                      # the gate isn't even present
        dsts = {d.id for d in self.t.devices.values()
                if d.type_key == dst_type and d.id not in gates}
        srcs = [d.id for d in self.t.devices.values()
                if d.type_key == src_type and d.id not in gates]
        if not dsts or not srcs:
            return True                       # removing gates erases a tier → the gate was on the path
        adj = self._adjacency()
        for src in srcs:                      # can any src reach a dst WITHOUT crossing a gate?
            seen, frontier = {src}, [src]
            while frontier:
                nxt = []
                for node in frontier:
                    for peer in adj.get(node, ()):
                        if peer in gates:
                            continue          # never route through a gate node
                        if peer in dsts and peer != src:
                            return False      # a gate-free path exists → NOT a chokepoint
                        if peer not in seen:
                            seen.add(peer)
                            nxt.append(peer)
                frontier = nxt
        return True

    def contains_types(self, box_type: str, member_type: str) -> bool:
        """Is some device of member_type inside a box of box_type (walking the parent chain)?"""
        for d in self.t.devices.values():
            if d.type_key != member_type:
                continue
            cur, seen = d, set()
            while cur is not None and getattr(cur, "parent_id", None) and cur.id not in seen:
                seen.add(cur.id)
                parent = self.t.devices.get(cur.parent_id)
                if parent is not None and parent.type_key == box_type:
                    return True
                cur = parent
        return False


def element_types_in_check(expr: str) -> list[str]:
    """The type_keys referenced by exists()/count() in a check — for lesson validation."""
    out: list[str] = []
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _TYPE_ARG_FUNCS):
            n = _TYPE_ARITY.get(node.func.id, len(node.args))
            for a in node.args[:n]:
                if isinstance(a, ast.Name):
                    out.append(a.id)
                elif isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.append(a.value)
    return out


def unknown_element_types(expr: str) -> list[str]:
    """type_keys in exists()/count() that aren't real GINI elements (validation)."""
    return [t for t in element_types_in_check(expr) if t not in REGISTRY]
