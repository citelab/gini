"""T-Lab 03's engine: deadlock handling as requests, grants, releases and recovery, one model.

T-Lab 02 already makes students FIND deadlocks in code by picking the schedule. What it cannot
show is the other half of Chapter 14 of the notes: what the operating system does about deadlock
— the resource-allocation graph, graph reduction, the claim-edge algorithm, the banker's
algorithm, the detector, and recovery by termination, rollback or preemption. Those are not
lines of a thread, so this lab has an engine of its own beside `schedlab`; what the two share is
the frame (levels, starts, goals, a Try every) and nothing else. `tlab_catalog` is the one place
that knows a level file with `engine: bank` is read here.

It is a port of the reference engine the professor approved with the design mockup
(`docs/design/t-lab-03-reference/banklab.py`), and the port is checked against that reference by
`tests/test_banklab_golden.py`: the reference's seventeen checks, each one a worked example or a
figure of the chapter, plus the shipped level file read against the reference's. Where the two
disagree, the reference is what the professor saw; fix the port. What the port adds is only what
shipping needs: a `LabFile` for the hub (the reference returned a bare list of levels), one-
sentence refusals for the mistakes a level file can make, and `count_orders`, so the chain's
Try-every-order entry can say how many orders reach each outcome.

Pure, like the rest of `gini.domain`: no Qt, no I/O except reading a level file, no randomness
("Random" lives in the face). The Teaching Center installs gini-core too, so a recorded order of
moves is a certificate exactly because `replay` here has no Qt in it.

**Two kinds of level, one model.** Every level is processes, resource types with units, and for
each process what it holds, what it is waiting for (`pend`), and, where the level declares claims,
the most it may ever hold (Max). What differs is who decides:

* **run** levels (1, 2, 4, 5): the student picks which process takes its next step — the next line
  of its script (`{ask: [...]}` or `finish`), or, where `early_finish`, deciding it needs no more —
  and a **policy** decides each request: `free` (grant whenever the units are free), `graph` (the
  claim-edge algorithm of §14.6.2; single-unit resources only, and the loader enforces it), or
  `banker` (§14.6.3; in the engine, not used by a shipped level). Where a start declares
  `recovery`, the three ways out of §14.7 are moves too. `explore` is Try every order.
* **bank** levels (3): the student IS the banker. A request is picked from a list, the three tests
  of "a request arrives" are applied (`tests`), and the student runs the safety check by hand,
  choosing which process finishes next (`check_pick`). Each row is written the way the notes'
  tables are (try · Need ≤ Work? · result), "no" rows included.

**A start may bring its own world.** Level 3 opens on a one-resource bank and level 5 has a
three-resource detector beside two four-resource ones, so `world(level, start)` returns the level
as that start sees it, and every function below takes the level `world` returns.

Vectors are tuples of ints, one entry per resource type, compared element by element; `vshow`
writes them as the notes do — (1, 2, 2), or a bare number when there is one resource type.
"""
from __future__ import annotations

import heapq
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

#: Reachable states Try every order visits before giving up. The shipped levels reach at most a
#: few hundred; the cap exists so a later level that explodes says so instead of hanging the face.
EXPLORE_CAP = 20000

#: Recovery actions a start may offer (§14.7).
RECOVERY = ("terminate", "rollback", "preempt")

POLICIES = ("free", "graph", "banker")


# --------------------------------------------------------------------------- #
# vectors
# --------------------------------------------------------------------------- #
def vle(a, b) -> bool:
    return all(x <= y for x, y in zip(a, b))


def vadd(a, b) -> tuple:
    return tuple(x + y for x, y in zip(a, b))


def vsub(a, b) -> tuple:
    return tuple(x - y for x, y in zip(a, b))


def vzero(m: int) -> tuple:
    return (0,) * m


def vshow(v) -> str:
    """One type: a bare number, as the notes write single-resource examples. Several: (1, 2, 2)."""
    return str(v[0]) if len(v) == 1 else "(" + ", ".join(str(x) for x in v) + ")"


# --------------------------------------------------------------------------- #
# the level file
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Start:
    label: str
    hold: tuple                    # per process, a vector
    pending: tuple                 # per process, a vector or None (already waiting at the start)
    scripts: tuple = ()            # per process, steps: ("ask", vec) | ("finish",)  (run levels)
    requests: tuple = ()           # (process index, vec) to decide from this state (bank levels)
    note: str = ""
    world: dict = field(default_factory=dict)   # overrides: resources, units, procs, claims, cpu
    hide_need: bool = False        # bank levels: Need and Avail hidden, to be worked out first


@dataclass(frozen=True)
class Version:
    id: str
    label: str
    policy: str                    # "free" | "graph" | "banker"
    proves: str = ""


@dataclass(frozen=True)
class Goal:
    id: str
    chip: str
    text: str
    start: str                     # start label the goal must be reached from
    version: str = ""              # run levels: the version it must be reached on
    kind: str = ""                 # see GOAL_KINDS


GOAL_KINDS = {
    # run levels, checked after every step
    "deadlock",            # some process is deadlocked (reduction leaves it)
    "cycle_no_deadlock",   # the graph has a cycle, and reduction erases every process
    "refused_free",        # a process waits although the units it asked for are free
    "all_done",            # every process has finished
    "cheapest_victim",     # one termination broke the deadlock, and it was the cheapest that does
    "cheapest_recovery",   # terminations, rollbacks and preemptions broke it, at the lowest cost
    # run levels, by Try every order
    "prove",
    # bank levels
    "safe_sequence",       # a complete safety check of the state as it is, by hand
    "grant",               # a safe request, granted after a complete check
    "wait_unsafe",         # an unsafe request, made to wait after a check that got stuck
}

