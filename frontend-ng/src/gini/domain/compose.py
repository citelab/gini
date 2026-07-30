"""Slot-aware composition — the deterministic assembler that scales primitives into larger topologies.

This is Phase 3: given a dependent fragment (e.g. a hub router) whose slots are *cardinal* (a slot
that binds to N distinct providers, `min..max`), plus a BINDING that says which certified provider
fills each slot, the assembler MATERIALIZES one concrete topology and the EXPANDED objective set that
grades it — then the oracle runs it. No LLM here: composition is a derivation, graded by the runtime.

How it scales without new grammar:
  * each cardinal slot expands to N distinct LABELS (`lans0, lans1, …`);
  * a provider is materialized per label from its faithful certified board (its saved `stage`), and
    every device it contributes is tagged with that label — so `switch@lans2` means "the switch of
    LAN #2", reusing the exact `type@slot` oracle Phase 1 already proved;
  * the dependent fragment's own delta (the router) is placed once, unlabelled;
  * a `@<cardinal>` predicate on the dependent fragment expands to one objective PER label
    (forall — e.g. the router wires to every LAN), and a same-slot behavioral `reach(host@s -> host@s)`
    expands to cross-member pairs (forall-pairs — every LAN reaches every other).

A binding:
    {"fragment": "hub-router", "bind": {"lans": ["cap-lan", "cap-lan", "cap-lan", "cap-lan"]}}
A provider entry may itself be a nested binding `{"fragment": ..., "bind": {...}}` for recursion.
"""
from __future__ import annotations

import ast
import re

from . import devices as _devices
from . import fragments as _frag
from .objectives import Objective, _TYPE_ARG_FUNCS
from .topology import Topology

_SLOT_RE = re.compile(r"@(\w+)")
MAX_DEPTH = 6                       # recursion budget — a composition can't nest forever
ROOT = "root"                      # label of the top fragment's own delta (never a member prefix)


class CompositionError(ValueError):
    pass


# ---- predicate rewriting (structural checks are python-parseable) ---------- #
def _suffix_plain(expr: str, label: str) -> str:
    """Scope every PLAIN type-arg to `@label` (a bare `host` becomes `host@label`). Args already
    slot-scoped are left alone. Used when a provider's own objectives are placed under a label."""
    if not expr or not label:
        return expr
    tree = ast.parse(expr, mode="eval")

    class T(ast.NodeTransformer):
        def visit_Call(self, node):                       # noqa: N802
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id in _TYPE_ARG_FUNCS:
                node.args = [
                    ast.BinOp(a, ast.MatMult(), ast.Name(label, ast.Load()))
                    if isinstance(a, ast.Name) else a
                    for a in node.args]
            return node

    return ast.unparse(T().visit(tree).body)


def _rebind(expr: str, slot: str, label: str) -> str:
    """Rebind a specific cardinal reference: `switch@lans` -> `switch@lans2`."""
    if not expr:
        return expr
    tree = ast.parse(expr, mode="eval")

    class T(ast.NodeTransformer):
        def visit_BinOp(self, node):                      # noqa: N802
            self.generic_visit(node)
            if (isinstance(node.op, ast.MatMult) and isinstance(node.right, ast.Name)
                    and node.right.id == slot):
                node.right = ast.Name(label, ast.Load())
            return node

    return ast.unparse(T().visit(tree).body)


def _slots_in(text: str) -> set[str]:
    return set(_SLOT_RE.findall(text or ""))


def _placements(check: str) -> list[tuple[str, int]]:
    """(type, count) for a PLAIN `exists`/`count` placement — for building the delta's devices.
    Objectives are single predicates, so `count(T) >= n` yields n and `exists(T)` yields 1."""
    try:
        top = ast.parse(check or "", mode="eval").body
    except SyntaxError:
        return []
    if (isinstance(top, ast.Compare) and isinstance(top.left, ast.Call)
            and isinstance(top.left.func, ast.Name) and top.left.func.id == "count"
            and top.left.args and isinstance(top.left.args[0], ast.Name)
            and isinstance(top.comparators[0], ast.Constant)):
        return [(top.left.args[0].id, int(top.comparators[0].value))]
    out: list[tuple[str, int]] = []
    for node in ast.walk(top):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "exists" and node.args and isinstance(node.args[0], ast.Name)):
            out.append((node.args[0].id, 1))
    return out


