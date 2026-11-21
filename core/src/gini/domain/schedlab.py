"""The scheduler lab's engine: two threads, one atomic line per step, and every schedule.

T-Lab 02 ("You are the scheduler") is a simulator and not xv6, on purpose. The bug a concurrency
lab is about is the one schedule in twenty that loses an update, and a real kernel cannot be told
to take that schedule, cannot show the other nineteen, and cannot say "no schedule breaks this".
This module can: `step` moves time by exactly one line of one thread, `replay` turns a schedule into
the course notes' trace diagram, and `explore` walks every reachable state and either proves the
code or hands back the shortest schedule that breaks it.

It is a port of the reference engine the professor approved on the design mockup
(`docs/design/t-lab-02-reference/engine.js`), and the port is checked against it two ways: the
golden checks from that folder, and a fixture exported from the JavaScript for every level,
version and starting state (`tests/data/t-lab-02-engine_*.json`). Where the two engines disagree,
the JavaScript is what the professor saw; fix the port.

Pure, like the rest of `gini.domain`: no Qt, no I/O except reading a level file, and no
randomness ("Random" lives in the face). The purity is load-bearing rather than tidy. The
Teaching Center installs gini-core too, so it can replay a submitted schedule and re-explore a
submitted fix with this same code and confirm what the chain claims — a trace is a certificate
only because the thing that checks it has no Qt in it. Exploration is therefore deterministic:
the same code gives the same witnesses on every machine.

**A level is data.** The four levels ship as `tlabs/t-lab-02.yaml`; the loader at the bottom of
this module validates one and refuses, in one sentence each, anything that would not run. Later
T-Labs are new level files, not new code, so the loader is written as if three more are coming.

Two limits of this release, stated here because the engine does not enforce them everywhere it
could: levels have exactly two threads (the face has two lanes and a two-dimensional progress
graph), and each thread runs its lines once — there are no loops, because an unbounded loop is a
state space the explorer cannot finish.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TLABS_DIR = Path(__file__).parent / "tlabs"

#: Reachable states an exploration will visit before giving up. The shipped levels reach a few
#: dozen; the cap exists so a later level that explodes says so instead of hanging the face.
EXPLORE_CAP = 20_000

#: How an unset register prints ("–", an en dash) and how a negative semaphore count prints
#: (with a true minus sign, U+2212, as the notes' Figure 9.4 draws it).
UNSET = "–"
MINUS = "−"

#: The operations a tile may carry. The names are the level file's; the mockup's engine spelled
#: a few differently (`addi`, `setv`, `tas`, `spinwhile`, `atomicadd`) and the loader does not
#: accept those, because a level file is a contract and one spelling is enough.
OPS = ("load", "add", "store", "atomic_add", "set", "nop", "spin_while", "test_and_set",
       "wait", "signal")

#: What a thread's header pill can say.
READY, BLOCKED, DONE = "ready", "blocked", "done"

#: What a trace row's box is drawn as (the notes' colour key).
PLAIN, CS, SPIN, OK, BLOCK = "plain", "cs", "spin", "ok", "blocked"


class LevelError(ValueError):
    """A level file that would not run. The message is one sentence naming what is wrong."""


# --------------------------------------------------------------------------- #
# a level, as data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tile:
    """One line of code. `text` is what the student reads; `op` and the operands are what it does.

    `cs` marks a line as inside the critical section, which is what "both inside" is checked
    against; `mark` colours the row (`cs` for red) independently of that. `effect`, `emit` and
    `needs` are how the engine checks a program's MEANING during a run (§3.5 of the plan): a fix
    that no longer deadlocks because it also no longer waits is caught by these, not by the
    deadlock check.
    """
    id: str
    text: str
    op: str
    var: str = ""
    reg: str = ""
    k: int = 0
    value: object = None
    sem: str = ""
    cs: bool = False
    mark: str = ""
    effect: str = ""
    emit: str = ""
    needs: str = ""
    needs_error: str = ""


@dataclass(frozen=True)
class Version:
    id: str
    label: str
    code: tuple            # tuple[tuple[str, ...], ...]: tile ids per thread
    proves: str = ""


@dataclass(frozen=True)
class Fix:
    """The presence of a fix is what puts a Fix it button on a level."""
    from_version: str
    pinned: tuple
    proves: str = ""


@dataclass(frozen=True)
class Start:
    label: str
    shared: dict = field(default_factory=dict)
    semaphores: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Register:
    name: str
    short: str


@dataclass(frozen=True)
class Goal:
    """`reach` is "deadlock", "both_inside" or `{"finished": True, "shared": {...}}`, checked
    after every step of a schedule on `version`; `prove` is met when Try every schedule proves
    that version (or, for version "fix", the student's edited code)."""
    id: str
    version: str
    chip: str
    text: str
    reach: object = None
    prove: bool = False


@dataclass(frozen=True)
class WrongNote:
    """What the trace's last row says when a run finishes with the wrong answer and no recorded
    error: the note for the value of `by`, e.g. counter 4 -> "4: the ++ is lost"."""
    by: str
    notes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Level:
    id: str
    n: int
    title: str
    source: str = ""
    setup: str = ""
    asks: str = ""
    hint: str = ""
    threads: tuple = ("A", "B")
    state_head: str = ""
    shade_both_inside: bool = False
    count_schedules: bool = False
    mutex_check: str = ""              # the label used in results; "" means not checked
    shared: dict = field(default_factory=dict)
    registers: tuple = ()              # one Register per thread, by position
    semaphores: dict = field(default_factory=dict)
    semaphore_notes: dict = field(default_factory=dict)
    starts: tuple = ()
    tiles: dict = field(default_factory=dict)
    versions: tuple = ()
    default: str = ""
    fix: Fix | None = None
    state: str = ""
    state_extra: dict = field(default_factory=dict)
    outcome: str = ""
    correct_when: dict = field(default_factory=dict)
    wrong_note: WrongNote | None = None
    goals: tuple = ()

    def version(self, version_id: str) -> Version | None:
        return next((v for v in self.versions if v.id == version_id), None)

    def code_of(self, version_id: str) -> tuple:
        """The program a version (or "fix", the broken version Fix it starts from) runs."""
        if version_id == "fix" and self.fix is not None:
            version_id = self.fix.from_version
        v = self.version(version_id)
        if v is None:
            raise LevelError(f"level '{self.id}' has no version '{version_id}'")
        return v.code

    def version_ids(self) -> tuple:
        return tuple(v.id for v in self.versions)

    def start_labels(self) -> tuple:
        return tuple(s.label for s in self.starts)

    def tile(self, tile_id: str) -> Tile:
        return self.tiles[tile_id]


@dataclass(frozen=True)
class LevelFile:
    """One T-Lab: an id, a title, and its levels in order."""
    id: str
    title: str
    levels: tuple = ()
    summary: str = ""
    hue: str = ""
    released: bool = True
    engine: str = "sched"

    def level(self, level_id: str) -> Level | None:
        return next((lv for lv in self.levels if lv.id == level_id), None)


# --------------------------------------------------------------------------- #
# states
# --------------------------------------------------------------------------- #
# A state is a plain dict, so it can be copied and keyed with json and handed to the Teaching
# Center as-is. Its shape:
#   code     [[tile ids], [tile ids]]      fixed for a replay or an exploration
#   pc       [i, j]                        each thread's next line
#   status   ["ready" | "blocked" | "done"] x 2
#   granted  [bool, bool]                  woken by a signal, and yet to pass its wait
#   regs     [{name: value}, {name: value}]
#   shared   {var: value, "ev": {event: True}, "err": ""}
#   sems     {name: {"count": n, "queue": [thread, ...]}}

def _clone(o):
    return json.loads(json.dumps(o))


def _key(st: dict) -> str:
    """The state's identity. `code` is left out: it is fixed for the whole exploration."""
    return json.dumps([st["pc"], st["status"], st["granted"], st["regs"], st["shared"],
                       st["sems"]], sort_keys=True, separators=(",", ":"))


def init(level: Level, code, start: int = 0) -> dict:
    """The initial state of `code` on `level`, from its `start`-th starting state (0 when the
    level has none)."""
    code = [list(th) for th in code]
    if len(code) != 2:
        raise LevelError("a program has exactly two threads")
    from_ = level.starts[start] if level.starts else None
    sems = dict(level.semaphores)
    shared = _clone(level.shared)
    if from_ is not None:
        sems.update(from_.semaphores)
        shared.update(_clone(from_.shared))
    shared["ev"] = {}
    shared["err"] = ""
    regs = []
    for t in range(2):
        reg = level.registers[t] if t < len(level.registers) else None
        regs.append({reg.name: None} if reg else {})
    st = {"code": code, "pc": [0, 0], "status": [READY, READY], "granted": [False, False],
          "regs": regs, "shared": shared,
          "sems": {name: {"count": int(n), "queue": []} for name, n in sems.items()}}
    for t in range(2):
        if not code[t]:
            st["status"][t] = DONE
    return st


def is_done(st: dict) -> bool:
    return st["status"][0] == DONE and st["status"][1] == DONE


def enabled(st: dict) -> list:
    """The threads that may take a step."""
    return [t for t in range(2) if st["status"][t] == READY]


def inside(level: Level, st: dict, t: int) -> bool:
    """A thread is inside when its NEXT line is a critical-section line."""
    th = st["code"][t]
    pc = st["pc"][t]
    return pc < len(th) and level.tiles[th[pc]].cs


def both_inside(level: Level, st: dict) -> bool:
    return bool(level.mutex_check) and inside(level, st, 0) and inside(level, st, 1)


def sem_value(st: dict, name: str) -> int:
    """The count students see: stored count minus the number queued (notes, Figure 9.4)."""
    s = st["sems"][name]
    return s["count"] - len(s["queue"])


def sem_show(st: dict, name: str) -> str:
    return str(sem_value(st, name)).replace("-", MINUS)


def uses(level: Level, code, sem: str) -> bool:
    return any(level.tiles[tid].sem == sem for th in code for tid in th)


def is_stuck(level: Level, st: dict) -> bool:
    """Nobody can change anything: everyone left is blocked, or every move left is a spin.

    The face calls this "deadlock", and in the four shipped levels every stuck state is one. The
    definition also covers "spins forever", which no shipped level can reach now that levels 1
    and 2 are read-only; a later level that can should label it that way, not "deadlock".
    """
    if is_done(st):
        return False
    e = enabled(st)
    if not e:
        return True
    k = _key(st)
    return all(_key(step(level, st, t)[0]) == k for t in e)


is_deadlock = is_stuck


@dataclass
class Row:
    """One row of the trace: which lane, what it says, how the box is coloured, the shared state
    after the step, a note for the state column, and whether this is where things went wrong."""
    t: int
    text: str
    kind: str = PLAIN
    note: str = ""
    state: str = ""
    bad: bool = False


def _advance(st: dict, t: int) -> None:
    st["pc"][t] += 1
    if st["pc"][t] >= len(st["code"][t]):
        st["status"][t] = DONE


def step(level: Level, st0: dict, t: int) -> tuple:
    """One atomic step of thread `t`: the new state and the trace row it writes."""
    st = _clone(st0)
    tile = level.tiles[st["code"][t][st["pc"][t]]]
    who = level.threads[t]
    sh = st["shared"]
    row = Row(t=t, text=tile.text, kind=tile.mark or PLAIN)
    op = tile.op
    if op == "load":
        st["regs"][t][tile.reg] = sh[tile.var]
        _advance(st, t)
    elif op == "add":
        st["regs"][t][tile.reg] = (st["regs"][t].get(tile.reg) or 0) + tile.k
        _advance(st, t)
    elif op == "store":
        sh[tile.var] = st["regs"][t][tile.reg]
        _advance(st, t)
    elif op == "atomic_add":
        sh[tile.var] += tile.k
        _advance(st, t)
    elif op == "set":
        sh[tile.var] = tile.value
        _advance(st, t)
    elif op == "nop":
        if tile.effect == "insert":
            if sh["items"] >= sh["N"] and not sh["err"]:
                sh["err"] = "it inserted into a full buffer"
            sh["items"] += 1
        elif tile.effect == "remove":
            if sh["items"] <= 0 and not sh["err"]:
                sh["err"] = "it removed from an empty buffer"
            sh["items"] -= 1
        if tile.needs and not sh["ev"].get(tile.needs) and not sh["err"]:
            sh["err"] = tile.needs_error
        if tile.emit:
            sh["ev"][tile.emit] = True
        _advance(st, t)
    elif op == "spin_while":
        if sh[tile.var] == tile.value:
            row.kind, row.note = SPIN, f"{who} spins"
        else:
            row.note = f"{who} sees free"
            _advance(st, t)
    elif op == "test_and_set":
        old = sh[tile.var]
        sh[tile.var] = 1
        if old == 1:
            row.kind, row.note = SPIN, "returned 1"
        else:
            row.kind, row.note = OK, f"returned 0, {who} in"
            _advance(st, t)
    elif op == "wait":
        s = st["sems"][tile.sem]
        if st["granted"][t]:
            st["granted"][t] = False
            row.kind, row.text, row.note = OK, f"{tile.text}, woken", f"{who} goes on"
            _advance(st, t)
        elif s["count"] > 0:
            s["count"] -= 1
            row.kind = OK
            _advance(st, t)
        else:
            s["queue"].append(t)
            st["status"][t] = BLOCKED
            row.kind, row.note = BLOCK, f"{who} blocks"
    elif op == "signal":
        q = st["sems"][tile.sem]
        if q["queue"]:
            w = q["queue"].pop(0)
            st["status"][w] = READY
            st["granted"][w] = True
            row.note = f"wakes {level.threads[w]}"
        else:
            q["count"] += 1
        _advance(st, t)
    else:                                   # the loader refuses this; belt and braces
        raise LevelError(f"tile '{tile.id}' has an unknown op '{op}'")
    return st, row


# --------------------------------------------------------------------------- #
# words: the state column, the outcome, the wrong note
# --------------------------------------------------------------------------- #
_PLACEHOLDER = re.compile(r"\{(sem:)?([A-Za-z_][A-Za-z0-9_]*)\}")


def _show(v) -> str:
    return UNSET if v is None else str(v)


def _fill(level: Level, template: str, st: dict, t: int) -> str:
    def sub(m):
        if m.group(1):
            return sem_show(st, m.group(2))
        name = m.group(2)
        if name == "reg":
            reg = level.registers[t] if t < len(level.registers) else None
            if reg is None:
                return UNSET
            return f"{reg.short} = {_show(st['regs'][t].get(reg.name))}"
        return _show(st["shared"].get(name))
    return _PLACEHOLDER.sub(sub, template)


def state_text(level: Level, st: dict, t: int) -> str:
    """The shared-state column after thread `t` moved, with each semaphore's extra appended
    only when the code uses it (a level without a lock does not show one)."""
    s = _fill(level, level.state, st, t)
    for sem, extra in level.state_extra.items():
        if uses(level, st["code"], sem):
            s += _fill(level, extra, st, t)
    return s


def outcome_label(level: Level, st: dict) -> str:
    """What a finished run is called. A run that recorded an error is "finishes, but <error>"
    on every level; that rule is the engine's, not the level file's."""
    if st["shared"]["err"]:
        return "finishes, but " + st["shared"]["err"]
    return _fill(level, level.outcome, st, 0)


def is_correct(level: Level, st: dict) -> bool:
    if st["shared"]["err"]:
        return False
    return all(st["shared"].get(k) == v for k, v in level.correct_when.items())


def wrong_note(level: Level, st: dict) -> str:
    wn = level.wrong_note
    if wn is None:
        return ""
    value = st["shared"].get(wn.by)
    return str(wn.notes.get(value, wn.notes.get(str(value), f"{_show(value)}: wrong")))


# --------------------------------------------------------------------------- #
# replay: a schedule into the notes' trace
# --------------------------------------------------------------------------- #
@dataclass
class Replay:
    st: dict
    rows: list
    path: list                 # [(i, j)] through the progress graph, (0, 0) first
    done: bool
    deadlock: bool

    @property
    def finished(self) -> bool:
        return self.done or self.deadlock


def replay(level: Level, code, moves, start: int = 0) -> Replay:
    """Fold `step` over a schedule from the initial state, marking the row where things went
    wrong with the dashed outline (`bad`): the first step that makes both inside, the step that
    records the first error, the last step of a wrong finished run, the last step of a stuck run.
    The UI keeps only the schedule and replays it on every change, so Undo is a pop."""
    st = init(level, code, start)
    rows: list = []
    path = [(0, 0)]
    was_both = was_err = False
    for t in moves:
        st, row = step(level, st, t)
        row.state = state_text(level, st, t)
        both = both_inside(level, st)
        if both and not was_both:
            row.bad = True
            row.state += ": both inside"
        if st["shared"]["err"] and not was_err:
            row.bad = True
            row.state = st["shared"]["err"]
        was_both, was_err = both, bool(st["shared"]["err"])
        rows.append(row)
        path.append((st["pc"][0], st["pc"][1]))
    done, stuck = is_done(st), is_stuck(level, st)
    if rows:
        last = rows[-1]
        if done and not is_correct(level, st) and level.wrong_note and not st["shared"]["err"]:
            last.bad = True
            last.state = wrong_note(level, st)
        if stuck:
            last.bad = True
            last.state = state_text(level, st, last.t) + ": deadlock"
    return Replay(st=st, rows=rows, path=path, done=done, deadlock=stuck)


# --------------------------------------------------------------------------- #
# explore: every schedule
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Outcome:
    label: str
    correct: bool
    moves: tuple               # a shortest schedule reaching it
    paths: int = 0             # schedules ending here, when the level counts them
    states: int = 0


@dataclass(frozen=True)
class Exploration:
    states: int                # reachable states visited
    total: int | None          # schedules in all, when the level counts them
    deadlock_paths: int
    outcomes: tuple            # Outcome, correct first, then by frequency, then by label
    points: frozenset          # reachable (i, j)
    stuck_points: frozenset    # (i, j) where some schedule is stuck
    certified: bool
    deadlock: tuple | None     # a shortest stuck schedule
    violation: tuple | None    # a shortest schedule with both inside
    capped: bool = False       # hit EXPLORE_CAP: nothing below is a proof

    def outcome(self, label: str) -> Outcome | None:
        return next((o for o in self.outcomes if o.label == label), None)


def explore(level: Level, code, start: int = 0) -> Exploration:
    """Breadth-first over the states `code` can reach, skipping moves that change nothing.

    Breadth-first is what makes the first schedule found for each outcome, for stuck and for
    both-inside a SHORTEST one — which is what "Show me" loads. Schedules are counted only on a
    level that asks (`count_schedules`): a level with spinning has no finite count.
    """
    s0 = init(level, code, start)
    k0 = _key(s0)
    seen = {k0: s0}
    parent: dict = {k0: None}
    order = [k0]
    outcomes: dict = {}
    points, stuck_points = set(), set()
    deadlocks: list = []
    violations: list = []
    qi = 0
    while qi < len(order) and qi < EXPLORE_CAP:
        k = order[qi]
        qi += 1
        st = seen[k]
        points.add((st["pc"][0], st["pc"][1]))
        if both_inside(level, st):
            violations.append(k)
        if is_done(st):
            label = outcome_label(level, st)
            o = outcomes.get(label)
            if o is None:
                o = outcomes[label] = {"label": label, "first": k,
                                       "correct": is_correct(level, st), "states": 0, "paths": 0}
            o["states"] += 1
            continue
        if is_stuck(level, st):
            deadlocks.append(k)
            stuck_points.add((st["pc"][0], st["pc"][1]))
            continue
        for t in enabled(st):
            nx = step(level, st, t)[0]
            nk = _key(nx)
            if nk == k or nk in seen:
                continue
            seen[nk] = nx
            parent[nk] = (k, t)
            order.append(nk)
    capped = qi < len(order)

    def moves_to(k: str) -> tuple:
        m: list = []
        while parent[k] is not None:
            pk, t = parent[k]
            m.append(t)
            k = pk
        return tuple(reversed(m))

    total = None
    dl_paths = 0
    if level.count_schedules and not capped:
        memo: dict = {}

        def count(k: str) -> dict:
            if k in memo:
                return memo[k]
            st = seen[k]
            res: dict = {}
            if is_done(st):
                res[outcome_label(level, st)] = 1
            elif is_stuck(level, st):
                res["deadlock"] = 1
            else:
                for t in enabled(st):
                    nk = _key(step(level, st, t)[0])
                    if nk == k:
                        continue
                    for label, n in count(nk).items():
                        res[label] = res.get(label, 0) + n
            memo[k] = res
            return res

        by_label = count(k0)
        total = 0
        for label, n in by_label.items():
            if label in outcomes:
                outcomes[label]["paths"] = n
            total += n
        dl_paths = by_label.get("deadlock", 0)

    listed = [Outcome(label=o["label"], correct=o["correct"], moves=moves_to(o["first"]),
                      paths=o["paths"], states=o["states"]) for o in outcomes.values()]
    listed.sort(key=lambda o: (not o.correct, -o.paths, o.label))
    certified = (bool(listed) and all(o.correct for o in listed) and not deadlocks
                 and not violations and not capped)
    return Exploration(
        states=len(order), total=total, deadlock_paths=dl_paths, outcomes=tuple(listed),
        points=frozenset(points), stuck_points=frozenset(stuck_points), certified=certified,
        deadlock=moves_to(deadlocks[0]) if deadlocks else None,
        violation=moves_to(violations[0]) if violations else None, capped=capped)


# --------------------------------------------------------------------------- #
# prove: from every starting state
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Proof:
    """Try every schedule, from every start the level has. `proved` only when every start is
    certified; `reason` is why not, in the order the face reports it: nothing finishes, a
    deadlock, both inside, a wrong answer. `failing_start` is the first start that failed."""
    proved: bool
    reason: str
    results: tuple             # (start index, Exploration) in start order
    failing_start: int | None = None

    def at(self, start: int) -> Exploration:
        return next(r for i, r in self.results if i == start)


def why_not(level: Level, r: Exploration) -> str:
    """The first reason an exploration is not a proof, in words a student can act on."""
    if r.capped:
        return f"GINI stopped after {EXPLORE_CAP} states; this code is too big to prove here"
    if not r.outcomes:
        return "no schedule finishes"
    if r.deadlock is not None:
        return "a schedule deadlocks"
    if r.violation is not None:
        return f"a schedule has {level.mutex_check} at once"
    if any(not o.correct for o in r.outcomes):
        return "a schedule gives the wrong answer"
    return ""


def prove(level: Level, code) -> Proof:
    starts = range(len(level.starts)) if level.starts else (0,)
    results = tuple((s, explore(level, code, s)) for s in starts)
    for s, r in results:
        if not r.certified:
            return Proof(proved=False, reason=why_not(level, r), results=results,
                         failing_start=s)
    return Proof(proved=True, reason="", results=results)


# --------------------------------------------------------------------------- #
# goals
# --------------------------------------------------------------------------- #
def reach_met(level: Level, goal: Goal, st: dict) -> bool:
    """Whether a `reach` goal holds in one state."""
    r = goal.reach
    if r == "deadlock":
        return is_stuck(level, st)
    if r == "both_inside":
        return both_inside(level, st)
    if isinstance(r, dict):
        if r.get("finished") and not is_done(st):
            return False
        return all(st["shared"].get(k) == v for k, v in (r.get("shared") or {}).items())
    return False


def goals_reached(level: Level, version_id: str, code, moves, start: int = 0) -> tuple:
    """The ids of the level's `reach` goals for `version_id` that hold after some step of
    `moves`. Checked after every step, not only the last: a student who finds the deadlock and
    then presses Undo has still found it."""
    goals = [g for g in level.goals if g.reach is not None and g.version == version_id]
    if not goals:
        return ()
    st = init(level, code, start)
    met: list = []
    for t in moves:
        st = step(level, st, t)[0]
        for g in goals:
            if g.id not in met and reach_met(level, g, st):
                met.append(g.id)
    return tuple(met)


# --------------------------------------------------------------------------- #
# fix it
# --------------------------------------------------------------------------- #
def can_move(level: Level, code, t: int, i: int, direction: int) -> bool:
    """Whether line `i` of thread `t` may swap with its neighbour in `direction` (-1 up, +1
    down): refused at either end, refused when either tile is pinned."""
    if level.fix is None:
        return False
    th = code[t]
    j = i + direction
    if not (0 <= i < len(th)) or not (0 <= j < len(th)):
        return False
    return th[i] not in level.fix.pinned and th[j] not in level.fix.pinned


def move(level: Level, code, t: int, i: int, direction: int) -> tuple:
    """The code with line `i` of thread `t` swapped with its neighbour, or the code unchanged
    when the move is refused. Always a new tuple; nothing is edited in place."""
    code = tuple(tuple(th) for th in code)
    if not can_move(level, code, t, i, direction):
        return code
    th = list(code[t])
    j = i + direction
    th[i], th[j] = th[j], th[i]
    out = list(code)
    out[t] = tuple(th)
    return tuple(out)


# --------------------------------------------------------------------------- #
# loading a level file
# --------------------------------------------------------------------------- #
def _refuse(cond: bool, sentence: str) -> None:
    if cond:
        raise LevelError(sentence)


def _code(raw, where: str, tiles: dict) -> tuple:
    _refuse(not isinstance(raw, list) or len(raw) != 2,
            f"{where} must list the code of exactly two threads")
    out = []
    for th in raw:
        th = list(th or [])
        for tid in th:
            _refuse(str(tid) not in tiles, f"{where} uses a tile '{tid}' that is not defined")
        out.append(tuple(str(t) for t in th))
    return tuple(out)


def _level_from_dict(d: dict, n: int) -> Level:
    lid = str(d.get("id") or "")
    _refuse(not lid, f"level {n} has no id")
    where = f"level '{lid}'"
    threads = tuple(str(t) for t in (d.get("threads") or ()))
    _refuse(len(threads) != 2, f"{where} must name exactly two threads")

    sems = {str(k): int(v) for k, v in (d.get("semaphores") or {}).items()}
    notes = {str(k): str(v) for k, v in (d.get("semaphore_notes") or {}).items()}
    for name in notes:
        _refuse(name not in sems, f"{where} has a note for a semaphore '{name}' it does not declare")
    extra = {str(k): str(v) for k, v in (d.get("state_extra") or {}).items()}
    for name in extra:
        _refuse(name not in sems,
                f"{where} shows a semaphore '{name}' it does not declare")

    tiles: dict = {}
    for tid, raw in (d.get("tiles") or {}).items():
        tid = str(tid)
        raw = raw or {}
        op = str(raw.get("op") or "")
        _refuse(op not in OPS, f"{where}, tile '{tid}': '{op}' is not an op the engine knows")
        sem = str(raw.get("sem") or "")
        _refuse(op in ("wait", "signal") and not sem,
                f"{where}, tile '{tid}': {op} needs a semaphore")
        _refuse(bool(sem) and sem not in sems,
                f"{where}, tile '{tid}' names a semaphore '{sem}' that is not declared")
        needs = raw.get("needs") or {}
        _refuse(bool(needs) and not (needs.get("event") and needs.get("error")),
                f"{where}, tile '{tid}': needs must give an event and an error")
        tiles[tid] = Tile(
            id=tid, text=str(raw.get("text") or tid), op=op,
            var=str(raw.get("var") or ""), reg=str(raw.get("reg") or ""),
            k=int(raw.get("k") or 0), value=raw.get("value"), sem=sem,
            cs=bool(raw.get("cs")), mark=str(raw.get("mark") or ""),
            effect=str(raw.get("effect") or ""), emit=str(raw.get("emit") or ""),
            needs=str(needs.get("event") or ""), needs_error=str(needs.get("error") or ""))
    _refuse(not tiles, f"{where} defines no tiles")

    versions = []
    for raw in d.get("versions") or []:
        raw = raw or {}
        vid = str(raw.get("id") or "")
        _refuse(not vid or vid == "fix", f"{where} has a version with no id (and 'fix' is reserved)")
        versions.append(Version(id=vid, label=str(raw.get("label") or vid),
                                code=_code(raw.get("code"), f"{where}, version '{vid}'", tiles),
                                proves=str(raw.get("proves") or "")))
    _refuse(not versions, f"{where} has no versions")
    ids = [v.id for v in versions]
    _refuse(len(set(ids)) != len(ids), f"{where} has two versions with the same id")
    default = str(d.get("default") or ids[0])
    _refuse(default not in ids, f"{where} defaults to a version '{default}' it does not have")

    fix = None
    if d.get("fix"):
        raw = d["fix"]
        from_ = str(raw.get("from") or "")
        _refuse(from_ not in ids, f"{where}: fix starts from a version '{from_}' it does not have")
        pinned = tuple(str(p) for p in (raw.get("pinned") or ()))
        for p in pinned:
            _refuse(p not in tiles, f"{where}: fix pins a tile '{p}' that is not defined")
        fix = Fix(from_version=from_, pinned=pinned, proves=str(raw.get("proves") or ""))

    starts = []
    for raw in d.get("starts") or []:
        raw = raw or {}
        label = str(raw.get("label") or f"start {len(starts) + 1}")
        ssems = {str(k): int(v) for k, v in (raw.get("semaphores") or {}).items()}
        for name in ssems:
            _refuse(name not in sems,
                    f"{where}, start '{label}' sets a semaphore '{name}' that is not declared")
        starts.append(Start(label=label, shared=dict(raw.get("shared") or {}), semaphores=ssems))

    goals = []
    for raw in d.get("goals") or []:
        raw = raw or {}
        gid = str(raw.get("id") or "")
        _refuse(not gid, f"{where} has a goal with no id")
        gv = str(raw.get("version") or "")
        _refuse(gv not in ids and not (gv == "fix" and fix is not None),
                f"{where}, goal '{gid}' is on a version '{gv}' the level does not have")
        reach, prove_ = raw.get("reach"), bool(raw.get("prove"))
        _refuse((reach is None) == (not prove_),
                f"{where}, goal '{gid}' must say either what to reach or that it is a proof")
        _refuse(reach is not None and reach not in ("deadlock", "both_inside")
                and not isinstance(reach, dict),
                f"{where}, goal '{gid}' reaches something the engine cannot check")
        _refuse(reach == "both_inside" and not d.get("mutex_check"),
                f"{where}, goal '{gid}' asks for both inside on a level without mutex_check")
        goals.append(Goal(id=gid, version=gv, chip=str(raw.get("chip") or gid),
                          text=str(raw.get("text") or ""), reach=reach, prove=prove_))

    registers = tuple(Register(name=str(r.get("name")), short=str(r.get("short") or r.get("name")))
                      for r in (d.get("registers") or []) if r and r.get("name"))
    _refuse(len(registers) > 2, f"{where} gives registers to more than two threads")
    wn = None
    if d.get("wrong_note"):
        raw = d["wrong_note"]
        wn = WrongNote(by=str(raw.get("by") or ""),
                       notes={k: str(v) for k, v in (raw.get("notes") or {}).items()})
    return Level(
        id=lid, n=n, title=str(d.get("title") or lid), source=str(d.get("source") or ""),
        setup=str(d.get("setup") or ""), asks=str(d.get("asks") or ""),
        hint=str(d.get("hint") or ""), threads=threads,
        state_head=str(d.get("state_head") or ""),
        shade_both_inside=bool(d.get("shade_both_inside")),
        count_schedules=bool(d.get("count_schedules")),
        mutex_check=str(d.get("mutex_check") or ""),
        shared=dict(d.get("shared") or {}), registers=registers, semaphores=sems,
        semaphore_notes=notes, starts=tuple(starts), tiles=tiles, versions=tuple(versions),
        default=default, fix=fix, state=str(d.get("state") or ""), state_extra=extra,
        outcome=str(d.get("outcome") or ""), correct_when=dict(d.get("correct_when") or {}),
        wrong_note=wn, goals=tuple(goals))


def from_dict(d: dict) -> LevelFile:
    d = d or {}
    lab_id = str(d.get("id") or "")
    _refuse(not lab_id, "the level file has no id")
    levels = tuple(_level_from_dict(raw or {}, i + 1) for i, raw in enumerate(d.get("levels") or []))
    ids = [lv.id for lv in levels]
    _refuse(len(set(ids)) != len(ids), f"'{lab_id}' has two levels with the same id")
    return LevelFile(id=lab_id, title=str(d.get("title") or lab_id), levels=levels,
                     summary=str(d.get("summary") or ""), hue=str(d.get("hue") or ""),
                     released=bool(d.get("released", True)))


def from_yaml(text: str) -> LevelFile:
    return from_dict(yaml.safe_load(text) or {})


def load(path) -> LevelFile:
    return from_yaml(Path(path).read_text(encoding="utf-8"))


def catalog() -> tuple:
    """Every T-Lab shipped with gini-core, in filename order (`t-lab-02`, `t-lab-03`, …).

    Unlike `lab_spec.catalog`, a file that does not load is NOT skipped quietly: a level file is
    the whole lab, and a lab that silently vanished from the hub is the failure the loader's
    one-sentence refusals exist to prevent. It raises, and the test suite loads every shipped
    file.
    """
    # Only the scheduler labs: T-Lab 03 and 04 have engines of their own (`tlab_catalog`), and
    # this loader would refuse their level files. The Teaching Center replays `schedule` entries
    # through `get`, so it must keep finding exactly the files this engine can read.
    from .tlab_catalog import files
    return tuple(load(p) for p in files("sched"))


def get(lab_id: str) -> LevelFile | None:
    return next((lab for lab in catalog() if lab.id == lab_id), None)