BANK_KINDS = ("safe_sequence", "grant", "wait_unsafe")


@dataclass(frozen=True)
class Level:
    id: str
    n: int
    title: str
    mode: str                      # "run" | "bank"
    resources: tuple               # names
    units: tuple                   # total units per resource type
    procs: tuple                   # names
    starts: tuple                  # Start
    versions: tuple = ()           # Version (run levels)
    claims: tuple | None = None    # per process: the declared maximum (Max), or None
    cpu: tuple = ()                # per process: minutes of CPU used so far (detector starts)
    early_finish: bool = False     # a running process may decide it needs no more and finish
    recovery: tuple = ()           # recovery offered: "terminate", "rollback", "preempt" (§14.7)
    preemptable: tuple = ()        # per resource: its state can be saved and restored
    took: tuple = ()               # per process, per resource: the minute it took them, or None
    checkpoint: tuple = ()         # per process: the minute of its last checkpoint, or None
    source: str = ""
    setup: str = ""
    asks: str = ""
    layout: dict = field(default_factory=dict)   # node -> (x, y) in 0..1, for the graph
    goals: tuple = ()

    @property
    def m(self) -> int:
        return len(self.resources)

    def start(self, label: str) -> int:
        return next(i for i, s in enumerate(self.starts) if s.label == label)

    def version(self, vid: str) -> Version:
        return next(v for v in self.versions if v.id == vid)

    def goal(self, gid: str) -> Goal | None:
        return next((g for g in self.goals if g.id == gid), None)


@dataclass(frozen=True)
class LabFile:
    """One T-Lab: what the hub needs to draw a tile (`tlab_catalog`'s contract), and its levels.

    The reference engine's loader returned the levels as a list; the hub also needs the lab's id,
    title, summary and whether it is released, so they are kept here beside the levels."""
    id: str
    title: str
    levels: tuple = ()
    summary: str = ""
    hue: str = ""
    released: bool = True
    engine: str = "bank"

    def level(self, level_id: str) -> Level | None:
        return next((lv for lv in self.levels if lv.id == level_id), None)


class LevelError(ValueError):
    """A level file that would not run. The message is one sentence naming what is wrong."""


def _refuse(cond: bool, sentence: str) -> None:
    if cond:
        raise LevelError(sentence)


def _vec(raw, m: int, where: str) -> tuple:
    if isinstance(raw, int):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or len(raw) != m:
        raise LevelError(f"{where}: expected {m} numbers, got {raw!r}")
    return tuple(int(x) for x in raw)


def _world(raw_res, raw_procs, where: str) -> dict:
    _refuse(not raw_res, f"{where} declares no resources")
    _refuse(not raw_procs, f"{where} declares no processes")
    for r in raw_res:
        _refuse(not isinstance(r, dict) or not r.get("name") or "units" not in r,
                f"{where}: every resource needs a name and a number of units")
    for p in raw_procs:
        _refuse(not isinstance(p, dict) or not p.get("name"),
                f"{where}: every process needs a name")
    res = tuple(str(r["name"]) for r in raw_res)
    units = tuple(int(r["units"]) for r in raw_res)
    m = len(res)
    procs = tuple(str(p["name"]) for p in raw_procs)
    _refuse(len(set(procs)) != len(procs) or len(set(res)) != len(res),
            f"{where} uses the same name twice")
    claims = None
    if any("max" in p for p in raw_procs):
        claims = tuple(_vec(p.get("max", [0] * m), m, f"{where}: max of {p['name']}")
                       for p in raw_procs)
    cpu = tuple(p.get("cpu", 0) for p in raw_procs)
    pre = tuple(bool(r.get("preemptable")) for r in raw_res)
    for p in raw_procs:
        for rn in (p.get("took") or {}):
            _refuse(rn not in res, f"{where}: {p['name']} took a resource '{rn}' that is not "
                                   "declared")
    took = tuple(tuple((p.get("took") or {}).get(rn) for rn in res) for p in raw_procs)
    ckpt = tuple(p.get("checkpoint") for p in raw_procs)
    return {"resources": res, "units": units, "procs": procs, "claims": claims, "cpu": cpu,
            "preemptable": pre, "took": took, "checkpoint": ckpt}


def world(level: Level, start: int) -> Level:
    """The level as seen from one start. A start may bring its own resources and processes
    (level 3 has a one-resource bank and level 5 a three-resource detector), and every engine
    function takes the level this returns, so nothing below needs to know."""
    w = level.starts[start].world
    if not w:
        return level
    return replace(level, **w)


def _names_known(raw: dict, procs: tuple, where: str, what: str) -> None:
    for pn in (raw or {}):
        _refuse(pn not in procs, f"{where}: {what} names a process '{pn}' it does not have")