# ---- materialization ------------------------------------------------------- #
def _is_rider(type_key: str) -> bool:
    dt = _devices.get(type_key)
    return bool(dt and getattr(dt, "rider", False))


def _instantiate_stage(topo: Topology, stage: dict, label: str) -> None:
    """Rebuild a provider's saved board into `topo`, tagging every (non-rider) device with `label`.
    Reproduces the exact certified structure (all hosts wired to the switch, etc.)."""
    idmap: dict[str, str] = {}
    for d in stage.get("devices", []) or []:
        if _is_rider(d.get("type_key", "")):
            continue                                      # ports, not structure
        inst = topo.add_device(d["type_key"])
        inst.slot = label
        idmap[d["id"]] = inst.id
    for l in stage.get("links", []) or []:
        if l.get("kind") == "attach":
            continue
        s, t = idmap.get(l.get("source_id")), idmap.get(l.get("target_id"))
        if s and t:
            topo.add_link(s, t)


def _link_exists(topo: Topology, a_id: str, b_id: str) -> bool:
    return any({l.source_id, l.target_id} == {a_id, b_id} for l in topo.links.values())


def _connect_via(F) -> str:
    """The device type external consumers link to — a provider's connection point. Explicit
    `connect_via` wins; else inferred: a composite's is the source of its own slot-links (its
    gateway, e.g. `router`); a leaf's is the structural hub of its board (first non-host device,
    e.g. `switch`). This is what lets a `network` slot bind a LAN (→switch) OR a routed net (→router)."""
    cv = getattr(F, "connect_via", "")
    if cv:
        return cv
    for t in F.objectives:                                # composite → its gateway
        m = re.match(r"^\s*link\((\w+)\s*,\s*\w+@\w+\)\s*$", t.check or "")
        if m:
            return m.group(1)
    for d in (getattr(F, "stage", None) or {}).get("devices", []) or []:   # leaf → structural hub
        tk = d.get("type_key", "")
        if tk and tk != "host" and not _is_rider(tk):
            return tk
    return ""


def _conn_point(topo: Topology, type_key: str, member_path: str):
    """A member's own top device of `type_key` — the shallowest one under the member's path (its
    delta / hub), never a device buried deeper in a nested sub-composition."""
    from .objectives import slot_match
    cands = [d for d in topo.devices.values()
             if d.type_key == type_key and slot_match(d.slot, member_path)]
    return min(cands, key=lambda d: d.slot.count("_"), default=None)


