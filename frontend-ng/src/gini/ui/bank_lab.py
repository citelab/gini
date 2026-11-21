"""T-Lab 03 — Simulate deadlock handling. The face over `gini.domain.banklab`.

T-Lab 02 made students find deadlocks by picking the schedule; this lab is the other half of the
chapter, what the operating system does about them. On the run levels the student picks which
process moves next and watches the resource-allocation graph the notes draw (Figure 14.1) grow a
cycle, reduce, or knot; on the bank level the student IS the banker and runs the safety check by
hand, one row at a time, in the notes' own columns. Try every order does for requests what Try
every schedule did for instructions.

Two halves, and the seam is the point — the same split as `sched_lab`:

* **The top half is a view model with no Qt in it.** `proc_rows`, `graph_view`, `trace`,
  `reduce_view`, `recover_view`, `orders_view`, `bank_table`, `test_lines`, `check_view` are pure
  functions from the engine's state to what is drawn: which words a process's pill says, which
  edges are thick, which circles are filled, what each check row reads. `Session` holds what the
  student chose — level, version, start, the moves so far, the request picked and the check rows
  run — and nothing else. Everything a behaviour test wants to assert is reachable without a
  widget.
* **The bottom half draws it.** One window, 940 × 700, the size T-Lab 02 and the Machine Lab's
  own pages open at. The graph is painted (`_GraphView`) exactly as the notes draw it: a circle
  per process, a box per resource with one dot per unit, claim edges dotted, request edges
  dashed, allocation edges solid and leaving from the dot of the unit held. Positions come from
  the level file's `layout`. The layout of the window, its labels and its wording follow the
  design mockup the professor approved (`docs/design/t-lab-03-reference/prototype.html`).

**Colour is never the only signal.** Every process state is also a word — *active*, *blocked*,
*deadlocked*, *done*, *terminated*, *waits — would close a cycle* — on the graph and in the
process pill; an edge on a cycle is thicker as well as part of a listed cycle in the Reduce tab;
the check's answers say *yes* and *no*; a provisional change is written `2 → 3`, not only tinted.

**No worker thread, by design.** Try every order on a shipped level visits at most a few hundred
states and runs on the GUI thread in a few milliseconds, as T-Lab 02's does, so the hazard in
known issue 17 cannot arise. `explore` stops at `EXPLORE_CAP` and says so if a later level ever
explodes; read that issue before moving exploration off the GUI thread.

**What the chain is told.** One entry per goal per level, the first time it is reached — never per
click: `allocate` on a run level (the order of moves, replayable by `banklab.replay`), `bank` on
the bank level (the request, the hand-run check rows, the decision and GINI's verdict), and
`orders` on every Try every order of a version that can be proved, held or not.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ..domain import banklab as B

# --------------------------------------------------------------------------- #
# the view model — no Qt
# --------------------------------------------------------------------------- #
#: What a Random press will do before giving up. Every shipped level finishes or sticks well
#: inside it; the cap is there so a later level cannot wedge the face.
RANDOM_CAP = 40

#: The Every order tab before the student has pressed the button.
GUESS_FIRST = ("Make your guess first. Then press Try every order: GINI runs every order in "
               "which these processes could ask, finish and wait, from this start.")

#: What the graph card says while the graph is hidden.
GRAPH_HIDDEN = ("The graph is hidden. Draw it on paper from the Tables tab: a circle for each "
                "process, a box for each resource with a dot for each unit, a dashed arrow for "
                "each request and a solid arrow for each unit held. Then show it and compare.")

PICK_REQUEST = "Pick a request on the left, or No request to check the state as it is."

NO_REQUEST = "No request: is this state safe?"


def claims_drawn(level: B.Level) -> bool:
    """Claim edges are drawn on a level that has an avoidance version, under every version, so
    the student sees what the graph algorithm sees whichever policy is on."""
    return level.claims is not None and any(v.policy in ("graph", "banker")
                                            for v in level.versions)


def wait_for_offered(level: B.Level, w: B.Level) -> bool:
    """The RAG / wait-for switch: on the detect-and-recover level (one whose starts declare
    recovery), and only from a start where every resource has one unit — the wait-for graph of
    Figure 14.9 is a faithful picture of the state only then."""
    rec = bool(level.recovery) or any(s.world.get("recovery") for s in level.starts)
    return rec and w.mode == "run" and all(u == 1 for u in w.units)


def recovery_offered(w: B.Level) -> bool:
    return bool(w.recovery)


def _refused_words(version: B.Version) -> str:
    return {"graph": "would close a cycle", "banker": "would be unsafe"}.get(version.policy,
                                                                          "refused")


@dataclass(frozen=True)
class MoveButton:
    p: int
    kind: str
    label: str
    ask: bool                  # a request (drawn with the accent border) rather than a finish


@dataclass(frozen=True)
class ProcRow:
    name: str
    status: str                # running · waits for R2 · waits: would close a cycle · done …
    kind: str                  # active · blocked · refused · done · terminated
    moves: tuple


def proc_rows(w: B.Level, version: B.Version, st: dict) -> tuple:
    """One row per process: its pill and the buttons for its next step (`moves`). Recovery
    actions are not here — they live in the Recover tab with their cost beside them."""
    mvs = B.moves(w, st, include_kill=False)
    out = []
    for p, pn in enumerate(w.procs):
        if st["killed"][p]:
            status, kind = "terminated", "terminated"
        elif st["done"][p]:
            status, kind = "done", "done"
        elif B.blocked(st, p):
            if st["refused"][p]:
                status, kind = f"waits: {_refused_words(version)}", "refused"
            else:
                status, kind = f"waits for {B.names(w, st['pend'][p])}", "blocked"
        else:
            status, kind = "running", "active"
        btns = tuple(MoveButton(m.p, m.kind, m.label,
                                ask=m.kind == "step" and not m.label.endswith("finishes"))
                     for m in mvs if m.p == p)
        out.append(ProcRow(pn, status, kind, btns))
    return tuple(out)


@dataclass(frozen=True)
class GNode:
    name: str
    proc: bool                 # a circle (process) or a box (resource)
    x: float                   # 0..1, from the level file's layout
    y: float
    state: str                 # active · blocked · refused · deadlocked · done · terminated
    caption: str               # the word under a circle; "" for a box
    units: int = 0             # boxes: one dot per unit
    erased: int | None = None  # the Reduce tab: this process's place in the erase order


@dataclass(frozen=True)
class GEdge:
    kind: str                  # alloc · request · claim · wait
    a: str
    b: str
    count: int
    thick: bool                # on a cycle


@dataclass(frozen=True)
class GraphView:
    nodes: tuple
    edges: tuple
    ghost: tuple               # the cycle a refused grant would close (graph policy), or ()
    wait_for: bool
    aria: str


def _layout(w: B.Level) -> dict:
    """Positions from the level file; anything it does not place goes on a ring, so a level
    written without a layout still draws."""
    nodes = list(w.procs) + list(w.resources)
    out = {}
    for i, n in enumerate(nodes):
        if n in w.layout:
            out[n] = tuple(w.layout[n])
        else:
            a = 2 * math.pi * i / max(1, len(nodes))
            out[n] = (0.5 + 0.4 * math.cos(a), 0.5 + 0.4 * math.sin(a))
    return out


def _cycle_pairs(cyc) -> set:
    return {(cyc[i], cyc[i + 1]) for i in range(len(cyc) - 1)} if cyc else set()


def graph_view(level: B.Level, w: B.Level, version: B.Version, st: dict,
               erased: bool = False, wait: bool = False) -> GraphView:
    """The graph as the notes draw it. `erased` numbers the circles in the reduction's erase
    order (the Reduce tab); `wait` draws the wait-for graph instead (Figure 14.9)."""
    pos = _layout(w)
    dl = set(B.deadlocked(w, st))
    order = {}
    if erased:
        order = {r[0]: i for i, r in enumerate(B.reduce(w, st)[0])}
    nodes = []
    for p, pn in enumerate(w.procs):
        if wait and not B.active(st, p):
            continue
        if st["killed"][p]:
            state, cap = "terminated", "terminated"
        elif st["done"][p]:
            state, cap = "done", "done"
        elif p in dl:
            state, cap = "deadlocked", "deadlocked"
        elif B.blocked(st, p) and not wait:
            if st["refused"][p]:
                state, cap = "refused", f"waits — {_refused_words(version)}"
            else:
                state, cap = "blocked", "blocked"
        else:
            state, cap = "active", "active"
        x, y = pos[pn]
        nodes.append(GNode(pn, True, x, y, state, cap, erased=order.get(p)))
    if wait:
        es = B.wait_for(w, st)
        on = _cycle_pairs(B.has_cycle(es))
        edges = tuple(GEdge("wait", a, b, 1, (a, b) in on) for _, a, b, _ in es)
        aria = "Wait-for graph. " + ("; ".join(f"{e.a} waits for {e.b}" for e in edges)
                                     or "Nobody waits.")
        return GraphView(tuple(nodes), edges, (), True, aria)
    for r, rn in enumerate(w.resources):
        x, y = pos[rn]
        nodes.append(GNode(rn, False, x, y, "", "", units=w.units[r]))
    es = B.edges(w, st, claims_drawn(level))
    on = _cycle_pairs(B.has_cycle(B.edges(w, st, False)))
    edges = tuple(GEdge(k, a, b, n, k != "claim" and (a, b) in on) for k, a, b, n in es)
    ghost: tuple = ()
    if version.policy == "graph":
        ref = next((p for p in range(len(w.procs)) if st["refused"][p] and B.blocked(st, p)),
                   None)
        if ref is not None:
            t = B.clone(st)
            t["hold"][ref] = list(B.vadd(tuple(t["hold"][ref]), tuple(t["pend"][ref])))
            t["pend"][ref] = None
            ghost = tuple(B.has_cycle(B.edges(w, t, True)))
    words = {"alloc": "allocation", "request": "request", "claim": "claim"}
    aria = "Resource-allocation graph. " + ("; ".join(
        f"{words[e.kind]} {e.a} to {e.b}" for e in edges) or "No edges.")
    return GraphView(tuple(nodes), edges, ghost, False, aria)


def trace(w: B.Level, st: dict, lines) -> tuple:
    """(lines, end): the trace, one line per move, and how the run stands — (text, kind) with
    kind good or bad, or None while it is still going."""
    end = None
    if st["error"]:
        end = (f"Error: {st['error']}.", "bad")
    elif B.finished(st):
        end = (("Every remaining process finishes." if st["acts"] else "Everyone has finished."),
               "good")
    elif not B.moves(w, st, include_kill=False):
        dl = B.deadlocked(w, st)
        who = ", ".join(w.procs[p] for p in dl)
        end = ((f"Deadlock: nobody can move. {who} wait forever." if dl
                else "Nobody can move."), "bad")
    return tuple(lines), end


@dataclass(frozen=True)
class Tables:
    heads: tuple
    rows: tuple                # (name, cell, cell, …)
    avail: str
    order_note: str
    note: str


def tables(w: B.Level, st: dict) -> Tables:
    heads = ("", "Hold", "Request") + (("Max",) if w.claims is not None else ()) + \
        (("CPU",) if w.recovery else ())
    rows = []
    for p, pn in enumerate(w.procs):
        row = [pn, B.vshow(st["hold"][p]) if B.active(st, p) else "–",
               B.vshow(st["pend"][p]) if st["pend"][p] is not None else "–"]
        if w.claims is not None:
            row.append(B.vshow(w.claims[p]))
        if w.recovery:
            row.append(_mins(w.cpu[p] if w.cpu else 0))
        rows.append(tuple(row))
    note = "Hold is what each process has; Request is what it is waiting for now." + \
        (" Max is its declared claim." if w.claims is not None else "")
    return Tables(heads, tuple(rows), B.vshow(B.avail(w, st)), _order_note(w), note)


def _order_note(w: B.Level) -> str:
    return f"Vectors list {', '.join(w.resources)}." if w.m > 1 else ""


def _mins(m: int) -> str:
    return f"{m // 60} h" if m >= 60 and m % 60 == 0 else f"{m} min"


@dataclass(frozen=True)
class ReduceView:
    cycles: tuple              # "P1 → R1 → P3 → R2 → P1"
    intro: str
    rows: tuple                # (n, name, "request ≤ Work", "Work after")
    left: tuple                # names
    box: str
    box_kind: str              # ok · bad
    order_note: str
    note: str


def reduce_view(w: B.Level, st: dict) -> ReduceView:
    cyc = tuple(" → ".join(c) for c in B.all_cycles(B.edges(w, st, False)))
    rows, left = B.reduce(w, st)
    out = tuple((i + 1, w.procs[p], f"{B.vshow(req)} ≤ {B.vshow(wk)}", B.vshow(after))
                for i, (p, req, wk, after) in enumerate(rows))
    names = tuple(w.procs[p] for p in left)
    if names:
        box = (f"Nobody else can be erased: {', '.join(names)} "
               f"{'are' if len(names) > 1 else 'is'} deadlocked.")
    else:
        box = "Everyone is erased: no deadlock."
    intro = ("Graph reduction (§14.3.1): find a process whose request fits in Work, erase it, "
             f"and give back what it holds. Work starts at Avail = {B.vshow(B.avail(w, st))}.")
    note = "The numbers on the graph are the erase order." + (
        " A process that was rolled back counts as asking for everything it must take again."
        if w.recovery else "")
    return ReduceView(cyc, intro, out, names, box, "bad" if names else "ok", _order_note(w), note)


@dataclass(frozen=True)
class RecoverRow:
    name: str
    facts: str
    moves: tuple               # MoveButton, labelled without the process name


@dataclass(frozen=True)
class RecoverView:
    thrown: str                # "Work thrown away so far: 15 min (1 action)"
    note: str
    box: str                   # "" until the detector finds no deadlock
    box_kind: str              # ok · info
    rows: tuple


def recover_view(w: B.Level, st: dict, start_note: str = "") -> RecoverView:
    """The Recover tab (§14.7). Once the detector finds no deadlock it says so, and whether
    nobody could have got out for less — never how."""
    rm = B.recovery_moves(w, st)
    acts = len(st["acts"])
    thrown = f"Work thrown away so far: {_mins(st['cost'])}" + (
        f" ({acts} action{'s' if acts != 1 else ''})" if acts else "")
    note = (start_note if "preempt" in w.recovery else
            "Recovery here is by termination only: a terminated process loses all the CPU time "
            "it has used.")
    box, kind = "", "info"
    if acts and not B.deadlocked(w, st):
        best = B.min_recovery(w, st["base"])[0]
        if st["cost"] == best:
            box, kind = "The detector finds no deadlock now. Nobody could have got out for less.", \
                "ok"
        else:
            box = ("The detector finds no deadlock now. There is a cheaper way out: Reset and "
                   "look again.")
    rows = []
    for p, pn in enumerate(w.procs):
        if not B.active(st, p):
            continue
        facts = [f"{_mins(w.cpu[p] if w.cpu else 0)} of CPU used"]
        if "rollback" in w.recovery and w.checkpoint and w.checkpoint[p] is not None:
            facts.append(f"checkpoint at minute {w.checkpoint[p]}")
        if "preempt" in w.recovery and w.took:
            for r, rn in enumerate(w.resources):
                if w.took[p][r] is not None and st["hold"][p][r]:
                    facts.append(f"took {rn} at minute {w.took[p][r]}")
        btns = tuple(MoveButton(m.p, m.kind, m.short or m.label, False) for m in rm if m.p == p)
        rows.append(RecoverRow(pn, " · ".join(facts), btns))
    return RecoverView(thrown, note, box, kind, tuple(rows))


@dataclass(frozen=True)
class OrderRow:
    label: str
    ok: bool
    steps: int
    moves: tuple


@dataclass(frozen=True)
class OrdersView:
    summary: str
    rows: tuple
    verdict: str               # proved · not_yet · ""
    verdict_text: str


def orders_view(version: B.Version, ex: B.Exploration) -> OrdersView:
    summary = f"GINI tried every order of these steps: {ex.states} states."
    if ex.capped:
        summary = f"GINI stopped after {B.EXPLORE_CAP} states; what follows is not a proof."
    rows = tuple(OrderRow(o.label, o.ok, len(o.moves), o.moves) for o in ex.outcomes)
    verdict, text = "", ""
    if version.proves:
        if ex.proved:
            verdict, text = "proved", version.proves
        else:
            bad = next((o for o in ex.outcomes if not o.ok), None)
            verdict = "not_yet"
            text = f"Not proved: {bad.label if bad else ex.reason}."
    return OrdersView(summary, rows, verdict, text)


@dataclass(frozen=True)
class Chip:
    id: str
    text: str
    done: bool
    tooltip: str


def goal_chips(level: B.Level, achieved) -> tuple:
    out = []
    for g in level.goals:
        where = g.start
        if g.version and len(level.versions) > 1:
            where += ", " + level.version(g.version).label
        if g.kind == "prove":
            where += ", then Try every order"
        out.append(Chip(g.id, g.chip, f"{level.id}:{g.id}" in achieved, f"{g.text} ({where})"))
    return tuple(out)


def result_words(w: B.Level, goal: B.Goal, st: dict) -> str:
    """The `result` of an allocate entry: what the goal's state looked like, in a few words."""
    k = goal.kind
    if k == "deadlock":
        return "deadlock: " + ", ".join(w.procs[p] for p in B.deadlocked(w, st))
    if k == "cycle_no_deadlock":
        return "cycle, no deadlock"
    if k == "refused_free":
        p = next((p for p in range(len(w.procs)) if st["refused"][p] and B.blocked(st, p)), None)
        return "" if p is None else f"{w.procs[p]} waits though {B.names(w, st['pend'][p])} is free"
    if k == "all_done":
        return "everyone finishes"
    if k in ("cheapest_victim", "cheapest_recovery"):
        return f"no deadlock, {B.lost(st['cost'])}"
    return ""