def _level_from_dict(L: dict, n: int) -> Level:
    _refuse(not isinstance(L, dict) or not L.get("id"), f"level {n} has no id")
    lid = str(L["id"])
    mode = str(L.get("mode") or "")
    _refuse(mode not in ("run", "bank"), f"level '{lid}' has mode '{mode}': it is run or bank")
    _refuse(not L.get("starts"), f"level '{lid}' has no starts")
    base = _world(L["resources"], L.get("processes") or [], lid) if "resources" in L else None
    starts = []
    for s in L["starts"]:
        _refuse(not isinstance(s, dict) or not s.get("label"),
                f"level '{lid}' has a start with no label")
        where = f"{lid}/{s['label']}"
        w = _world(s["resources"], s.get("processes") or [], where) if "resources" in s else None
        cur = w or base
        if cur is None:
            raise LevelError(f"{where}: no resources for this start")
        res, units, procs, claims = cur["resources"], cur["units"], cur["procs"], cur["claims"]
        m = len(res)
        hold_raw = s.get("hold") or {}
        pend_raw = s.get("pending") or {}
        _names_known(hold_raw, procs, where, "hold")
        _names_known(pend_raw, procs, where, "pending")
        _names_known(s.get("scripts"), procs, where, "scripts")
        hold = tuple(_vec(hold_raw.get(pn, [0] * m), m, f"{where}: hold of {pn}")
                     for pn in procs)
        pend = tuple((_vec(pend_raw[pn], m, f"pending of {pn}") if pn in pend_raw else None)
                     for pn in procs)
        scripts = ()
        if "scripts" in s:
            scripts = tuple(tuple(_step(st, m, pn) for st in (s["scripts"].get(pn) or []))
                            for pn in procs)
        for r in s.get("requests", []):
            _refuse(r.get("by") not in procs,
                    f"{where}: a request is by a process '{r.get('by')}' it does not have")
        reqs = tuple((procs.index(r["by"]), _vec(r["vec"], m, "request"))
                     for r in s.get("requests", []))
        for i, h in enumerate(hold):
            if claims and not vle(h, claims[i]):
                raise LevelError(f"{where}: {procs[i]} holds more than its max")
        tot = vzero(m)
        for h in hold:
            tot = vadd(tot, h)
        if not vle(tot, units):
            raise LevelError(f"{where}: more units held than exist")
        wd = dict(w or {})
        if "layout" in s:
            for node in s["layout"]:
                _refuse(node not in procs and node not in res,
                        f"{where}: the layout places '{node}', which is neither a process nor a "
                        "resource")
            wd["layout"] = {k: tuple(v) for k, v in s["layout"].items()}
        if "early_finish" in s:                       # a start may switch these
            wd["early_finish"] = bool(s["early_finish"])
        if "recovery" in s:
            bad = set(s["recovery"]) - set(RECOVERY)
            if bad:
                raise LevelError(f"{where}: unknown recovery {sorted(bad)}")
            wd["recovery"] = tuple(s["recovery"])
        starts.append(Start(label=str(s["label"]), hold=hold, pending=pend, scripts=scripts,
                            requests=reqs, note=s.get("note", ""), world=wd,
                            hide_need=bool(s.get("hide_need"))))
    labels = [s.label for s in starts]
    _refuse(len(set(labels)) != len(labels), f"level '{lid}' has two starts with the same label")
    base = base or _world(L["starts"][0]["resources"], L["starts"][0].get("processes") or [], lid)
    res, units, procs, claims, cpu = (base["resources"], base["units"], base["procs"],
                                      base["claims"], base["cpu"])
    versions = tuple(Version(str(v["id"]), str(v.get("label") or v["id"]), str(v["policy"]),
                             str(v.get("proves", "")))
                     for v in L.get("versions", []))
    for v in versions:
        _refuse(v.policy not in POLICIES,
                f"{lid}: version {v.id} has a policy '{v.policy}' the engine does not know")
        if v.policy == "graph" and any(u != 1 for u in units):
            raise LevelError(f"{lid}: the graph algorithm needs single-unit resources")
        if v.policy in ("graph", "banker") and claims is None:
            raise LevelError(f"{lid}: version {v.id} needs every process's max")
    _refuse(mode == "run" and not versions, f"level '{lid}' is a run level with no versions")
    vids = [v.id for v in versions]
    goals = tuple(Goal(str(g["id"]), str(g.get("chip") or g["id"]), str(g.get("text") or ""),
                       str(g.get("start") or ""), str(g.get("version", "") or ""),
                       str(g.get("kind") or ""))
                  for g in (L.get("goals") or []))
    for g in goals:
        if g.kind not in GOAL_KINDS:
            raise LevelError(f"{lid}: goal {g.id} has an unknown kind '{g.kind}'")
        _refuse(g.start not in labels, f"{lid}: goal {g.id} is on a start '{g.start}' the level "
                                       "does not have")
        _refuse(mode == "run" and g.version not in vids,
                f"{lid}: goal {g.id} is on a version '{g.version}' the level does not have")
        _refuse((mode == "bank") != (g.kind in BANK_KINDS),
                f"{lid}: goal {g.id} is a {g.kind} goal on a {mode} level")
    gids = [g.id for g in goals]
    _refuse(len(set(gids)) != len(gids), f"level '{lid}' has two goals with the same id")
    if mode == "bank":
        for s in starts:
            own = s.world.get("claims", claims)
            _refuse(own is None, f"{lid}/{s.label}: the banker needs every process's max")
    return Level(id=lid, n=n, title=str(L.get("title") or lid), mode=mode, resources=res,
                 units=units, procs=procs, starts=tuple(starts), versions=versions,
                 claims=claims, cpu=cpu, early_finish=bool(L.get("early_finish")),
                 recovery=tuple(L.get("recovery", ())), preemptable=base["preemptable"],
                 took=base["took"], checkpoint=base["checkpoint"], source=L.get("source", ""),
                 setup=L.get("setup", ""), asks=L.get("asks", ""),
                 layout={k: tuple(v) for k, v in L.get("layout", {}).items()},
                 goals=goals)


def levels_from_dict(d: dict) -> list:
    """The levels alone, as the reference engine's `from_dict` returned them."""
    return [_level_from_dict(L, n) for n, L in enumerate((d or {}).get("levels") or [], 1)]