def _build(binding: dict, topo: Topology, out: list[Objective], path: str,
           is_provider: bool, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise CompositionError(f"composition nests deeper than {MAX_DEPTH} levels")
    F = _frag.get(binding.get("fragment", ""))
    if F is None:
        raise CompositionError(f"unknown fragment: {binding.get('fragment')!r}")

    # A LEAF provider (a certified board, no slots) is reproduced faithfully, every device tagged
    # with this member's path.
    if is_provider and getattr(F, "stage", None) and F.stage.get("devices") and not F.slots:
        _instantiate_stage(topo, F.stage, path)
        for t in F.objectives:
            # A provider's OWN live/output checks (reach, measure on its riders) are proven by its
            # certificate — we trust it, we don't re-run them here. (The composer strips riders, so a
            # `measure(packet_view…)` couldn't pass anyway.) Only its STRUCTURAL shape is re-checked.
            if t.kind == "behavioral":
                continue
            out.append(Objective(id=f"{path}-{t.id}", say=t.say, kind=t.kind,
                                 check=_suffix_plain(t.check, path), probe="",
                                 level=t.level, stars=t.stars))
        return

    # Otherwise DERIVE (the top fragment, or a composite provider). Its own delta is tagged
    # `path_self` — a leaf segment no member shares, so a `type@path_self` scope never bleeds into
    # the sub-networks it contains (crucial when delta and members share a type, e.g. router↔router).
    own = f"{path}_self"
    delta: dict[str, list[str]] = {}
    for t in F.objectives:
        if _slots_in(t.check) or _slots_in(t.probe):
            continue
        for typ, n in _placements(t.check):
            for _ in range(n):
                d = topo.add_device(typ)
                d.slot = own
                delta.setdefault(typ, []).append(d.id)

    slotmap: dict[str, list[tuple[str, str]]] = {}        # slot -> [(member_path, connect_via_type)]
    for S in F.slots:
        provs = (binding.get("bind") or {}).get(S.name) or []
        if len(provs) < S.min or (S.max and S.max > 0 and len(provs) > S.max):
            raise CompositionError(
                f"slot {S.name!r} takes {S.min}..{S.max or '∞'} providers, got {len(provs)}")
        members: list[tuple[str, str]] = []
        for i, pv in enumerate(provs):
            mp = f"{path}_{S.name}{i}"
            child = pv if isinstance(pv, dict) else {"fragment": pv}
            _build(child, topo, out, mp, is_provider=True, depth=depth + 1)
            members.append((mp, _connect_via(_frag.get(child["fragment"]))))
        slotmap[S.name] = members

    # LATERAL peer groups (Phase 5): members interconnect as peers per a topology (mesh/ring/…),
    # rather than each linking back to the delta. Meshes/graphs of routers live here.
    for PG in getattr(F, "peerings", ()):
        provs = (binding.get("peer") or {}).get(PG.name) or []
        if len(provs) < PG.min or (PG.max and PG.max > 0 and len(provs) > PG.max):
            raise CompositionError(
                f"peering {PG.name!r} takes {PG.min}..{PG.max or '∞'} peers, got {len(provs)}")
        members = []
        for i, pv in enumerate(provs):
            mp = f"{path}_{PG.name}{i}"
            child = pv if isinstance(pv, dict) else {"fragment": pv}
            _build(child, topo, out, mp, is_provider=True, depth=depth + 1)
            members.append((mp, _connect_via(_frag.get(child["fragment"]))))
        _wire_peering(topo, out, members, PG.topology, PG.name)
        slotmap[PG.name] = members                        # @name reach objectives expand all-pairs

    for t in F.objectives:
        refs = _slots_in(t.check) | _slots_in(t.probe)
        if is_provider and t.kind == "behavioral":
            continue                                      # a bound provider's live checks: trusted
        if not (refs & set(slotmap)):
            out.append(Objective(id=f"{path}-{t.id}", say=t.say, kind=t.kind,
                                 check=_suffix_plain(t.check, own),
                                 probe=_suffix_plain(t.probe, own), level=t.level, stars=t.stars))
            _wire_plain_link(topo, t.check, delta, own)
            continue
        slot = next(iter(refs & set(slotmap)))
        if t.kind == "behavioral":
            out.extend(_expand_behavioral(t, slot, [mp for mp, _ in slotmap[slot]]))
        else:
            out.extend(_expand_structural(t, slot, slotmap[slot], topo, delta, own))


def _wire_plain_link(topo: Topology, check: str, delta: dict, own: str) -> None:
    """`link(a, b)` within a node's own delta → wire the a's to the first b (star for repeats)."""
    m = re.match(r"^\s*link\((\w+)\s*,\s*(\w+)\)\s*$", check or "")
    if not m:
        return
    a_ids = delta.get(m.group(1), [])
    b = _conn_point(topo, m.group(2), own) or topo.devices.get((delta.get(m.group(2)) or [None])[0])
    if not a_ids or b is None:
        return
    for aid in a_ids:
        if aid != b.id and not _link_exists(topo, aid, b.id):
            topo.add_link(aid, b.id)


def _expand_structural(t, slot: str, members: list[tuple[str, str]], topo: Topology,
                       delta: dict, own: str) -> list[Objective]:
    """forall members: one objective per member. A `link(a, b@slot)` wires this node's delta `a` to
    each member's connection point (its `connect_via` type — a LAN's switch, a routed net's router)."""
    objs: list[Objective] = []
    link_m = re.match(r"^\s*link\((\w+)\s*,\s*(\w+)@" + re.escape(slot) + r"\)\s*$", t.check or "")
    for mp, cv in members:
        if link_m:
            a, attach = link_m.group(1), (cv or link_m.group(2))
            objs.append(Objective(id=f"{t.id}-{mp}", say=f"{t.say} [{mp}]", kind=t.kind,
                                 check=f"link({a}@{own}, {attach}@{mp})", level=t.level, stars=t.stars))
            adev = topo.devices.get((delta.get(a) or [None])[0])
            bdev = _conn_point(topo, attach, mp)
            if adev is not None and bdev is not None and not _link_exists(topo, adev.id, bdev.id):
                topo.add_link(adev.id, bdev.id)
        else:
            objs.append(Objective(id=f"{t.id}-{mp}", say=f"{t.say} [{mp}]", kind=t.kind,
                                 check=_rebind(_suffix_plain(t.check, own), slot, mp),
                                 level=t.level, stars=t.stars))
    return objs


def _topology_pairs(topology: str, n: int) -> list[tuple[int, int]]:
    """Which member index pairs a peering interconnects, by shape."""
    if n < 2:
        return []
    topo = (topology or "mesh").lower()
    if topo == "ring":
        return [(0, 1)] if n == 2 else [(i, (i + 1) % n) for i in range(n)]
    if topo == "line":
        return [(i, i + 1) for i in range(n - 1)]
    if topo == "star":
        return [(0, i) for i in range(1, n)]                # member 0 is the hub
    return [(i, j) for i in range(n) for j in range(i + 1, n)]   # mesh: every pair


def _wire_peering(topo: Topology, out: list[Objective], members: list[tuple[str, str]],
                  topology: str, name: str) -> None:
    """Interconnect peer members per `topology`, linking each pair's connection points, and emit a
    structural objective per peered pair so the graph is gradable."""
    for i, j in _topology_pairs(topology, len(members)):
        (mpi, cvi), (mpj, cvj) = members[i], members[j]
        a, b = _conn_point(topo, cvi, mpi), _conn_point(topo, cvj, mpj)
        if a is not None and b is not None and not _link_exists(topo, a.id, b.id):
            topo.add_link(a.id, b.id)
        out.append(Objective(id=f"peer-{name}-{i}-{j}", say=f"{name}: peers {i}↔{j} interconnected",
                             kind="structural", check=f"link({cvi}@{mpi}, {cvj}@{mpj})", level=2))


def _expand_behavioral(t, slot: str, member_paths: list[str]) -> list[Objective]:
    """forall-pairs: a same-slot `reach(x@s -> y@s)` becomes one probe per distinct member pair."""
    objs: list[Objective] = []
    for i in range(len(member_paths)):
        for j in range(i + 1, len(member_paths)):
            probe = _rebind_probe(t.probe, slot, member_paths[i], member_paths[j])
            objs.append(Objective(id=f"{t.id}-{member_paths[i]}-{member_paths[j]}",
                                 say=f"{t.say} [{member_paths[i]}↔{member_paths[j]}]", kind="behavioral",
                                 check="", probe=probe, level=t.level, stars=t.stars))
    return objs


def _rebind_probe(probe: str, slot: str, la: str, lb: str) -> str:
    """Rebind the two same-slot operands of a probe to a specific member pair: the first `@slot`
    occurrence becomes `@la`, the second `@lb` (a probe has src then dst)."""
    parts = probe.split(f"@{slot}")                       # template carries the bare slot name twice
    if len(parts) == 3:
        return parts[0] + f"@{la}" + parts[1] + f"@{lb}" + parts[2]
    return probe.replace(f"@{slot}", f"@{la}")            # single-operand fallback


def _leaf_type(F) -> str:
    """A provider's 'leaf' element — the endpoints its hub connects (a LAN's `host`). Inferred as a
    non-hub, non-rider device type in its board."""
    hub = _connect_via(F)
    for d in (getattr(F, "stage", None) or {}).get("devices", []) or []:
        tk = d.get("type_key", "")
        if tk and tk != hub and not _is_rider(tk):
            return tk
    return ""


def _open_objectives(binding: dict) -> list[Objective]:
    """The OPEN-N win condition — quantified and N-INDEPENDENT, so a student may build ANY number of
    members ≥ the slot's floor and still pass. Instead of expanding to concrete `@lans2` labels we
    emit: `count(hub) >= K` (the floor), `all_linked(hub, delta)` / `all_linked(leaf, hub)` (every
    member wired), and `reach(x -> y, all)` (every pair reachable). This is the generalizable pattern
    the AI composer plans against; the concrete board materialized alongside is just a sample."""
    F = _frag.get(binding.get("fragment", ""))
    if F is None:
        raise CompositionError(f"unknown fragment: {binding.get('fragment')!r}")
    objs: list[Objective] = []
    for t in F.objectives:                                # delta placements stay as-is (exists router)
        if not (_slots_in(t.check) or _slots_in(t.probe)):
            objs.append(Objective(id=t.id, say=t.say, kind=t.kind, check=t.check, probe=t.probe,
                                 level=t.level, stars=t.stars))

    groups = [(S.name, S.min, (binding.get("bind") or {}).get(S.name)) for S in F.slots]
    groups += [(P.name, P.min, (binding.get("peer") or {}).get(P.name)) for P in getattr(F, "peerings", ())]
    for name, k, provs in groups:
        if not provs:
            continue
        first = provs[0]
        pf = _frag.get(first["fragment"] if isinstance(first, dict) else first)
        if pf is None:
            continue
        hub = _connect_via(pf)
        # A composite member (a routed-network with its own LANs) has its internal structure proven
        # by its certificate — we don't re-assert "leaf on hub" for it. Only a LEAF provider (a LAN:
        # hosts directly on a switch) gets the `all_linked(host, switch)` check.
        composite = bool(getattr(pf, "slots", ()) or getattr(pf, "peerings", ()))
        leaf = "" if composite else _leaf_type(pf)
        objs.append(Objective(id=f"open-count-{name}", say=f"at least {k} {hub}(s) in {name}",
                             kind="structural", check=f"count({hub}) >= {k}", level=1))
        for t in F.objectives:                            # every member wired to the delta it links
            m = re.match(r"^\s*link\((\w+)\s*,\s*(\w+)@" + re.escape(name) + r"\)\s*$", t.check or "")
            if m:
                objs.append(Objective(id=f"open-wire-{name}", say=f"every {hub} wired to the {m.group(1)}",
                                     kind="structural", check=f"all_linked({hub}, {m.group(1)})",
                                     level=2))
        if leaf and leaf != hub:
            objs.append(Objective(id=f"open-leaf-{name}", say=f"every {leaf} on a {hub}",
                                 kind="structural", check=f"all_linked({leaf}, {hub})", level=2))
        for t in F.objectives:                            # cross-member reach → every pair (all)
            if t.kind == "behavioral" and name in _slots_in(t.probe):
                probe = re.sub(r"@\w+", "", t.probe)      # strip slot scopes
                probe = re.sub(r"\)\s*==", ", all) ==", probe, count=1)
                objs.append(Objective(id=f"open-reach-{name}", say=t.say + " (all pairs, any N)",
                                     kind="behavioral", probe=probe, level=4))
    return objs


def materialize(binding: dict, mode: str = "fixed") -> tuple[Topology, list[Objective]]:
    """Assemble the concrete topology + objective ladder. `mode='fixed'` bakes this exact N (a
    specific lab); `mode='open'` grades a quantified, N-independent pattern (student picks N ≥ floor).
    Either way a concrete board is materialized — in open mode it's a representative sample."""
    topo = Topology()
    out: list[Objective] = []
    _build(binding, topo, out, path=ROOT, is_provider=False)
    from .objectives import by_level
    if mode == "open":
        out = _open_objectives(binding)
    return topo, by_level(out)


def grade(binding: dict, runner=None, mode: str = "fixed"):
    """Materialize + grade a composition against the oracle. `runner` supplies behavioral verdicts
    (a live DockerProbeRunner on the Mac, a FakeRunner headless). `mode` picks fixed-N or open-N
    grading. Returns (topology, results)."""
    from . import objectives as _obj
    from . import probes as _probes
    topo, objs = materialize(binding, mode=mode)
    r = _probes.TypeRunner(runner, lambda: topo) if runner is not None else None
    return topo, _obj.evaluate_all(objs, _obj.TopologyWorld(topo), r)