def walk(w: B.Level, version: B.Version, start: int, path) -> tuple:
    """(states, lines) of an order of moves: the start state and the state after each move."""
    st = B.start_state(w, start)
    states, lines = [st], []
    for p, kind in path:
        mv = next((m for m in B.moves(w, st) if m.p == p and m.kind == kind), None)
        if mv is None:
            raise B.LevelError(f"move {w.procs[p]}/{kind} is not possible here")
        st, line = B.apply(w, version, st, mv)
        states.append(st)
        lines.append(line)
    return states, lines


def random_extend(w: B.Level, version: B.Version, start: int, moves, rng=None,
                  cap: int = RANDOM_CAP) -> tuple:
    """`moves` extended with random steps (never a recovery: those are choices with a cost)
    until the run finishes or sticks. It takes an `rng` so a test can seed it."""
    rng = rng or random.Random()
    moves = list(moves)
    st = walk(w, version, start, moves)[0][-1]
    for _ in range(cap):
        if st["error"] or B.finished(st):
            break
        mvs = B.moves(w, st, include_kill=False)
        if not mvs:
            break
        mv = rng.choice(mvs)
        st = B.apply(w, version, st, mv)[0]
        moves.append((mv.p, mv.kind))
    return tuple(moves)


# ---- the bank level ----------------------------------------------------------------------
@dataclass(frozen=True)
class Cell:
    text: str                  # "2", "2 → 3" or "?"
    changed: bool = False
    hidden: bool = False


@dataclass(frozen=True)
class BankTable:
    resources: tuple
    rows: tuple                # (name, max cells, hold cells, need cells)
    avail: str                 # "Avail = (2, 1, 3) → (2, 0, 3) (provisional)" or "Avail = ?"
    hidden: bool


def bank_table(w: B.Level, start: int, prov, shown: bool) -> BankTable:
    """Max, Hold and Need as the notes tabulate them (Worked example 14.7), a provisional change
    written `2 → 3`, Need and Avail as `?` until the student shows them."""
    a, hold, nd = B.bank_state(w, start)
    hidden = w.starts[start].hide_need and not shown

    def cells(base, new, hide=False):
        out = []
        for j, x in enumerate(base):
            if hide:
                out.append(Cell("?", hidden=True))
            elif new is not None and new[j] != x:
                out.append(Cell(f"{x} → {new[j]}", changed=True))
            else:
                out.append(Cell(str(x)))
        return tuple(out)

    rows = []
    for p, pn in enumerate(w.procs):
        rows.append((pn, cells(w.claims[p], None),
                     cells(hold[p], prov[1][p] if prov else None),
                     cells(nd[p], prov[2][p] if prov else None, hidden)))
    if hidden:
        avl = "Avail = ?"
    elif prov is not None and tuple(prov[0]) != tuple(a):
        avl = f"Avail = {B.vshow(a)} → {B.vshow(prov[0])} (provisional)"
    else:
        avl = f"Avail = {B.vshow(a)}"
    return BankTable(tuple(w.resources), tuple(rows), avl, hidden)


def request_labels(w: B.Level, start: int) -> tuple:
    """The requests list, "No request" first (index -1)."""
    return (NO_REQUEST,) + tuple(f"{w.procs[p]} requests {B.vshow(vec)}"
                                 for p, vec in w.starts[start].requests)


def test_lines(w: B.Level, start: int, sel, shown: bool) -> tuple:
    """The three tests of "a request arrives" as (text, answer) lines, answer "yes", "no" or "".
    GINI applies the first two; the third, the safety check, is the student's."""
    if sel is None:
        return (("Pick a request. GINI applies the first two tests; the safety check is yours.",
                 ""),)
    if sel == -1:
        return (("No request: run the safety check on the state as it is.", ""),)
    p, vec = w.starts[start].requests[sel]
    a, _hold, nd = B.bank_state(w, start)
    t = B.tests(w, start, p, vec)
    out = [(f"1. Req ≤ Need{p + 1}? {B.vshow(vec)} ≤ {B.vshow(nd[p]) if shown else 'Need'}:",
            "yes" if t["le_need"] else "no")]
    if t["le_need"]:
        out.append((f"2. Req ≤ Avail? {B.vshow(vec)} ≤ {B.vshow(a) if shown else 'Avail'}:",
                    "yes" if t["le_avail"] else "no"))
    if t["le_need"] and t.get("le_avail"):
        out.append((("3. Allocate provisionally (written 2 → 3 in the tables) and run the "
                     "safety check."), ""))
    else:
        out.append(("No safety check is needed: decide.", ""))
    return tuple(out)