def from_dict(d: dict) -> LabFile:
    """A parsed level file. Refuses a bad file with one sentence, as `schedlab` does."""
    d = d or {}
    lab_id = str(d.get("id") or "")
    _refuse(not lab_id, "the level file has no id")
    levels = tuple(levels_from_dict(d))
    ids = [lv.id for lv in levels]
    _refuse(len(set(ids)) != len(ids), f"'{lab_id}' has two levels with the same id")
    return LabFile(id=lab_id, title=str(d.get("title") or lab_id), levels=levels,
                   summary=str(d.get("summary") or ""), hue=str(d.get("hue") or ""),
                   released=bool(d.get("released", True)))


def from_yaml(text: str) -> LabFile:
    return from_dict(yaml.safe_load(text) or {})


def load(path) -> LabFile:
    return from_yaml(Path(path).read_text(encoding="utf-8"))


def get(lab_id: str) -> LabFile | None:
    """A shipped lab this engine reads, by id (`t-lab-03`), or None."""
    from .tlab_catalog import files
    for p in files("bank"):
        lab = load(p)
        if lab.id == lab_id:
            return lab
    return None


def _step(raw, m: int, who: str):
    if raw == "finish":
        return ("finish",)
    if isinstance(raw, dict) and "ask" in raw:
        return ("ask", _vec(raw["ask"], m, f"ask of {who}"))
    raise LevelError(f"{who}: a step is 'finish' or {{ask: [...]}}, not {raw!r}")


# --------------------------------------------------------------------------- #
# run levels: the state
# --------------------------------------------------------------------------- #
# A state is a plain dict, so it can be copied and keyed with json and handed to the Teaching
# Center as-is. `_script` (added by `start_state`) carries the scripts, so a replay needs nothing
# but the level and the moves.
def init(level: Level, start: int) -> dict:
    s = level.starts[start]
    n = len(level.procs)
    return {
        "hold": [list(h) for h in s.hold],
        "pend": [list(p) if p is not None else None for p in s.pending],
        "pc": [0] * n,
        "done": [False] * n,
        "killed": [False] * n,
        "refused": [False] * n,          # waiting although the units are free (avoidance said no)
        "queue": [i for i in range(n) if s.pending[i] is not None],   # waiters, oldest first
        "kills": [],
        "error": "",
        "redo": [None] * n,              # after a rollback: the steps it must do again
        "ri": [0] * n,
        "cost": 0,                       # minutes of work thrown away by recovery so far
        "acts": [],                      # the recovery actions taken, in order
        "base": None,                    # the state the detector found, before any recovery
    }


def key(st: dict) -> str:
    return json.dumps([st["hold"], st["pend"], st["pc"], st["done"], st["killed"], st["queue"],
                       st["redo"], st["ri"]])


def clone(st: dict) -> dict:
    return json.loads(json.dumps(st))


def avail(level: Level, st: dict) -> tuple:
    a = tuple(level.units)
    for h in st["hold"]:
        a = vsub(a, h)
    return a


def active(st: dict, p: int) -> bool:
    return not st["done"][p] and not st["killed"][p]


def blocked(st: dict, p: int) -> bool:
    return st["pend"][p] is not None


# --------------------------------------------------------------------------- #
# the resource-allocation graph (§14.3, the notes' three edges)
# --------------------------------------------------------------------------- #
def edges(level: Level, st: dict, show_claims: bool) -> list:
    """(kind, from, to, count): kind is "alloc" (resource -> process, solid), "request"
    (process -> resource, dashed) or "claim" (process -> resource, dotted). An edge changes kind
    as the process runs: claim -> request -> allocation -> back to claim when released."""
    out = []
    for p, pn in enumerate(level.procs):
        if not active(st, p):
            continue
        for r, rn in enumerate(level.resources):
            h = st["hold"][p][r]
            q = st["pend"][p][r] if st["pend"][p] is not None else 0
            if h:
                out.append(("alloc", rn, pn, h))
            if q:
                out.append(("request", pn, rn, q))
            if show_claims and level.claims is not None and not h and not q \
                    and level.claims[p][r] > 0:
                out.append(("claim", pn, rn, level.claims[p][r]))
    return out


def wait_for(level: Level, st: dict) -> list:
    """The wait-for graph of §14.7 (Figure 14.9): drop the resource nodes and draw Pi -> Pj when
    Pi waits for something Pj holds. Meaningful when every resource has a single unit."""
    out = []
    for p, pn in enumerate(level.procs):
        if not active(st, p) or st["pend"][p] is None:
            continue
        for r in range(level.m):
            if st["pend"][p][r]:
                for q, qn in enumerate(level.procs):
                    if q != p and active(st, q) and st["hold"][q][r]:
                        out.append(("wait", pn, qn, 1))
    return out


def all_cycles(es: list) -> list:
    """Every simple cycle, each once, written from its smallest node name and back to it
    ("List all the cycles in the graph"). Graphs here have a handful of nodes, so a plain DFS from
    each node, keeping only cycles whose start is their smallest node, is enough."""
    adj: dict = {}
    for _, a, b, _ in es:
        adj.setdefault(a, []).append(b)
    nodes = sorted(set(adj) | {b for v in adj.values() for b in v})
    out = []

    def go(start, u, path, seen):
        for v in sorted(adj.get(u, [])):
            if v == start:
                out.append(path + [start])
            elif v > start and v not in seen:
                go(start, v, path + [v], seen | {v})

    for n in nodes:
        go(n, n, [n], {n})
    return out


