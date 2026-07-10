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


@dataclass
class Objective:
    id: str
    say: str                 # student-facing one-liner
    kind: str = "structural"  # structural | behavioral
    check: str = ""          # structural predicate expression
    probe: str = ""          # behavioral probe (opaque in Phase 1)

    def is_behavioral(self) -> bool:
        return self.kind == "behavioral"


@dataclass
class ObjectiveResult:
    id: str
    say: str
    kind: str
    status: str              # met | unmet | pending

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
               "link", "path", "contains_type", "through"}
_TYPE_ARG_FUNCS = {"exists", "count", "link", "path", "contains_type", "through"}  # args are type_keys


class PredicateError(ValueError):
    """A structural check that doesn't parse or uses an unknown function."""


def _arg(node) -> object:
    """A predicate argument is a bare identifier (element name/type) or a literal."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
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
    if fn == "link":                    # a link between ANY typeA device and ANY typeB device
        return world.link_types(str(args[0]), str(args[1]))
    if fn == "path":                    # a path between some typeA and some typeB device
        return world.path_types(str(args[0]), str(args[1]))
    if fn == "contains_type":           # a memberType device inside a boxType box
        return world.contains_types(str(args[0]), str(args[1]))
    if fn == "through":                 # every src->dst path crosses a gate (a chokepoint)
        return world.through_types(str(args[0]), str(args[1]), str(args[2]))
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
    the probe; without one they stay `pending` (Phase 1 behavior, and whenever nothing is running)."""
    if obj.is_behavioral():
        if runner is None or not getattr(runner, "available", lambda: False)():
            return ObjectiveResult(obj.id, obj.say, obj.kind, PENDING)
        from . import probes as _probes
        try:
            met = _probes.evaluate(obj.probe, runner)
        except _probes.ProbeError:
            met = False
        return ObjectiveResult(obj.id, obj.say, obj.kind, MET if met else UNMET)
    try:
        met = evaluate_check(obj.check, world)
    except PredicateError:
        met = False
    return ObjectiveResult(obj.id, obj.say, obj.kind, MET if met else UNMET)


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

    def exists(self, type_key: str) -> bool:
        return any(d.type_key == type_key for d in self.t.devices.values())

    def count(self, type_key: str) -> int:
        return sum(1 for d in self.t.devices.values() if d.type_key == type_key)

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
        props = getattr(d, "properties", {}) or {}
        if key in props:
            return props[key]
        low = {k.lower(): v for k, v in props.items()}      # forgiving on case
        return low.get(key.lower())

    # -- type-based (name-agnostic) predicates: match what the student built, not the names -- #
    def link_types(self, type_a: str, type_b: str) -> bool:
        """Is there a link directly connecting some device of type_a to some device of type_b?"""
        for l in self.t.links.values():
            s = self.t.devices.get(l.source_id)
            d = self.t.devices.get(l.target_id)
            if s is None or d is None:
                continue
            st, dt = s.type_key, d.type_key
            if (st == type_a and dt == type_b) or (st == type_b and dt == type_a):
                return True
        return False

    def path_types(self, type_a: str, type_b: str) -> bool:
        """Is there a path (over links) between some device of type_a and some device of type_b?"""
        dsts = {d.id for d in self.t.devices.values() if d.type_key == type_b}
        if not dsts:
            return False
        adj = self._adjacency()
        for src in [d.id for d in self.t.devices.values() if d.type_key == type_a]:
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
            for a in node.args:                 # every arg of a type-based predicate is a type_key
                if isinstance(a, ast.Name):
                    out.append(a.id)
                elif isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.append(a.value)
    return out


def unknown_element_types(expr: str) -> list[str]:
    """type_keys in exists()/count() that aren't real GINI elements (validation)."""
    return [t for t in element_types_in_check(expr) if t not in REGISTRY]