@dataclass(frozen=True)
class CheckRow:
    name: str
    test: str                  # "(3, 1, 2) ≤ (2, 0, 3)"
    fits: bool
    result: str                # "finishes; Work = (4, 2, 5)" or ""


@dataclass(frozen=True)
class CheckView:
    work: str
    rows: tuple
    done: bool
    stuck: bool                # the student said "Nobody fits" and it was accepted
    can_try: tuple             # per process: may it still be tried
    box: str
    box_kind: str              # ok · bad · ""


def check_rows(w: B.Level, c: B.Check) -> tuple:
    return tuple(CheckRow(w.procs[p], f"{B.vshow(nd)} ≤ {B.vshow(wk)}", fits,
                          f"finishes; Work = {B.vshow(after)}" if fits else "")
                 for p, nd, wk, fits, after in c.rows)


def check_view(w: B.Level, c: B.Check, stuck: bool) -> CheckView:
    done = B.check_done(c)
    box, kind = "", ""
    if done:
        seq = ", ".join(w.procs[r[0]] for r in c.rows if r[3])
        box, kind = f"Safe: ⟨{seq}⟩ is a safe sequence.", "ok"
    elif stuck:
        box, kind = f"Unsafe: no unfinished process fits in Work = {B.vshow(c.work)}.", "bad"
    return CheckView(B.vshow(c.work), check_rows(w, c), done, stuck,
                     tuple(not f for f in c.fin), box, kind)


def verdict(w: B.Level, start: int, sel: int, choice: str) -> tuple:
    """(right, sentence) for a decision on request `sel`, judged against `banklab.tests`."""
    p, vec = w.starts[start].requests[sel]
    t = B.tests(w, start, p, vec)
    right = choice == t["verdict"]
    said = {"grant": "grant it", "wait": f"make {w.procs[p]} wait",
            "error": "call it an error"}[t["verdict"]]
    return right, (("Right: " if right else f"Not this time. The banker would {said}: ")
                   + t["why"] + ".")


@dataclass(frozen=True)
class SafeOrders:
    intro: str
    count: int
    sequences: tuple           # "⟨P2, P3, P1, P4⟩", at most 24
    more: bool


def safe_orders(w: B.Level, start: int, prov) -> SafeOrders:
    """Try every order on the bank level: every safe sequence of the state (after the
    provisional grant, when a request that passed tests 1 and 2 is picked)."""
    a, hold, nd = prov if prov is not None else B.bank_state(w, start)
    seqs = B.all_safe_sequences(a, hold, nd)
    intro = "Every order in which the processes could finish" + (
        ", after the provisional grant:" if prov is not None else ":")
    shown = tuple("⟨" + ", ".join(w.procs[p] for p in q) + "⟩" for q in seqs[:24])
    return SafeOrders(intro, len(seqs), shown, len(seqs) > 24)


@dataclass
class BankWork:
    """What the student has done on one bank start: the request picked (-1 is No request), the
    processes tried in the check, in order, and the decision. The `Check` itself is rebuilt from
    `picks`, so Undo is a pop."""
    sel: int | None = None
    picks: list = field(default_factory=list)
    stuck: bool = False
    msg: str = ""
    decided: str = ""
    verdict: str = ""
    right: bool = False


class Session:
    """What the student has chosen, and nothing Qt. One per window.

    Per level it remembers the version, the start and the side tab; per bank start, the work in
    progress. The moves and Try every order's result are NOT per level: switching level, version
    or start clears them, because an order belongs to exactly one (level, version, start).

    `on_event(kind, payload)` is told, once per goal per level, the first time it is reached
    ("allocate" or "bank"), and on every Try every order of a version that can be proved
    ("orders"). It is a callback and not a signal so this class stays importable without Qt.
    """

    def __init__(self, lab: B.LabFile, on_event=None) -> None:
        self.lab = lab
        self.on_event = on_event
        self.level_index = 0
        self._version: dict = {}
        self._start: dict = {}
        self._tab: dict = {}
        self.moves: tuple = ()
        self.achieved: set = set()
        self.hide_graph = False
        self.view = "rag"
        self._shown: set = set()
        self._bank: dict = {}
        self._ex = None              # (key, Exploration)
        self._walked = None          # (key, states, lines)

    # -- what is loaded ------------------------------------------------------ #
    @property
    def level(self) -> B.Level:
        return self.lab.levels[self.level_index]

    @property
    def start(self) -> int:
        return self._start.get(self.level.id, 0)

    @property
    def start_label(self) -> str:
        return self.level.starts[self.start].label

    @property
    def world(self) -> B.Level:
        return B.world(self.level, self.start)

    @property
    def mode(self) -> str:
        return self.level.mode

    @property
    def version_id(self) -> str:
        lv = self.level
        return self._version.get(lv.id) or (lv.versions[0].id if lv.versions else "")

    @property
    def version(self) -> B.Version:
        lv = self.level
        if not lv.versions:
            return B.Version("free", "", "free")
        return lv.version(self.version_id)

    def tabs(self) -> tuple:
        """(id, label) of the side tabs, which depend on the level and the start."""
        if self.mode == "bank":
            return (("check", "Safety check"), ("every", "Every order"))
        out = [("tables", "Tables"), ("reduce", "Reduce")]
        if recovery_offered(self.world):
            out.append(("recover", "Recover"))
        out.append(("every", "Every order"))
        return tuple(out)

    @property
    def tab(self) -> str:
        t = self._tab.get(self.level.id)
        ids = [k for k, _ in self.tabs()]
        return t if t in ids else ids[0]

    @tab.setter
    def tab(self, name: str) -> None:
        if name in [k for k, _ in self.tabs()]:
            self._tab[self.level.id] = name

    def _key(self) -> tuple:
        return (self.level.id, self.version_id, self.start)

    def goal_met(self, goal_id: str) -> bool:
        return f"{self.level.id}:{goal_id}" in self.achieved

    # -- choosing ------------------------------------------------------------ #
    def _clear(self) -> None:
        self.moves = ()
        self._ex = None

    def pick_level(self, index: int) -> None:
        if not (0 <= index < len(self.lab.levels)):
            return
        self.level_index = index
        self._clear()

    def pick_version(self, version_id: str) -> None:
        if version_id not in [v.id for v in self.level.versions]:
            return
        self._version[self.level.id] = version_id
        self._clear()

    def pick_start(self, index: int) -> None:
        if not (0 <= index < len(self.level.starts)):
            return
        self._start[self.level.id] = index
        self._clear()

    # -- run levels ---------------------------------------------------------- #
    def walked(self) -> tuple:
        """(states, lines) for the moves so far, memoised: a refresh asks several times."""
        k = (self._key(), self.moves)
        if self._walked is None or self._walked[0] != k:
            states, lines = walk(self.world, self.version, self.start, self.moves)
            self._walked = (k, states, lines)
        return self._walked[1], self._walked[2]

    def state(self) -> dict:
        return self.walked()[0][-1]

    def lines(self) -> tuple:
        return tuple(self.walked()[1])

    def options(self) -> list:
        """Every move possible now, recovery actions included."""
        if self.mode != "run":
            return []
        return B.moves(self.world, self.state())

    def _commit(self, moves) -> None:
        self.moves = tuple((int(p), str(k)) for p, k in moves)
        self._check_goals()

    def _check_goals(self) -> None:
        """A goal is met at any step of the order, not only the last, and is recorded with the
        moves up to the step that met it — so `banklab.replay` of the entry ends on that state."""
        lv, w = self.level, self.world
        states = self.walked()[0]
        for g in lv.goals:
            if g.kind in ("prove",) + B.BANK_KINDS:
                continue
            if g.start != self.start_label or g.version != self.version_id:
                continue
            k = f"{lv.id}:{g.id}"
            if k in self.achieved:
                continue
            i = next((i for i, st in enumerate(states) if B.reach_met(w, g, st)), None)
            if i is None:
                continue
            self.achieved.add(k)
            self._emit("allocate", {"version": self.version_id,
                                    "moves": [[p, kind] for p, kind in self.moves[:i]],
                                    "goal": g.id, "result": result_words(w, g, states[i])})

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event is None:
            return
        base = {"lab": self.lab.id, "level": self.level.id, "start": self.start}
        base.update(payload)
        self.on_event(kind, base)

    def play(self, p: int, kind: str) -> bool:
        """One move. False when it is not possible now — its button is not there then, but a
        test or a stale click can still ask."""
        if not any(m.p == p and m.kind == kind for m in self.options()):
            return False
        self._commit(self.moves + ((p, kind),))
        return True

    def undo(self) -> None:
        if self.mode == "bank":
            self._bank_undo()
        elif self.moves:
            self._commit(self.moves[:-1])

    def reset(self) -> None:
        if self.mode == "bank":
            self._bank.pop(self._bank_key(), None)
        else:
            self.moves = ()

    def can_undo(self) -> bool:
        if self.mode == "bank":
            b = self._bank.get(self._bank_key())
            return bool(b and (b.decided or b.stuck or b.picks))
        return bool(self.moves)

    def can_reset(self) -> bool:
        if self.mode == "bank":
            return self._bank_key() in self._bank
        return bool(self.moves)

    def can_random(self) -> bool:
        if self.mode != "run":
            return False
        st = self.state()
        return not st["error"] and not B.finished(st) and bool(
            B.moves(self.world, st, include_kill=False))

    def random(self, rng=None) -> None:
        if self.mode != "run":
            return
        self._commit(random_extend(self.world, self.version, self.start, self.moves, rng))

    def exploration(self) -> B.Exploration | None:
        if self._ex is not None and self._ex[0] == self._key():
            return self._ex[1]
        return None

    def show(self, moves) -> None:
        """Load an order from Try every order: an ordinary order afterwards, so Undo works."""
        self._commit(tuple(moves))

    def explore_all(self):
        """Try every order. On a run level: explore, prove the goals it proves, and tell the
        chain when the version can be proved. On the bank level: switch to the Every order tab,
        which lists every safe sequence of the state."""
        self._tab[self.level.id] = "every"
        if self.mode == "bank":
            return None
        lv, w, v = self.level, self.world, self.version
        ex = B.explore(w, v, self.start)
        self._ex = (self._key(), ex)
        if ex.proved:
            for g in lv.goals:
                if g.kind == "prove" and g.version == v.id and g.start == self.start_label:
                    self.achieved.add(f"{lv.id}:{g.id}")
        if v.proves:
            counts = B.count_orders(w, v, self.start)
            self._emit("orders", {
                "version": v.id, "ok": ex.proved, "reason": ex.reason,
                "summary": {"states": ex.states,
                            "outcomes": [[o.label, counts.get(o.label, 0)]
                                         for o in ex.outcomes]}})
        return ex

    # -- the bank level ------------------------------------------------------ #
    def _bank_key(self) -> tuple:
        return (self.level.id, self.start)

    @property
    def work(self) -> BankWork:
        return self._bank.setdefault(self._bank_key(), BankWork())

    def peek_work(self) -> BankWork:
        """The work on this start, without creating it (so Reset can stay disabled)."""
        return self._bank.get(self._bank_key()) or BankWork()

    @property
    def need_shown(self) -> bool:
        return not self.world.starts[self.start].hide_need or self._bank_key() in self._shown

    def toggle_need(self) -> None:
        k = self._bank_key()
        if k in self._shown:
            self._shown.discard(k)
        else:
            self._shown.add(k)

    def provisional(self):
        """(Avail, Hold, Need) the check runs on: the state as it is for No request, the
        provisional state for a request that passed tests 1 and 2, None otherwise."""
        b = self.peek_work()
        w, s = self.world, self.start
        if b.sel is None:
            return None
        if b.sel == -1:
            return B.bank_state(w, s)
        p, vec = w.starts[s].requests[b.sel]
        return B.tests(w, s, p, vec).get("provisional")

    def check(self) -> B.Check | None:
        prov = self.provisional()
        if prov is None:
            return None
        c = B.check_begin(*prov)
        for p in self.peek_work().picks:
            B.check_pick(c, p)
        return c

    def pick_request(self, sel: int) -> None:
        n = len(self.world.starts[self.start].requests)
        if not (-1 <= sel < n):
            return
        self._bank[self._bank_key()] = BankWork(sel=sel)

    def try_process(self, p: int) -> bool:
        c = self.check()
        b = self.work
        if c is None or b.stuck or B.check_done(c) or c.fin[p]:
            return False
        b.picks.append(p)
        b.msg = ""
        c = self.check()
        if B.check_done(c) and b.sel == -1:
            self._bank_goal("safe_sequence", c, "", check_view(self.world, c, False).box)
        return True

    def claim_stuck(self) -> bool:
        """"Nobody fits: unsafe" — accepted only when `check_stuck` is true; otherwise GINI says
        which process still fits."""
        c = self.check()
        b = self.work
        if c is None or B.check_done(c) or b.stuck:
            return False
        if B.check_stuck(c):
            b.stuck, b.msg = True, ""
            return True
        still = next(i for i in range(len(c.fin)) if not c.fin[i] and B.vle(c.need[i], c.work))
        b.msg = f"Not yet: {self.world.procs[still]} still fits in Work = {B.vshow(c.work)}."
        return False

    def decide(self, choice: str) -> None:
        b = self.work
        if b.sel is None or b.sel < 0 or choice not in ("grant", "wait", "error"):
            return
        w, s = self.world, self.start
        right, text = verdict(w, s, b.sel, choice)
        b.decided, b.verdict, b.right = choice, text, right
        if not right:
            return
        p, vec = w.starts[s].requests[b.sel]
        t = B.tests(w, s, p, vec)
        c = self.check()
        if choice == "grant" and c is not None and B.check_done(c):
            self._bank_goal("grant", c, choice, text)
        if choice == "wait" and t["why"] == "the state would be unsafe" and b.stuck:
            self._bank_goal("wait_unsafe", c, choice, text)

    def _bank_goal(self, kind: str, c: B.Check, decision: str, said: str) -> None:
        lv, w = self.level, self.world
        b = self.work
        for g in lv.goals:
            if g.kind != kind or g.start != self.start_label:
                continue
            k = f"{lv.id}:{g.id}"
            if k in self.achieved:
                continue
            self.achieved.add(k)
            req = None
            if b.sel is not None and b.sel >= 0:
                p, vec = w.starts[self.start].requests[b.sel]
                req = (p, tuple(vec))
            rows = [[r.name, r.test, "yes" if r.fits else "no", r.result]
                    for r in check_rows(w, c)] if c is not None else []
            self._emit("bank", {"request": req, "rows": rows, "decision": decision,
                                "verdict": said, "goal": g.id})

    def _bank_undo(self) -> None:
        b = self._bank.get(self._bank_key())
        if b is None:
            return
        if b.decided:
            b.decided, b.verdict, b.right = "", "", False
        elif b.stuck:
            b.stuck = False
        elif b.picks:
            b.picks.pop()
        b.msg = ""