def has_cycle(es: list) -> list:
    """A cycle in the graph as a node list, or [] — any edge kind counts as an arrow."""
    adj: dict = {}
    for _, a, b, _ in es:
        adj.setdefault(a, []).append(b)
    color: dict = {}
    stack: list = []

    def dfs(u):
        color[u] = 1
        stack.append(u)
        for v in adj.get(u, []):
            if color.get(v, 0) == 0:
                c = dfs(v)
                if c:
                    return c
            elif color.get(v) == 1:
                return stack[stack.index(v):] + [v]
        stack.pop()
        color[u] = 2
        return []

    for u in list(adj):
        if color.get(u, 0) == 0:
            c = dfs(u)
            if c:
                return c
    return []


def reduce(level: Level, st: dict) -> tuple:
    """Graph reduction (§14.3.1), which is the detector of §14.7 in matrix form: Work = Avail;
    erase any process whose current request fits in Work and add back what it holds. Returns
    (rows, deadlocked): rows are (process, request, work before, work after) in erase order."""
    work = avail(level, st)
    left = [p for p in range(len(level.procs)) if active(st, p)]
    rows = []
    moved = True
    while moved:
        moved = False
        for p in left:
            req = current_request(level, st, p)
            if vle(req, work):
                new = vadd(work, tuple(st["hold"][p]))
                rows.append((p, req, work, new))
                work = new
                left.remove(p)
                moved = True
                break
    return rows, tuple(left)


def current_request(level: Level, st: dict, p: int) -> tuple:
    """What the detector counts as P's request. A waiting process: what it waits for. A process
    that was rolled back: everything it must take again (what it gave back, then what it was
    waiting for) — so a rollback that gave back nothing does not pass for a recovery. Anyone
    else: nothing, which is the detector's optimism (§14.7)."""
    if st["pend"][p] is not None:
        return tuple(st["pend"][p])
    if st.get("redo") and st["redo"][p] is not None:
        need_ = vzero(level.m)
        for step in st["redo"][p][st["ri"][p]:]:
            if step[0] == "ask":
                need_ = vadd(need_, tuple(step[1]))
        return need_
    return vzero(level.m)


# --------------------------------------------------------------------------- #
# policies
# --------------------------------------------------------------------------- #
def need(level: Level, st: dict, p: int) -> tuple:
    return vsub(level.claims[p], tuple(st["hold"][p]))


def grant_ok(level: Level, version: Version, st: dict, p: int, vec: tuple) -> tuple:
    """(ok, why-not). The units must be there whatever the policy; avoidance may refuse more."""
    if not vle(vec, avail(level, st)):
        return False, "units not free"
    if version.policy == "free":
        return True, ""
    trial = clone(st)
    trial["hold"][p] = list(vadd(tuple(trial["hold"][p]), vec))
    trial["pend"][p] = None
    if version.policy == "graph":
        cyc = has_cycle(edges(level, trial, show_claims=True))
        return (False, "would close a cycle through claim edges") if cyc else (True, "")
    if version.policy == "banker":
        a = avail(level, trial)
        hold = [tuple(h) for h in trial["hold"]]
        nd = [need(level, trial, q) for q in range(len(level.procs))]
        live = [active(trial, q) for q in range(len(level.procs))]
        return (True, "") if is_safe(a, hold, nd, live) else (False, "would leave the state unsafe")
    raise LevelError(f"unknown policy {version.policy}")


def _wake(level: Level, version: Version, st: dict) -> list:
    """After a release, grant every waiter the policy now allows, oldest first, until nothing
    changes. Returns the notes for the trace ("P1 gets printer")."""
    notes = []
    changed = True
    while changed:
        changed = False
        for p in list(st["queue"]):
            vec = tuple(st["pend"][p])
            ok, why = grant_ok(level, version, st, p, vec)
            if ok:
                st["hold"][p] = list(vadd(tuple(st["hold"][p]), vec))
                st["pend"][p] = None
                st["refused"][p] = False
                st["queue"].remove(p)
                notes.append(f"{level.procs[p]} gets {names(level, vec)}")
                changed = True
                break
            st["refused"][p] = (why != "units not free")
    return notes


def names(level: Level, vec) -> str:
    """A request in words: "printer", "2 × A and C", or, with one resource type, "1 unit"."""
    if level.m == 1:                       # one resource type: "1 unit", as the notes say it
        return f"{vec[0]} unit" + ("" if vec[0] == 1 else "s")
    parts = []
    for r, k in enumerate(vec):
        if k:
            parts.append(level.resources[r] if k == 1 else f"{k} × {level.resources[r]}")
    return " and ".join(parts) if parts else "nothing"


_names = names        # the reference's spelling


# --------------------------------------------------------------------------- #
# run levels: moves
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Move:
    p: int
    kind: str          # "step" | "finish" | "kill" | "rollback" | "preempt:<resource index>"
    label: str
    cost: int = 0      # minutes of work a recovery move throws away
    short: str = ""    # the label without the process name, for the Recover tab's buttons


def lost(m: int) -> str:
    return f"{m // 60} h lost" if m >= 60 and m % 60 == 0 else f"{m} min lost"


_lost = lost


def is_recovery(kind: str) -> bool:
    return kind in ("kill", "rollback") or kind.startswith("preempt:")


def recovery_moves(level: Level, st: dict) -> list:
    """The three ways out of §14.7, for every live process. All are offered, useful or not:
    telling them apart is the lesson. Each is a rollback to some earlier minute — termination to
    the very start, a checkpoint to its minute, a preemption to just before the resource was
    taken — and it costs the work done since."""
    out = []
    for p, pn in enumerate(level.procs):
        if not active(st, p):
            continue
        cpu = level.cpu[p] if level.cpu else 0
        if "terminate" in level.recovery:
            out.append(Move(p, "kill", f"Terminate {pn} · {lost(cpu)}", cpu,
                            f"Terminate · {lost(cpu)}"))
        if "rollback" in level.recovery and level.checkpoint and level.checkpoint[p] is not None \
                and st["redo"][p] is None:
            t = level.checkpoint[p]
            out.append(Move(p, "rollback", f"Roll {pn} back to minute {t} · {lost(cpu - t)}",
                            cpu - t, f"Roll back to minute {t} · {lost(cpu - t)}"))
        if "preempt" in level.recovery and st["redo"][p] is None:
            for r, rn in enumerate(level.resources):
                t = level.took[p][r] if level.took else None
                if level.preemptable and level.preemptable[r] and st["hold"][p][r] \
                        and t is not None:
                    out.append(Move(p, f"preempt:{r}", f"Preempt {rn} from {pn} · {lost(cpu - t)}",
                                    cpu - t, f"Preempt {rn} · {lost(cpu - t)}"))
    return out


def moves(level: Level, st: dict, include_kill: bool = True) -> list:
    out = []
    for p, pn in enumerate(level.procs):
        if not active(st, p) or blocked(st, p):
            continue
        nxt = _next_step(level, st, p)
        if nxt is not None:
            out.append(Move(p, "step", _step_label(level, p, nxt)))
        if level.early_finish and (nxt is None or nxt[0] != "finish"):
            out.append(Move(p, "finish", f"{pn} finishes"))
    if include_kill and level.recovery:
        out += recovery_moves(level, st)
    return out


def _next_step(level: Level, st: dict, p: int):
    if st["redo"][p] is not None:
        r = st["redo"][p]
        return tuple(r[st["ri"][p]]) if st["ri"][p] < len(r) else None
    script = st.get("_script")
    steps = script[p] if script else ()
    return steps[st["pc"][p]] if st["pc"][p] < len(steps) else None


def _step_label(level: Level, p: int, step) -> str:
    if step[0] == "finish":
        return f"{level.procs[p]} finishes"
    return f"{level.procs[p]} asks for {names(level, step[1])}"


def start_state(level: Level, start: int) -> dict:
    """init() plus the scripts carried in the state, so a replay needs nothing else."""
    st = init(level, start)
    st["_script"] = [[list(s) if s[0] == "finish" else ["ask", list(s[1])] for s in sc]
                     for sc in level.starts[start].scripts] or [[] for _ in level.procs]
    return st


def apply(level: Level, version: Version, st0: dict, mv: Move) -> tuple:
    """One move. Returns (new state, trace line)."""
    st = clone(st0)
    p, pn = mv.p, level.procs[mv.p]
    if is_recovery(mv.kind):
        if st["base"] is None:
            base = clone(st0)
            base["base"] = None
            st["base"] = base                # the detector's state, for "the cheapest"
        st["cost"] += mv.cost
        st["acts"].append([p, mv.kind])
    if mv.kind == "rollback" or mv.kind.startswith("preempt:"):
        t = level.checkpoint[p] if mv.kind == "rollback" else level.took[p][int(mv.kind[8:])]
        back = [st["hold"][p][r] if level.took[p][r] is not None and level.took[p][r] >= t else 0
                for r in range(level.m)]
        st["hold"][p] = list(vsub(tuple(st["hold"][p]), tuple(back)))
        old = st["pend"][p]
        st["pend"][p] = None
        st["refused"][p] = False
        if p in st["queue"]:
            st["queue"].remove(p)
        redo = []
        if any(back):
            redo.append(["ask", back])
        if old is not None:
            redo.append(["ask", old])
        redo.append(["finish"])
        st["redo"][p] = redo                 # it runs again from minute t and asks again
        st["ri"][p] = 0
        notes = _wake(level, version, st)
        what = (f"{pn} is rolled back to minute {t}" if mv.kind == "rollback"
                else f"{pn} loses {level.resources[int(mv.kind[8:])]} and is rolled back to "
                     f"minute {t}")
        gave = f", giving back {names(level, back)}" if any(back) else ", giving back nothing"
        return st, what + gave + "".join(f"; {n}" for n in notes)
    if mv.kind == "kill":
        st["hold"][p] = list(vzero(level.m))
        st["pend"][p] = None
        st["killed"][p] = True
        if p in st["queue"]:
            st["queue"].remove(p)
        st["kills"].append(p)
        notes = _wake(level, version, st)
        return st, f"{pn} is terminated" + "".join(f"; {n}" for n in notes)
    step = _next_step(level, st, p) if mv.kind == "step" else ("finish",)
    if mv.kind == "step":
        if st["redo"][p] is not None:
            st["ri"][p] += 1
        else:
            st["pc"][p] += 1
    if step[0] == "finish":
        st["hold"][p] = list(vzero(level.m))
        st["done"][p] = True
        notes = _wake(level, version, st)
        return st, f"{pn} finishes and returns what it held" + "".join(f"; {n}" for n in notes)
    vec = tuple(step[1])
    if level.claims is not None and not vle(vadd(tuple(st["hold"][p]), vec), level.claims[p]):
        st["error"] = f"{pn} asks for more than its max"
        return st, st["error"]
    ok, why = grant_ok(level, version, st, p, vec)
    if ok:
        st["hold"][p] = list(vadd(tuple(st["hold"][p]), vec))
        return st, f"{pn} asks for {names(level, vec)}: granted"
    st["pend"][p] = list(vec)
    st["queue"].append(p)
    st["refused"][p] = why != "units not free"
    if st["refused"][p]:
        return st, f"{pn} asks for {names(level, vec)}: waits, {why}"
    return st, f"{pn} asks for {names(level, vec)}: waits"