# --------------------------------------------------------------------------- #
# the face
# --------------------------------------------------------------------------- #
try:
    from PySide6.QtCore import QPointF, QRectF, QSize, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
    from PySide6.QtWidgets import (
        QDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
    _QT = True
except Exception:                                   # noqa: BLE001 — the view model needs no Qt
    _QT = False

if _QT:
    from .theme import ThemeManager
    from .theme.manager import scale_css as _scss
    from .windowing import standalone

    SIDE_W = 300
    MONO = "ui-monospace,Menlo,Consolas,'DejaVu Sans Mono',monospace"

    @dataclass
    class _Ink:
        """The colours, resolved once per theme. On the light themes these are the mockup's,
        which are the notes' (navy allocation, orange request, grey claim); on a dark theme the
        same meanings are taken from the mockup's dark palette and the theme's tokens."""
        alloc: str
        req: str
        claim: str
        blocked: str
        blocked_bg: str
        active: str
        active_bg: str
        ok: str
        ok_bg: str
        ok_ink: str
        warn: str
        warn_bg: str
        warn_ink: str
        bad_ink: str
        res_bg: str
        res_line: str
        done: str
        text: str
        text2: str
        muted: str
        faint: str
        line: str
        line2: str
        panel: str
        panel2: str
        bg: str
        accent: str
        accent_ink: str
        accent_soft: str
        head: str

    def _ink(t) -> _Ink:
        common = {"text": t.text, "muted": t.muted, "faint": t.faint, "line": t.line,
                  "line2": t.line2, "panel": t.panel, "panel2": t.panel2, "bg": t.bg,
                  "accent": t.accent}
        if not t.dark:
            return _Ink(alloc="#1f3a8a", req="#d9731c", claim="#7b8591", blocked="#c62f2f",
                        blocked_bg="#fbe4e4", active="#2560c8", active_bg="#e6eefc",
                        ok="#23863a", ok_bg="#e1f2e4", ok_ink="#14532d", warn="#b8741a",
                        warn_bg="#fbeed9", warn_ink="#6b4208", bad_ink="#8f1d1d",
                        res_bg="#fdf3e6", res_line="#a8611a", done="#9aa5b1", text2=t.text,
                        accent_ink="#1f4fb0", accent_soft="#dfe8fb", head="#1b2433", **common)
        return _Ink(alloc="#8fb0ff", req="#f0a050", claim="#8d99a6", blocked=t.danger,
                    blocked_bg=t.danger_soft, active=t.accent, active_bg=t.accent_soft,
                    ok=t.success, ok_bg=t.success_soft, ok_ink=t.text, warn=t.warning,
                    warn_bg=t.warning_soft, warn_ink=t.text, bad_ink=t.text, res_bg="#2a2116",
                    res_line="#d9944a", done=t.faint, text2=t.text, accent_ink=t.text,
                    accent_soft=t.accent_soft, head=t.bg3, **common)

    def _mono_font(px: float, bold: bool = False) -> QFont:
        f = QFont()
        f.setStyleHint(QFont.Monospace)
        f.setFamilies(["Menlo", "Consolas", "DejaVu Sans Mono", "monospace"])
        f.setPixelSize(max(8, round(px)))
        f.setBold(bold)
        return f

    def _clear_layout(lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                _clear_layout(it.layout())

    class _ElideLabel(QLabel):
        """A single-line label that gives way instead of setting the window's width."""

        def __init__(self, text: str = "", parent=None) -> None:
            super().__init__(text, parent)
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        def minimumSizeHint(self) -> QSize:
            return QSize(24, super().minimumSizeHint().height())

        def paintEvent(self, e) -> None:
            p = QPainter(self)
            p.setFont(self.font())
            p.setPen(self.palette().color(self.foregroundRole()))
            fm = QFontMetrics(self.font())
            r = self.contentsRect()               # inside the stylesheet's padding (the pills)
            txt = fm.elidedText(self.text(), Qt.ElideRight, r.width())
            p.drawText(r, int(self.alignment()) | Qt.AlignVCenter, txt)

    class _GraphView(QWidget):
        """The resource-allocation graph (or the wait-for graph), painted the notes' way."""

        R = 17

        def __init__(self, ink: _Ink) -> None:
            super().__init__()
            self._ink = ink
            self._g: GraphView | None = None
            self.setMinimumHeight(150)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        def set_view(self, g: GraphView) -> None:
            self._g = g
            self.setToolTip(g.aria)
            self.setAccessibleName(g.aria)
            self.update()

        def view(self) -> GraphView | None:
            return self._g

        def sizeHint(self) -> QSize:
            return QSize(560, 210)

        def minimumSizeHint(self) -> QSize:
            return QSize(200, 150)

        def _pos(self, x: float, y: float) -> QPointF:
            pad = 26
            return QPointF(pad + x * (self.width() - 2 * pad), 16 + y * (self.height() - 40))

        def _arrow(self, p: QPainter, a: QPointF, b: QPointF, color: QColor) -> None:
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            size = 7.5
            pts = [b] + [QPointF(b.x() - size * math.cos(ang + d), b.y() - size * math.sin(ang + d))
                         for d in (-0.42, 0.42)]
            p.save()
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawPolygon(QPolygonF(pts))
            p.restore()

        def paintEvent(self, _e) -> None:
            g = self._g
            if g is None:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            k = self._ink
            R = self.R
            pos = {n.name: self._pos(n.x, n.y) for n in g.nodes}
            boxes = {}
            for n in g.nodes:
                if n.proc:
                    continue
                c = pos[n.name]
                cols = 2 if n.units > 4 else 1
                rows = math.ceil(n.units / cols)
                bw = 22 if cols == 1 else 34
                bh = max(22, 12 * rows + 6)
                dots = []
                for i in range(n.units):
                    cc, rr = i % cols, i // cols
                    dots.append(QPointF(c.x() + (0 if cols == 1 else (6 if cc else -6)),
                                        c.y() - bh / 2 + 9 + rr * 12))
                boxes[n.name] = {"c": c, "bw": bw, "bh": bh, "dots": dots, "next": 0}

            def to_circle(a: QPointF, b: QPointF, gap: float) -> QPointF:
                dx, dy = b.x() - a.x(), b.y() - a.y()
                d = math.hypot(dx, dy) or 1
                return QPointF(b.x() - dx / d * gap, b.y() - dy / d * gap)

            def from_circle(a: QPointF, b: QPointF) -> QPointF:
                dx, dy = b.x() - a.x(), b.y() - a.y()
                d = math.hypot(dx, dy) or 1
                return QPointF(a.x() + dx / d * (R + 1), a.y() + dy / d * (R + 1))

            def hit_rect(bx, a: QPointF) -> QPointF:
                c = bx["c"]
                dx, dy = a.x() - c.x(), a.y() - c.y()
                s = max(abs(dx) / (bx["bw"] / 2 + 3), abs(dy) / (bx["bh"] / 2 + 3)) or 1
                return QPointF(c.x() + dx / s, c.y() + dy / s)

            if g.ghost:
                ghost = QColor(k.blocked)
                ghost.setAlpha(46)
                pen = QPen(ghost, 8)
                pen.setJoinStyle(Qt.RoundJoin)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                path = QPainterPath(pos[g.ghost[0]])
                for nm in g.ghost[1:]:
                    path.lineTo(pos[nm])
                p.drawPath(path)

            for e in g.edges:
                width = 2.6 if e.thick else 1.6
                if e.kind == "wait":
                    a, b = pos[e.a], pos[e.b]
                    s, t = from_circle(a, b), to_circle(a, b, R + 3)
                    col = QColor(k.alloc)
                    p.setPen(QPen(col, width))
                    p.drawLine(s, t)
                    self._arrow(p, s, t, col)
                elif e.kind == "alloc":
                    bx = boxes[e.a]
                    target = pos[e.b]
                    col = QColor(k.alloc)
                    for _ in range(e.count):
                        dot = bx["dots"][bx["next"] % max(1, len(bx["dots"]))]
                        bx["next"] += 1
                        t = to_circle(dot, target, R + 3)
                        p.setPen(QPen(col, width))
                        p.drawLine(dot, t)
                        self._arrow(p, dot, t, col)
                else:
                    a = pos[e.a]
                    bx = boxes[e.b]
                    s, t = from_circle(a, bx["c"]), hit_rect(bx, a)
                    if e.kind == "request":
                        col = QColor(k.req)
                        pen = QPen(col, width)
                        pen.setDashPattern([6 / width, 4 / width])
                    else:
                        col = QColor(k.claim)
                        pen = QPen(col, 1.6)
                        pen.setCapStyle(Qt.RoundCap)
                        pen.setDashPattern([0.1, 4 / 1.6])
                    p.setPen(pen)
                    p.drawLine(s, t)
                    self._arrow(p, s, t, col)

            name_f = QFont(self.font())
            name_f.setPixelSize(12)
            name_f.setBold(True)
            cap_f = QFont(self.font())
            cap_f.setPixelSize(10)
            res_f = QFont(self.font())
            res_f.setPixelSize(11)
            res_f.setItalic(True)
            for n in g.nodes:
                if n.proc:
                    continue
                bx = boxes[n.name]
                c = bx["c"]
                p.setPen(QPen(QColor(k.res_line), 1.3))
                p.setBrush(QColor(k.res_bg))
                p.drawRoundedRect(QRectF(c.x() - bx["bw"] / 2, c.y() - bx["bh"] / 2,
                                         bx["bw"], bx["bh"]), 3, 3)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(k.text))
                for d in bx["dots"]:
                    p.drawEllipse(d, 3, 3)
                p.setFont(res_f)
                p.setPen(QColor(k.text2))
                p.drawText(QPointF(c.x() + bx["bw"] / 2 + 5, c.y() - bx["bh"] / 2 + 10), n.name)
            for n in g.nodes:
                if not n.proc:
                    continue
                c = pos[n.name]
                stroke, fill, width = QColor(k.active), QColor(k.active_bg), 1.6
                if n.state in ("done", "terminated"):
                    stroke, fill = QColor(k.done), QColor(k.panel2)
                elif n.state == "deadlocked":
                    stroke, fill, width = QColor(k.blocked), QColor(k.blocked_bg), 2.4
                elif n.state == "blocked":
                    stroke, fill = QColor(k.blocked), QColor(k.panel)
                elif n.state == "refused":
                    stroke, fill = QColor(k.warn), QColor(k.panel)
                p.save()
                if n.state in ("done", "terminated") or n.erased is not None:
                    p.setOpacity(0.5)
                p.setPen(QPen(stroke, width))
                p.setBrush(fill)
                p.drawEllipse(c, R, R)
                p.setFont(name_f)
                p.setPen(QColor(k.text))
                p.drawText(QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R), Qt.AlignCenter, n.name)
                p.setFont(cap_f)
                p.setPen(stroke)
                p.drawText(QRectF(c.x() - 90, c.y() + R + 1, 180, 14),
                           Qt.AlignHCenter | Qt.AlignTop, n.caption)
                p.restore()
                if n.erased is not None:
                    b = QPointF(c.x() + R - 2, c.y() - R + 2)
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(k.ok))
                    p.drawEllipse(b, 8, 8)
                    p.setPen(QColor("#ffffff"))
                    p.setFont(cap_f)
                    p.drawText(QRectF(b.x() - 8, b.y() - 8, 16, 16), Qt.AlignCenter,
                               str(n.erased + 1))

    class BankLab(QDialog):
        """One window: the header with the level, version and goal controls; the graph, the
        process rows and the trace (or, on the bank level, the tables and the requests); and the
        side panel with its tabs.

        It polls nothing. Every control goes through `Session` and then `_refresh`, which
        redraws the face from the session — a few dozen small widgets, well under a frame, and
        no state to get out of step with the model.
        """

        def __init__(self, parent, theme: ThemeManager, lab: B.LabFile | None = None,
                     device=None, on_event=None) -> None:
            super().__init__(parent)
            self.theme = theme
            self.device = device
            lab = lab or B.get("t-lab-03")
            if lab is None:
                raise B.LevelError("the level file t-lab-03 is not shipped with this gini-core")
            if not lab.levels:
                raise B.LevelError(f"{lab.title} has no levels yet")
            self.session = Session(lab, on_event=on_event)
            self._rng = random.Random()
            t = theme.theme
            self._k = _ink(t)
            title = lab.title + (f" — {device.name}" if getattr(device, "name", "") else "")
            standalone(self, title)
            self.resize(940, 700)
            self.setMinimumSize(760, 560)
            self.setStyleSheet(_scss(
                f"QDialog{{background:{t.bg};}} QLabel{{color:{t.text};}}"
                f"QToolTip{{color:{t.text};background:{t.panel};border:1px solid {t.line};}}"))
            root = QVBoxLayout(self)
            root.setContentsMargins(14, 12, 14, 12)
            root.setSpacing(8)
            self._build_header(root)
            self._build_goals(root)
            body = QHBoxLayout()
            body.setSpacing(8)
            root.addLayout(body, 1)
            left = QVBoxLayout()
            left.setSpacing(8)
            body.addLayout(left, 1)
            self._build_starts(left)
            self._left = QStackedWidget()
            self._left.addWidget(self._build_run_page())
            self._left.addWidget(self._build_bank_page())
            left.addWidget(self._left, 1)
            self._build_aside(body)
            self._refresh()

        # -- styles ------------------------------------------------------------ #
        def _btn_css(self, prime: bool = False, size: int = 13, h: int = 30) -> str:
            k = self._k
            if prime:
                return _scss(f"QPushButton{{background:{k.head};color:#ffffff;border:1px solid "
                             f"{k.head};border-radius:7px;padding:0 12px;font-size:{size}px;"
                             f"font-weight:600;min-height:{h}px;}}"
                             f"QPushButton:disabled{{background:{k.panel2};color:{k.faint};"
                             f"border-color:{k.line};}}")
            return _scss(f"QPushButton{{background:{k.panel};color:{k.text};border:1px solid "
                         f"{k.line2};border-radius:7px;padding:0 10px;font-size:{size}px;"
                         f"min-height:{h}px;}}"
                         f"QPushButton:hover{{border-color:{k.accent};}}"
                         f"QPushButton:checked{{background:{k.accent_soft};color:{k.accent_ink};"
                         f"border:1.5px solid {k.accent};}}"
                         f"QPushButton:disabled{{color:{k.faint};}}")

        def _small_css(self, border: str = "", ink: str = "") -> str:
            k = self._k
            return _scss(f"QPushButton{{background:{k.panel};color:{ink or k.text};border:1px "
                         f"solid {border or k.line2};border-radius:6px;padding:0 8px;"
                         f"font-size:12px;min-height:24px;}}"
                         f"QPushButton:hover{{border-color:{k.accent};}}"
                         f"QPushButton:checked{{background:{k.accent_soft};color:{k.accent_ink};"
                         f"border:1.5px solid {k.accent};}}"
                         f"QPushButton:disabled{{color:{k.faint};border-color:{k.line};}}")

        def _card(self, name: str) -> QFrame:
            k = self._k
            f = QFrame()
            f.setObjectName(name)
            f.setStyleSheet(f"QFrame#{name}{{background:{k.panel};border:1px solid {k.line};"
                            f"border-radius:10px;}}")
            return f

        def _scroll(self, inner: QWidget) -> QScrollArea:
            k = self._k
            s = QScrollArea()
            s.setWidgetResizable(True)
            s.setFrameShape(QFrame.Shape.NoFrame)
            s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            # The viewport and the host both get the panel colour explicitly: left to the
            # palette, the dark themes put light text on a light field (as T-Lab 02 found).
            s.setStyleSheet(f"QScrollArea{{border:none;background:{k.panel};}}"
                            f"QScrollArea > QWidget > QWidget{{background:{k.panel};}}")
            s.setWidget(inner)
            return s

        def _label(self, text: str = "", size: int = 12, color: str = "", bold: bool = False,
                   wrap: bool = True, mono: bool = False) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(wrap)
            lbl.setTextFormat(Qt.PlainText)
            if mono:
                lbl.setFont(_mono_font(size - 0.5, bold))
                lbl.setStyleSheet(_scss(f"color:{color or self._k.text};border:none;"
                                        "background:transparent;"))
            else:
                lbl.setStyleSheet(_scss(f"font-size:{size}px;color:{color or self._k.text};"
                                        f"{'font-weight:700;' if bold else ''}border:none;"
                                        "background:transparent;"))
            return lbl

        def _cap(self, text: str) -> QLabel:
            lbl = QLabel(text.upper())
            lbl.setStyleSheet(_scss(f"font-size:11px;font-weight:700;letter-spacing:1px;"
                                    f"color:{self._k.faint};border:none;background:transparent;"))
            return lbl

        def _box(self, text: str, kind: str) -> QLabel:
            k = self._k
            bg, fg, border = {"ok": (k.ok_bg, k.ok_ink, k.ok),
                              "bad": (k.blocked_bg, k.bad_ink, k.blocked)}.get(
                                  kind, (k.panel2, k.text, k.line))
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.PlainText)
            lbl.setStyleSheet(_scss(f"font-size:12px;padding:7px 9px;border-radius:8px;"
                                    f"background:{bg};color:{fg};border:1px solid {border};"
                                    f"{'font-weight:600;' if kind in ('ok', 'bad') else ''}"))
            return lbl

        # -- construction ----------------------------------------------------- #
        def _build_header(self, root) -> None:
            k = self._k
            row = QHBoxLayout()
            row.setSpacing(10)
            col = QVBoxLayout()
            col.setSpacing(0)
            cap = QLabel(self.session.lab.title.replace(" — ", " · ").upper())
            cap.setStyleSheet(_scss(f"font-size:11px;font-weight:700;letter-spacing:1px;"
                                    f"color:{k.accent};"))
            self._level_title = _ElideLabel("")
            self._level_title.setStyleSheet(_scss(f"font-size:17px;font-weight:700;"
                                                  f"color:{k.text};"))
            col.addWidget(cap)
            col.addWidget(self._level_title)
            row.addLayout(col, 1)
            self._level_btns = []
            lv_row = QHBoxLayout()
            lv_row.setSpacing(4)
            for i, lv in enumerate(self.session.lab.levels):
                b = QPushButton(str(lv.n))
                b.setCheckable(True)
                b.setFixedSize(34, 34)
                b.setToolTip(f"Level {lv.n}: {lv.title}")
                b.setAccessibleName(f"Level {lv.n}: {lv.title}")
                b.setStyleSheet(_scss(
                    f"QPushButton{{background:{k.panel};color:{k.text};border:1px solid "
                    f"{k.line2};border-radius:7px;font-size:14px;font-weight:700;}}"
                    f"QPushButton:checked{{background:{k.head};color:#ffffff;"
                    f"border-color:{k.head};}}"))
                b.clicked.connect(lambda _c=False, i=i: self._pick_level(i))
                lv_row.addWidget(b)
                self._level_btns.append(b)
            row.addLayout(lv_row)
            self._version_row = QHBoxLayout()
            self._version_row.setSpacing(4)
            self._version_btns: dict = {}
            row.addLayout(self._version_row)
            root.addLayout(row)

        def _build_goals(self, root) -> None:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(self._cap("Goals"))
            self._chips_row = QHBoxLayout()
            self._chips_row.setSpacing(6)
            self._chips: dict = {}
            row.addLayout(self._chips_row)
            row.addStretch(1)
            self._undo = QPushButton("Undo")
            self._reset = QPushButton("Reset")
            self._random = QPushButton("Random")
            self._explore = QPushButton("Try every order")
            for b in (self._undo, self._reset, self._random):
                b.setStyleSheet(self._btn_css())
                row.addWidget(b)
            self._explore.setStyleSheet(self._btn_css(prime=True))
            row.addWidget(self._explore)
            self._undo.clicked.connect(self._on_undo)
            self._reset.clicked.connect(self._on_reset)
            self._random.clicked.connect(self._on_random)
            self._explore.clicked.connect(self._on_explore)
            root.addLayout(row)

        def _build_starts(self, left) -> None:
            self._starts_box = QWidget()
            row = QHBoxLayout(self._starts_box)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(self._cap("Start"))
            self._starts_row = row
            self._start_btns: list = []
            row.addStretch(1)
            left.addWidget(self._starts_box)

        def _build_run_page(self) -> QWidget:
            k = self._k
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(8)
            # the graph card
            card = self._card("graphcard")
            gl = QVBoxLayout(card)
            gl.setContentsMargins(10, 6, 10, 6)
            gl.setSpacing(2)
            top = QHBoxLayout()
            top.setSpacing(4)
            self._graph_note = self._label("", 11, k.muted)
            top.addWidget(self._graph_note, 1)
            self._hide_graph = QPushButton("Hide the graph")
            self._hide_graph.setCheckable(True)
            self._hide_graph.setStyleSheet(self._small_css())
            self._hide_graph.clicked.connect(self._on_hide_graph)
            top.addWidget(self._hide_graph, 0, Qt.AlignTop)
            self._view_btns = {}
            for vid, text in (("rag", "Allocation graph"), ("wf", "Wait-for graph")):
                b = QPushButton(text)
                b.setCheckable(True)
                b.setStyleSheet(self._small_css())
                b.clicked.connect(lambda _c=False, vid=vid: self._on_view(vid))
                top.addWidget(b, 0, Qt.AlignTop)
                self._view_btns[vid] = b
            gl.addLayout(top)
            self._graph_stack = QStackedWidget()
            self._graph = _GraphView(k)
            self._graph_stack.addWidget(self._graph)
            hidden = self._label(GRAPH_HIDDEN, 12, k.muted)
            hidden.setAlignment(Qt.AlignCenter)
            hidden.setContentsMargins(40, 0, 40, 0)
            self._graph_stack.addWidget(hidden)
            gl.addWidget(self._graph_stack, 1)
            self._legend = self._label("", 11, k.muted)
            gl.addWidget(self._legend)
            v.addWidget(card, 5)
            # the process rows
            pcard = self._card("procs")
            pl = QVBoxLayout(pcard)
            pl.setContentsMargins(8, 4, 8, 4)
            host = QWidget()
            self._procs_grid = QGridLayout(host)
            self._procs_grid.setContentsMargins(0, 0, 0, 0)
            self._procs_grid.setHorizontalSpacing(8)
            self._procs_grid.setVerticalSpacing(3)
            self._procs_scroll = self._scroll(host)
            pl.addWidget(self._procs_scroll)
            self._procs_card = pcard
            v.addWidget(pcard, 0)
            # the trace
            tcard = self._card("trace")
            tl = QVBoxLayout(tcard)
            tl.setContentsMargins(10, 6, 10, 6)
            tl.setSpacing(3)
            tl.addWidget(self._cap("Trace"))
            host = QWidget()
            self._trace_col = QVBoxLayout(host)
            self._trace_col.setContentsMargins(0, 0, 0, 0)
            self._trace_col.setSpacing(2)
            self._trace_scroll = self._scroll(host)
            self._trace_scroll.setMinimumHeight(60)
            tl.addWidget(self._trace_scroll, 1)
            v.addWidget(tcard, 3)
            return page

        def _build_bank_page(self) -> QWidget:
            k = self._k
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(8)
            card = self._card("banktable")
            bl = QVBoxLayout(card)
            bl.setContentsMargins(10, 8, 10, 8)
            bl.setSpacing(4)
            self._bank_note = self._label("", 11, k.muted)
            bl.addWidget(self._bank_note)
            self._bank_grid = QGridLayout()
            self._bank_grid.setHorizontalSpacing(4)
            self._bank_grid.setVerticalSpacing(2)
            gw = QHBoxLayout()
            gw.addLayout(self._bank_grid)
            gw.addStretch(1)
            bl.addLayout(gw)
            arow = QHBoxLayout()
            arow.setSpacing(8)
            self._avail = self._label("", 13, k.text, mono=True, wrap=False)
            arow.addWidget(self._avail)
            self._need_btn = QPushButton("Show Need and Avail")
            self._need_btn.setStyleSheet(self._small_css(k.accent, k.accent_ink))
            self._need_btn.clicked.connect(self._on_need)
            arow.addWidget(self._need_btn)
            arow.addStretch(1)
            bl.addLayout(arow)
            v.addWidget(card, 0)
            rcard = self._card("requests")
            rl = QVBoxLayout(rcard)
            rl.setContentsMargins(10, 8, 10, 8)
            host = QWidget()
            self._req_col = QVBoxLayout(host)
            self._req_col.setContentsMargins(0, 0, 0, 0)
            self._req_col.setSpacing(5)
            rl.addWidget(self._scroll(host))
            v.addWidget(rcard, 1)
            return page

        def _build_aside(self, body) -> None:
            k = self._k
            aside = QFrame()
            aside.setObjectName("aside")
            aside.setStyleSheet(f"QFrame#aside{{background:{k.panel};border:1px solid {k.line};"
                                f"border-radius:10px;}}")
            aside.setFixedWidth(SIDE_W)
            al = QVBoxLayout(aside)
            al.setContentsMargins(0, 0, 0, 0)
            al.setSpacing(0)
            self._tabs_row = QHBoxLayout()
            self._tabs_row.setSpacing(0)
            self._tab_btns: dict = {}
            al.addLayout(self._tabs_row)
            host = QWidget()
            self._side_col = QVBoxLayout(host)
            self._side_col.setContentsMargins(12, 10, 12, 10)
            self._side_col.setSpacing(8)
            self._side_scroll = self._scroll(host)
            al.addWidget(self._side_scroll, 1)
            body.addWidget(aside)
            self._aside = aside

        # -- handlers ------------------------------------------------------------ #
        def _pick_level(self, i: int) -> None:
            self.session.pick_level(i)
            self._refresh()

        def _pick_version(self, vid: str) -> None:
            self.session.pick_version(vid)
            self._refresh()

        def _pick_start(self, i: int) -> None:
            self.session.pick_start(i)
            self._refresh()

        def _on_play(self, p: int, kind: str) -> None:
            self.session.play(p, kind)
            self._refresh()

        def _on_undo(self) -> None:
            self.session.undo()
            self._refresh()

        def _on_reset(self) -> None:
            self.session.reset()
            self._refresh()

        def _on_random(self) -> None:
            self.session.random(self._rng)
            self._refresh()

        def _on_explore(self) -> None:
            self.session.explore_all()
            self._refresh()

        def _on_show(self, moves) -> None:
            self.session.show(moves)
            self._refresh()

        def _on_tab(self, name: str) -> None:
            self.session.tab = name
            self._refresh()

        def _on_hide_graph(self) -> None:
            self.session.hide_graph = not self.session.hide_graph
            self._refresh()

        def _on_view(self, vid: str) -> None:
            self.session.view = vid
            self._refresh()

        def _on_need(self) -> None:
            self.session.toggle_need()
            self._refresh()

        def _on_request(self, sel: int) -> None:
            self.session.pick_request(sel)
            self._refresh()

        def _on_try(self, p: int) -> None:
            self.session.try_process(p)
            self._refresh()

        def _on_stuck(self) -> None:
            self.session.claim_stuck()
            self._refresh()

        def _on_decide(self, choice: str) -> None:
            self.session.decide(choice)
            self._refresh()

        # -- drawing --------------------------------------------------------------- #
        def _refresh(self) -> None:
            s = self.session
            lv = s.level
            k = self._k
            self._level_title.setText(f"{lv.n} · {lv.title}    {lv.source}")
            self._level_title.setToolTip(f"{lv.n} · {lv.title} — {lv.source}")
            for i, b in enumerate(self._level_btns):
                b.setChecked(i == s.level_index)
            _clear_layout(self._version_row)
            self._version_btns = {}
            if len(lv.versions) > 1:
                for v in lv.versions:
                    b = QPushButton(v.label)
                    b.setCheckable(True)
                    b.setChecked(v.id == s.version_id)
                    b.setStyleSheet(self._btn_css(h=32))
                    b.clicked.connect(lambda _c=False, vid=v.id: self._pick_version(vid))
                    self._version_row.addWidget(b)
                    self._version_btns[v.id] = b
            _clear_layout(self._chips_row)
            self._chips = {}
            for c in goal_chips(lv, s.achieved):
                lbl = QLabel(("✓ " if c.done else "○ ") + c.text)
                lbl.setToolTip(c.tooltip)
                lbl.setAccessibleName(f"{c.text} ({'done' if c.done else 'not yet'})")
                lbl.setStyleSheet(_scss(
                    "font-size:12px;padding:3px 9px;border-radius:12px;"
                    + (f"background:{k.ok_bg};color:{k.ok_ink};border:1px solid {k.ok};"
                       "font-weight:600;" if c.done else
                       f"background:{k.panel};color:{k.text};border:1px solid {k.line2};")))
                self._chips_row.addWidget(lbl)
                self._chips[c.id] = lbl
            self._undo.setEnabled(s.can_undo())
            self._reset.setEnabled(s.can_reset())
            self._random.setVisible(s.mode == "run")
            self._random.setEnabled(s.can_random())
            # starts
            for b in self._start_btns:
                b.setParent(None)
                b.deleteLater()
            self._start_btns = []
            for i, st in enumerate(lv.starts):
                b = QPushButton(st.label)
                b.setCheckable(True)
                b.setChecked(i == s.start)
                b.setStyleSheet(self._small_css())
                b.clicked.connect(lambda _c=False, i=i: self._pick_start(i))
                self._starts_row.insertWidget(self._starts_row.count() - 1, b)
                self._start_btns.append(b)
            self._starts_box.setVisible(len(lv.starts) > 1)
            # tabs
            _clear_layout(self._tabs_row)
            self._tab_btns = {}
            for tid, text in s.tabs():
                b = QPushButton(text)
                b.setCheckable(True)
                b.setChecked(tid == s.tab)
                b.setStyleSheet(_scss(
                    f"QPushButton{{border:0;border-bottom:2px solid transparent;"
                    f"background:{k.panel2};color:{k.muted};font-size:12px;min-height:34px;"
                    f"padding:0 4px;}}"
                    f"QPushButton:checked{{border-bottom:2px solid {k.accent};"
                    f"background:{k.panel};color:{k.accent_ink};font-weight:700;}}"))
                b.clicked.connect(lambda _c=False, tid=tid: self._on_tab(tid))
                self._tabs_row.addWidget(b, 1)
                self._tab_btns[tid] = b
            _clear_layout(self._side_col)
            # Every handle into the rebuilt widgets starts empty, so none outlives its widget
            # when the mode changes (a bank level has no process rows, a run level no check).
            self._proc_btns: dict = {}
            self._pills: dict = {}
            self._trace_lines: list = []
            self._trace_end = None
            self._req_btns: list = []
            self._bank_cells: dict = {}
            self._check_rows: list = []
            self._show_btns: list = []
            self._verdict = None
            self._recover_btns: dict = {}
            self._try_btns: list = []
            self._stuck_btn = None
            self._check_msg = None
            self._decide_btns: dict = {}
            self._bank_verdict = None
            if s.mode == "run":
                self._left.setCurrentIndex(0)
                self._refresh_run()
            else:
                self._left.setCurrentIndex(1)
                self._refresh_bank()
            self._side_col.addStretch(1)

        # -- run levels -------------------------------------------------------- #
        def _refresh_run(self) -> None:
            s = self.session
            k = self._k
            lv, w, v = s.level, s.world, s.version
            st = s.state()
            tab = s.tab
            start = w.starts[s.start]
            wf_ok = wait_for_offered(lv, w)
            wf = wf_ok and s.view == "wf"
            self._graph_note.setText(start.note)       # empty, it still holds the buttons right
            self._hide_graph.setChecked(s.hide_graph)
            self._hide_graph.setText("Show the graph" if s.hide_graph else "Hide the graph")
            for vid, b in self._view_btns.items():
                b.setVisible(wf_ok and not s.hide_graph)
                b.setChecked((vid == "wf") == wf)
            self._graph_stack.setCurrentIndex(1 if s.hide_graph else 0)
            gv = graph_view(lv, w, v, st, erased=(tab == "reduce"), wait=wf)
            self._graph.set_view(gv)
            if wf:
                self._legend.setText("Pi → Pj: Pi waits for something Pj holds · "
                                     "thick edges: a cycle")
            else:
                parts = (["┈ claim (dotted)"] if claims_drawn(lv) else []) + [
                    "╌ request (dashed)", "─ allocation (solid)", "• one dot per unit",
                    "thick edges: a cycle"]
                if gv.ghost:
                    parts.append("shaded: the cycle the grant would close")
                self._legend.setText("   ".join(parts))
            # process rows
            _clear_layout(self._procs_grid)
            rows = proc_rows(w, v, st)
            pill_css = {
                "active": (k.active_bg, k.active, k.accent_ink),
                "blocked": (k.blocked_bg, k.blocked, k.bad_ink),
                "refused": (k.warn_bg, k.warn, k.warn_ink),
                "done": (k.panel2, k.line2, k.muted),
                "terminated": (k.panel2, k.line2, k.muted)}
            row_h = 26
            for i, r in enumerate(rows):
                name = self._label(r.name, 13, bold=True, wrap=False)
                name.setFixedWidth(28)
                self._procs_grid.addWidget(name, i, 0)
                pill = _ElideLabel(r.status)
                bg, border, fg = pill_css[r.kind]
                pill.setToolTip(r.status)
                pill.setFixedWidth(180)
                pill.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
                pill.setStyleSheet(_scss(f"font-size:11px;padding:2px 8px;border-radius:9px;"
                                         f"background:{bg};border:1px solid {border};"
                                         f"color:{fg};"))
                self._procs_grid.addWidget(pill, i, 1)
                self._pills[r.name] = pill
                row_h = max(row_h, pill.sizeHint().height())
                mv = QHBoxLayout()
                mv.setSpacing(5)
                for m in r.moves:
                    b = QPushButton(m.label)
                    b.setStyleSheet(self._small_css(k.accent if m.ask else "",
                                                    k.accent_ink if m.ask else ""))
                    b.clicked.connect(lambda _c=False, p=m.p, kd=m.kind: self._on_play(p, kd))
                    mv.addWidget(b)
                    self._proc_btns[(m.p, m.kind)] = b
                    row_h = max(row_h, b.sizeHint().height())
                mv.addStretch(1)
                self._procs_grid.addLayout(mv, i, 2)
            self._procs_grid.setColumnStretch(2, 1)
            # Sized from what the rows really measure (a row is as tall as its buttons, which a
            # fixed guess under-counted — P4 of Figure 14.9 was cut off), up to five rows; a
            # sixth scrolls.
            self._procs_scroll.setFixedHeight(min(5, len(rows)) * (row_h + 3) + 4)
            # And the card with it: left to its stretch of 0, the column gave the graph the
            # spare height and squeezed this card below its own minimum.
            self._procs_card.setFixedHeight(self._procs_scroll.height() + 8)
            # trace
            _clear_layout(self._trace_col)
            lines, end = trace(w, st, s.lines())
            if not lines:
                setup = self._label(lv.setup, 12)
                self._trace_col.addWidget(setup)
                ask = QFrame()
                ask.setObjectName("ask")
                ask.setStyleSheet(f"QFrame#ask{{background:{k.accent_soft};border-radius:8px;}}")
                al = QHBoxLayout(ask)
                al.setContentsMargins(10, 6, 10, 6)
                al.setSpacing(10)
                acap = self._label("GINI asks", 11, k.accent_ink, bold=True, wrap=False)
                acap.setAlignment(Qt.AlignTop)
                al.addWidget(acap)
                al.addWidget(self._label(lv.asks, 12), 1)
                self._trace_col.addWidget(ask)
                self._asks = ask
                if not end:
                    self._trace_col.addWidget(self._label(
                        "Press a button: you decide which process moves next.", 11, k.muted))
            for i, line in enumerate(lines):
                lbl = self._label(f"{i + 1}. {line}", 12)
                self._trace_col.addWidget(lbl)
                self._trace_lines.append(lbl)
            if end:
                text, kind = end
                lbl = self._label(text, 12, k.ok_ink if kind == "good" else k.bad_ink, bold=True)
                self._trace_col.addWidget(lbl)
                self._trace_end = lbl
            self._trace_col.addStretch(1)
            sb = self._trace_scroll.verticalScrollBar()
            sb.setValue(sb.maximum())
            # the side panel
            if tab == "tables":
                self._side_tables(w, st)
            elif tab == "reduce":
                self._side_reduce(w, st)
            elif tab == "recover":
                self._side_recover(w, st, start.note)
            else:
                self._side_orders(v)

        def _grid_table(self, heads, rows, mono_from: int = 1) -> QGridLayout:
            k = self._k
            g = QGridLayout()
            g.setHorizontalSpacing(8)
            g.setVerticalSpacing(3)
            for j, h in enumerate(heads):
                g.addWidget(self._label(h.upper(), 10, k.muted, bold=True, wrap=False), 0, j)
            for i, row in enumerate(rows, 1):
                for j, cell in enumerate(row):
                    if j < mono_from:
                        g.addWidget(self._label(str(cell), 12, bold=True, wrap=False), i, j)
                    else:
                        g.addWidget(self._label(str(cell), 12, mono=True, wrap=False), i, j)
            return g

        def _side_tables(self, w, st) -> None:
            t = tables(w, st)
            col = self._side_col
            if t.order_note:
                col.addWidget(self._label(t.order_note, 11, self._k.muted))
            g = self._grid_table(t.heads, t.rows)
            n = len(t.rows) + 1
            g.addWidget(self._label("Avail", 12, bold=True, wrap=False), n, 0)
            g.addWidget(self._label(t.avail, 12, mono=True, wrap=False), n, 1, 1, len(t.heads) - 1)
            col.addLayout(g)
            col.addWidget(self._label(t.note, 11, self._k.muted))

        def _side_reduce(self, w, st) -> None:
            r = reduce_view(w, st)
            col = self._side_col
            col.addWidget(self._label("Cycles in the graph:" + ("" if r.cycles else " none."), 12,
                                      bold=True))
            for c in r.cycles:
                col.addWidget(self._label(c, 12, mono=True))
            col.addWidget(self._label(r.intro, 12))
            if r.order_note:
                col.addWidget(self._label(r.order_note, 11, self._k.muted))
            col.addLayout(self._grid_table(("#", "erase", "request ≤ Work", "Work after"),
                                           r.rows, mono_from=2))
            col.addWidget(self._box(r.box, r.box_kind))
            col.addWidget(self._label(r.note, 11, self._k.muted))

        def _side_recover(self, w, st, note: str) -> None:
            k = self._k
            r = recover_view(w, st, note)
            col = self._side_col
            col.addWidget(self._label(r.thrown, 12, bold=True))
            col.addWidget(self._label(r.note, 11, k.muted))
            if r.box:
                col.addWidget(self._box(r.box, r.box_kind))
            for row in r.rows:
                f = QFrame()
                f.setObjectName("rrow")
                f.setStyleSheet(f"QFrame#rrow{{border:1px solid {k.line};border-radius:8px;}}")
                fl = QVBoxLayout(f)
                fl.setContentsMargins(8, 6, 8, 6)
                fl.setSpacing(4)
                fl.addWidget(self._label(f"{row.name}  ·  {row.facts}", 12))
                if not row.moves:
                    fl.addWidget(self._label("nothing left to take back", 11, k.muted))
                for m in row.moves:
                    b = QPushButton(m.label)
                    b.setStyleSheet(self._small_css(k.blocked, k.bad_ink))
                    b.clicked.connect(lambda _c=False, p=m.p, kd=m.kind: self._on_play(p, kd))
                    fl.addWidget(b)
                    self._recover_btns[(m.p, m.kind)] = b
                col.addWidget(f)

        def _side_orders(self, v) -> None:
            k = self._k
            col = self._side_col
            ex = self.session.exploration()
            if ex is None:
                col.addWidget(self._box(GUESS_FIRST, "info"))
                return
            o = orders_view(v, ex)
            col.addWidget(self._label(o.summary, 12))
            for r in o.rows:
                f = QFrame()
                f.setObjectName("orow")
                f.setStyleSheet(f"QFrame#orow{{border:1px solid {k.line};border-radius:8px;}}")
                fl = QGridLayout(f)
                fl.setContentsMargins(8, 6, 8, 6)
                fl.setSpacing(4)
                fl.addWidget(self._label(r.label, 12, k.ok_ink if r.ok else k.bad_ink,
                                         bold=True), 0, 0)
                fl.addWidget(self._label("possible", 11, k.muted, wrap=False), 0, 1)
                fl.addWidget(self._label(f"shortest: {r.steps} step"
                                         f"{'' if r.steps == 1 else 's'}", 11, k.muted), 1, 0)
                b = QPushButton("Show me the shortest")
                b.setStyleSheet(self._small_css(k.accent, k.accent_ink))
                b.clicked.connect(lambda _c=False, m=r.moves: self._on_show(m))
                fl.addWidget(b, 1, 1)
                self._show_btns.append(b)
                col.addWidget(f)
            if o.verdict:
                box = self._box(o.verdict_text, "ok" if o.verdict == "proved" else "bad")
                box.setObjectName("verdict")
                col.addWidget(box)
                self._verdict = box

        # -- the bank level ------------------------------------------------------ #
        def _refresh_bank(self) -> None:
            s = self.session
            k = self._k
            lv, w = s.level, s.world
            start = w.starts[s.start]
            b = s.peek_work()
            prov = s.provisional() if (b.sel is not None and b.sel >= 0) else None
            shown = s.need_shown
            self._bank_note.setText(start.note)
            self._bank_note.setVisible(bool(start.note))
            t = bank_table(w, s.start, prov, shown)
            g = self._bank_grid
            _clear_layout(g)
            m = len(t.resources)
            multi = m > 1
            heads = ("Max", "Hold", "Need")
            if multi:
                for gi, h in enumerate(heads):
                    lbl = self._label(h, 11, k.text, bold=True, wrap=False)
                    lbl.setAlignment(Qt.AlignCenter)
                    g.addWidget(lbl, 0, 1 + gi * (m + 1), 1, m)
                for gi in range(3):
                    for j, rn in enumerate(t.resources):
                        lbl = self._label(rn, 10, k.muted, bold=True, wrap=False)
                        lbl.setAlignment(Qt.AlignCenter)
                        g.addWidget(lbl, 1, 1 + gi * (m + 1) + j)
                    if gi < 2:
                        sep = QFrame()
                        sep.setFixedWidth(1)
                        sep.setStyleSheet(f"background:{k.line};")
                        g.addWidget(sep, 0, (gi + 1) * (m + 1), len(t.rows) + 2, 1)
            else:
                for gi, h in enumerate(heads):
                    lbl = self._label(h, 11, k.text, bold=True, wrap=False)
                    lbl.setAlignment(Qt.AlignCenter)
                    g.addWidget(lbl, 1, 1 + gi * 2)
            for i, (pn, mx, hd, nd) in enumerate(t.rows, 2):
                g.addWidget(self._label(pn, 12, bold=True, wrap=False), i, 0)
                for gi, cells in enumerate((mx, hd, nd)):
                    for j, c in enumerate(cells):
                        color = k.muted if c.hidden else (k.accent_ink if c.changed else k.text)
                        lbl = self._label(c.text, 12.5, color, mono=True, wrap=False)
                        if c.changed:
                            lbl.setFont(_mono_font(12, True))
                        lbl.setAlignment(Qt.AlignCenter)
                        lbl.setMinimumWidth(22)
                        g.addWidget(lbl, i, 1 + gi * (m + 1 if multi else 2) + j)
                        self._bank_cells[(pn, heads[gi], j)] = lbl
            self._avail.setText(t.avail)
            self._need_btn.setVisible(start.hide_need)
            self._need_btn.setText("Hide Need and Avail" if shown else "Show Need and Avail")
            # requests
            col = self._req_col
            _clear_layout(col)
            col.addWidget(self._cap("Requests"))
            for i, text in enumerate(request_labels(w, s.start)):
                sel = i - 1
                btn = QPushButton(text)
                btn.setCheckable(True)
                btn.setChecked(b.sel == sel)
                btn.setStyleSheet(_scss(
                    f"QPushButton{{background:{k.panel};color:{k.text};border:1px solid "
                    f"{k.line2};border-radius:7px;padding:4px 8px;font-size:12px;"
                    f"text-align:left;}}"
                    f"QPushButton:hover{{border-color:{k.accent};}}"
                    f"QPushButton:checked{{background:{k.accent_soft};color:{k.accent_ink};"
                    f"border:1.5px solid {k.accent};font-weight:600;}}"))
                btn.clicked.connect(lambda _c=False, sel=sel: self._on_request(sel))
                col.addWidget(btn)
                self._req_btns.append(btn)
            tf = QFrame()
            tf.setObjectName("tests")
            tf.setStyleSheet(f"QFrame#tests{{background:{k.panel2};border:1px solid {k.line};"
                             f"border-radius:7px;}}")
            tl = QVBoxLayout(tf)
            tl.setContentsMargins(8, 6, 8, 6)
            tl.setSpacing(2)
            self._test_lines = []
            for text, ans in test_lines(w, s.start, b.sel, shown):
                row = QHBoxLayout()
                row.setSpacing(5)
                row.addWidget(self._label(text, 12, wrap=not ans))
                if ans:
                    row.addWidget(self._label(ans, 12, k.ok_ink if ans == "yes" else k.bad_ink,
                                              bold=True, wrap=False))
                    row.addStretch(1)
                tl.addLayout(row)
                self._test_lines.append((text, ans))
            col.addWidget(tf)
            if b.sel is None and not b.picks:
                ask = QFrame()
                ask.setObjectName("ask")
                ask.setStyleSheet(f"QFrame#ask{{background:{k.accent_soft};border-radius:8px;}}")
                al = QHBoxLayout(ask)
                al.setContentsMargins(10, 6, 10, 6)
                al.setSpacing(10)
                acap = self._label("GINI asks", 11, k.accent_ink, bold=True, wrap=False)
                acap.setAlignment(Qt.AlignTop)
                al.addWidget(acap)
                al.addWidget(self._label(lv.asks, 12), 1)
                col.addWidget(ask)
                col.addWidget(self._label(lv.setup, 11, k.muted))
            col.addStretch(1)
            # the side panel
            if s.tab == "check":
                self._side_check(w, b)
            else:
                self._side_safe(w, prov if b.sel is not None and b.sel >= 0 else None)

        def _side_check(self, w, b: BankWork) -> None:
            s = self.session
            k = self._k
            col = self._side_col
            c = s.check()
            if c is None:
                if b.sel is None:
                    col.addWidget(self._box(PICK_REQUEST, "info"))
                else:
                    p, vec = w.starts[s.start].requests[b.sel]
                    t = B.tests(w, s.start, p, vec)
                    col.addWidget(self._box(
                        f"There is nothing to check: test {'1' if not t['le_need'] else '2'} "
                        "already failed. Decide below.", "info"))
            else:
                cv = check_view(w, c, b.stuck)
                col.addWidget(self._label(f"Work = {cv.work}", 13, bold=True, mono=True))
                g = QGridLayout()
                g.setHorizontalSpacing(8)
                g.setVerticalSpacing(2)
                for j, h in enumerate(("try", "Need ≤ Work?", "result")):
                    g.addWidget(self._label(h, 10, k.muted, bold=True, wrap=False), 0, j)
                for i, r in enumerate(cv.rows, 1):
                    g.addWidget(self._label(r.name, 12, bold=True, wrap=False), i, 0)
                    cell = QHBoxLayout()
                    cell.setSpacing(4)
                    cell.addWidget(self._label(r.test + ":", 11, mono=True, wrap=False))
                    cell.addWidget(self._label("yes" if r.fits else "no", 11,
                                               k.ok_ink if r.fits else k.bad_ink, bold=True,
                                               wrap=False))
                    cell.addStretch(1)
                    g.addLayout(cell, i, 1)
                    g.addWidget(self._label(r.result, 11), i, 2)
                    self._check_rows.append(r)
                g.setColumnStretch(2, 1)
                col.addLayout(g)
                if cv.box:
                    col.addWidget(self._box(cv.box, cv.box_kind))
                else:
                    col.addWidget(self._cap("Try a process"))
                    tg = QGridLayout()
                    tg.setSpacing(4)
                    for p, pn in enumerate(w.procs):
                        btn = QPushButton(f"Try {pn}")
                        btn.setStyleSheet(self._small_css())
                        btn.setEnabled(cv.can_try[p])
                        btn.clicked.connect(lambda _c=False, p=p: self._on_try(p))
                        tg.addWidget(btn, p // 4, p % 4)
                        self._try_btns.append(btn)
                    col.addLayout(tg)
                    self._stuck_btn = QPushButton("Nobody fits: unsafe")
                    self._stuck_btn.setStyleSheet(self._small_css())
                    self._stuck_btn.clicked.connect(self._on_stuck)
                    col.addWidget(self._stuck_btn, 0, Qt.AlignLeft)
                    if b.msg:
                        self._check_msg = self._label(b.msg, 12, k.bad_ink)
                        col.addWidget(self._check_msg)
            if b.sel is not None and b.sel >= 0:
                col.addWidget(self._cap("Decide"))
                row = QHBoxLayout()
                row.setSpacing(6)
                for choice in ("grant", "wait", "error"):
                    btn = QPushButton(choice.capitalize())
                    btn.setCheckable(True)
                    btn.setChecked(b.decided == choice)
                    btn.setStyleSheet(self._btn_css(h=30))
                    btn.clicked.connect(lambda _c=False, ch=choice: self._on_decide(ch))
                    row.addWidget(btn, 1)
                    self._decide_btns[choice] = btn
                col.addLayout(row)
                if b.verdict:
                    self._bank_verdict = self._box(b.verdict, "ok" if b.right else "bad")
                    col.addWidget(self._bank_verdict)

        def _side_safe(self, w, prov) -> None:
            s = self.session
            o = safe_orders(w, s.start, prov)
            col = self._side_col
            col.addWidget(self._label(o.intro, 12))
            if o.count:
                col.addWidget(self._box(f"{o.count} safe sequence{'s' if o.count > 1 else ''}.",
                                        "ok"))
                col.addWidget(self._label("\n".join(o.sequences) + ("\n…" if o.more else ""),
                                          12, mono=True))
            else:
                col.addWidget(self._box("No safe sequence: the state is unsafe.", "bad"))

    def open_bank_lab(parent, theme: ThemeManager, device=None, on_event=None,
                      lab_id: str = "t-lab-03") -> BankLab:
        """The entry point: opens the lab with no machine, no provider and no container. A T-Lab
        is a simulator; nothing here needs Docker."""
        w = BankLab(parent, theme, lab=B.get(lab_id), device=device, on_event=on_event)
        w.show()
        w.raise_()
        return w