def finished(st: dict) -> bool:
    return all(not a for a in (active(st, p) for p in range(len(st["done"]))))


def stuck(level: Level, st: dict) -> bool:
    """Nobody can move and somebody is still there: a deadlock (or, under avoidance, a level
    that is wrong — the proof checks for exactly this)."""
    return not finished(st) and not moves(level, st, include_kill=False)


def deadlocked(level: Level, st: dict) -> tuple:
    return reduce(level, st)[1]


# --------------------------------------------------------------------------- #
# run levels: goals and Try every order
# --------------------------------------------------------------------------- #
def reach_met(level: Level, goal: Goal, st: dict) -> bool:
    k = goal.kind
    if k == "deadlock":
        return bool(deadlocked(level, st))
    if k == "cycle_no_deadlock":
        return bool(has_cycle(edges(level, st, show_claims=False))) and not deadlocked(level, st)
    if k == "refused_free":
        return any(st["refused"][p] and blocked(st, p)
                   and vle(tuple(st["pend"][p]), avail(level, st)) for p in range(len(level.procs)))
    if k == "all_done":
        return finished(st) and not st["kills"]
    if k == "cheapest_victim":
        return (len(st["kills"]) == 1 and len(st["acts"]) == 1 and not deadlocked(level, st)
                and st["kills"][0] == cheapest_victim(level, st["base"]))
    if k == "cheapest_recovery":
        return (bool(st["acts"]) and not deadlocked(level, st)
                and st["cost"] == min_recovery(level, st["base"])[0])
    return False


def min_recovery(level: Level, st: dict, depth: int = 3) -> tuple:
    """The cheapest set of recovery actions after which the detector finds no deadlock:
    (minutes lost, actions). Cheapest-first over action sequences, at most `depth` long."""
    s0 = clone(st)
    heap = [(0, 0, key(s0), s0, [])]
    seen = set()
    tick = 0
    while heap:
        cost, _, k, cur, acts = heapq.heappop(heap)
        if not deadlocked(level, cur):
            return cost, tuple(acts)
        if k in seen or len(acts) >= depth:
            continue
        seen.add(k)
        for mv in recovery_moves(level, cur):
            nx, _ = apply(level, Version("free", "", "free"), cur, mv)
            tick += 1
            heapq.heappush(heap, (cost + mv.cost, tick, key(nx), nx, acts + [(mv.p, mv.kind)]))
    return None, ()


def victims(level: Level, st: dict) -> list:
    """Processes whose termination alone breaks the deadlock (§14.7, Worked example 14.8)."""
    out = []
    for p in deadlocked(level, st):
        trial = clone(st)
        trial["hold"][p] = list(vzero(level.m))
        trial["pend"][p] = None
        trial["killed"][p] = True
        if not deadlocked(level, trial):
            out.append(p)
    return out


def cheapest_victim(level: Level, st: dict):
    vs = victims(level, st)
    return min(vs, key=lambda p: level.cpu[p]) if vs else None


@dataclass(frozen=True)
class Outcome:
    label: str
    ok: bool
    moves: tuple       # a shortest order of moves reaching it, as (p, kind)


@dataclass(frozen=True)
class Exploration:
    states: int
    outcomes: tuple
    proved: bool
    reason: str
    capped: bool


def _end_label(level: Level, st: dict, mvs: list) -> tuple | None:
    """(label, ok) for a state Try every order stops at, or None when it goes on."""
    if st["error"]:
        return "error: " + st["error"], False
    if finished(st):
        return "everyone finishes", True
    if not mvs:
        dl = deadlocked(level, st)
        return ("deadlock: " + ", ".join(level.procs[p] for p in dl) if dl else "stuck"), False
    return None


def explore(level: Level, version: Version, start: int) -> Exploration:
    """Every order of steps (terminations are a recovery, not a future, so they are left out),
    breadth-first, so the first witness found for each outcome is a shortest one."""
    s0 = start_state(level, start)
    seen = {key(s0): (None, None)}
    order = [s0]
    outs: dict = {}
    qi = 0
    while qi < len(order) and qi < EXPLORE_CAP:
        st = order[qi]
        qi += 1
        k = key(st)
        mvs = [] if (st["error"] or finished(st)) else moves(level, st, include_kill=False)
        end = _end_label(level, st, mvs)
        if end is not None:
            outs.setdefault(end[0], (end[1], k))
            continue
        for mv in mvs:
            nx, _ = apply(level, version, st, mv)
            nk = key(nx)
            if nk in seen:
                continue
            seen[nk] = (k, (mv.p, mv.kind))
            order.append(nx)

    def path(k):
        out = []
        while seen[k][0] is not None:
            pk, m = seen[k]
            out.append(m)
            k = pk
        return tuple(reversed(out))

    listed = tuple(Outcome(lab, ok, path(k)) for lab, (ok, k) in
                   sorted(outs.items(), key=lambda kv: (not kv[1][0], kv[0])))
    capped = qi < len(order)
    proved = bool(listed) and all(o.ok for o in listed) and not capped
    reason = ""
    if not proved:
        bad = next((o for o in listed if not o.ok), None)
        reason = ("GINI stopped after 20 000 states" if capped
                  else f"an order ends in {bad.label}" if bad else "nothing finishes")
    return Exploration(states=len(order), outcomes=listed, proved=proved, reason=reason,
                       capped=capped)


def count_orders(level: Level, version: Version, start: int) -> dict:
    """How many orders of moves end in each outcome, {label: count}. Not in the reference: the
    chain's Try-every-order entry records it, as T-Lab 02's records schedules per outcome.

    Every move takes a process one step further (or finishes it), so the states form a DAG and
    the count is a memoised walk over it. It visits the same states as `explore`; past
    EXPLORE_CAP it gives up and returns {} rather than a number that is not a count."""
    memo: dict = {}

    def go(st) -> dict:
        k = key(st)
        if k in memo:
            return memo[k]
        if len(memo) >= EXPLORE_CAP:
            raise OverflowError
        mvs = [] if (st["error"] or finished(st)) else moves(level, st, include_kill=False)
        end = _end_label(level, st, mvs)
        out: dict = {}
        if end is not None:
            out = {end[0]: 1}
        else:
            for mv in mvs:
                for lab, n in go(apply(level, version, st, mv)[0]).items():
                    out[lab] = out.get(lab, 0) + n
        memo[k] = out
        return out

    try:
        return go(start_state(level, start))
    except (OverflowError, RecursionError):
        return {}


def replay(level: Level, version: Version, start: int, path) -> tuple:
    """Re-run a recorded order of moves; returns (state, trace lines). The certificate check the
    Teaching Center runs on a recorded goal, as for T-Lab 02."""
    st = start_state(level, start)
    lines = []
    for p, kind in path:
        p = int(p)
        mv = next((m for m in moves(level, st) if m.p == p and m.kind == kind), None)
        if mv is None:
            raise LevelError(f"move {level.procs[p]}/{kind} is not possible here")
        st, line = apply(level, version, st, mv)
        lines.append(line)
    return st, lines


# --------------------------------------------------------------------------- #
# bank levels: the banker's algorithm (§14.6.3), done by hand
# --------------------------------------------------------------------------- #
def is_safe(a, hold, nd, live=None) -> bool:
    return bool(safe_sequence(a, hold, nd, live))


def safe_sequence(a, hold, nd, live=None) -> tuple:
    """The notes' check: the first process in index order whose Need fits, repeatedly."""
    n = len(hold)
    live = live or [True] * n
    work = tuple(a)
    fin = [not x for x in live]
    seq = []
    while True:
        p = next((i for i in range(n) if not fin[i] and vle(nd[i], work)), None)
        if p is None:
            break
        work = vadd(work, hold[p])
        fin[p] = True
        seq.append(p)
    return tuple(seq) if all(fin) else ()


def all_safe_sequences(a, hold, nd) -> list:
    """Every safe sequence — what Try every order shows on a bank level."""
    n = len(hold)
    out = []

    def go(work, fin, seq):
        if len(seq) == n:
            out.append(tuple(seq))
            return
        for i in range(n):
            if not fin[i] and vle(nd[i], work):
                fin[i] = True
                go(vadd(work, hold[i]), fin, seq + [i])
                fin[i] = False

    go(tuple(a), [False] * n, [])
    return out


@dataclass
class Check:
    """A safety check being done by hand. `rows` are (process, need, work, fits, work after)."""
    avail: tuple
    hold: tuple
    need: tuple
    work: tuple
    fin: list
    rows: list = field(default_factory=list)
    stuck_claimed: bool = False


def bank_state(level: Level, start: int) -> tuple:
    """(Avail, Hold, Need) of a bank start. Pass the level `world` returns for that start."""
    s = level.starts[start]
    hold = tuple(s.hold)
    a = tuple(level.units)
    for h in hold:
        a = vsub(a, h)
    nd = tuple(vsub(level.claims[p], hold[p]) for p in range(len(hold)))
    return a, hold, nd


def tests(level: Level, start: int, p: int, req: tuple) -> dict:
    """The three tests of "a request arrives", in the notes' order."""
    a, hold, nd = bank_state(level, start)
    if not vle(req, nd[p]):
        return {"verdict": "error", "why": f"{level.procs[p]} asks for more than it declared",
                "le_need": False}
    if not vle(req, a):
        return {"verdict": "wait", "why": "the units are not there", "le_need": True,
                "le_avail": False}
    hold2 = tuple(vadd(h, req) if i == p else h for i, h in enumerate(hold))
    nd2 = tuple(vsub(x, req) if i == p else x for i, x in enumerate(nd))
    a2 = vsub(a, req)
    safe = is_safe(a2, hold2, nd2)
    return {"verdict": "grant" if safe else "wait",
            "why": "the state stays safe" if safe else "the state would be unsafe",
            "le_need": True, "le_avail": True, "provisional": (a2, hold2, nd2)}


def check_begin(a, hold, nd) -> Check:
    return Check(avail=tuple(a), hold=tuple(hold), need=tuple(nd), work=tuple(a),
                 fin=[False] * len(hold))


def check_pick(c: Check, p: int) -> tuple:
    """The student tries P_p: a row is written whether it fits or not, as in Worked example 14.5."""
    if c.fin[p]:
        raise LevelError("that process has already finished in this check")
    fits = vle(c.need[p], c.work)
    after = vadd(c.work, c.hold[p]) if fits else c.work
    c.rows.append((p, c.need[p], c.work, fits, after))
    if fits:
        c.work = after
        c.fin[p] = True
    return c.rows[-1]


def check_done(c: Check) -> bool:
    return all(c.fin)


def check_stuck(c: Check) -> bool:
    """True when no unfinished process fits: the student may say "unsafe" only then."""
    return not check_done(c) and not any(not c.fin[i] and vle(c.need[i], c.work)
                                         for i in range(len(c.fin)))
