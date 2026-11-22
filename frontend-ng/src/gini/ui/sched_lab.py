"""T-Lab 02 — You are the scheduler. The face over `gini.domain.schedlab`.

The student is the scheduler: two threads, one atomic line per press, and a trace that is written
row for row the way the course notes draw it. Then "Try every schedule" and GINI runs every order
the scheduler could have picked and says what can happen — which is the thing no live kernel can
show, and why this lab is a simulator and not an xv6 face (see the engine's docstring).

Two halves, and the seam is the point:

* **The top half is a view model with no Qt in it.** `lane`, `trace_rows`, `state_rows`, `graph`,
  `results` and friends are pure functions from (level, code, a replay, an exploration) to what is
  drawn: which line is next, what each trace box says and what colour it is, where the staircase
  goes on the progress graph, which outcomes are possible. `Session` holds what the student has
  chosen — level, version, start, the schedule so far, the edited code in Fix it — and nothing
  else. Everything a behaviour test wants to assert is reachable here without a widget, in the
  same spirit as `lock_lab.bar_rows`.
* **The bottom half draws it.** One window, 940 × 700, the size the Machine Lab's own pages open
  at; the first, wide mockup was rejected for exactly this. The trace and the progress graph are
  painted (`_TraceView`, `_GraphView`): the mockup's grid of cells was a canvas workaround, not a
  design. The code tiles are real widgets because in Fix it they are buttons.

**Code sits on the trace.** The left panel is one four-column grid — row number, thread A, thread
B, shared state — and the trace rows use the SAME columns, so a box in the producer's column is
under the producer's code. The painted trace asks the lane widgets where they are rather than
guessing from stretch factors, which is what keeps the two aligned when a scrollbar appears.

**No worker thread, by design.** Exploring a shipped level visits a few dozen states and runs on
the GUI thread in well under a frame, so the hazard in known issue 17 (a fire-and-forget worker
emitting into a dialog being destroyed) cannot arise here. Keep it that way: if a later level ever
needs exploration off the GUI thread, read that issue first.

Colour is never the only signal. Every coloured trace row also carries words in the state column
("producer blocks", "P1 spins", ": both inside", ": deadlock"), every pinned tile says "pinned",
and every goal chip has its mark as text. The hues come from the notes' trace key on the Light
theme; on the dark themes they are mapped onto the theme's semantic tokens so the MEANINGS survive.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import pairwise

from ..domain import schedlab as S

# --------------------------------------------------------------------------- #
# the view model — no Qt
# --------------------------------------------------------------------------- #
#: What a Random press will do before giving up: long enough for any shipped level to finish or
#: stick, short enough that a level with a spin loop cannot wedge the face.
RANDOM_CAP = 80

#: The hint shown under the "GINI asks" callout while the student is rearranging lines.
FIX_HINT = ("Rearrange the lines, then press Try every schedule. "
            "You can still run it step by step.")

#: The Every schedule tab before the student has pressed the button.
GUESS_FIRST = ("Make your guess first. Then press Try every schedule and GINI runs every "
               "order the scheduler could pick, for the code as it is now.")

NO_SELECTION = "Click a line to pick it up."


@dataclass(frozen=True)
class Line:
    """One code tile as drawn: `here` is the thread's next line, `done` is behind it."""
    no: int
    text: str
    here: bool
    done: bool
    pinned: bool
    selected: bool
    editable: bool
    arrow: bool


@dataclass(frozen=True)
class Lane:
    """One thread's column: its header pill, its lines, and what its Run button says."""
    name: str
    status: str                # ready · spinning · blocked on <sem> · in critical section …
    kind: str                  # ready · spinning · blocked · cs · stuck · done
    lines: tuple
    run_label: str
    run_enabled: bool


def lane(level: S.Level, code, rep: S.Replay, t: int, editing: bool = False,
         selected=None) -> Lane:
    st = rep.st
    name = level.threads[t]
    pc, status = st["pc"][t], st["status"][t]
    th = code[t]
    cur = level.tile(th[pc]) if pc < len(th) else None
    mine = [r for r in rep.rows if r.t == t]
    spinning = status == S.READY and bool(mine) and mine[-1].kind == S.SPIN
    in_cs = bool(level.mutex_check) and S.inside(level, st, t) and cur is not None \
        and cur.mark == S.CS
    if status == S.DONE:
        label, kind = "done", "done"
    elif status == S.BLOCKED:
        label, kind = f"blocked on {cur.sem}", "blocked"
    elif rep.deadlock:
        label, kind = "stuck", "stuck"
    elif spinning:
        label, kind = "spinning", "spinning"
    elif in_cs:
        label, kind = "in critical section", "cs"
    else:
        label, kind = "ready", "ready"
    started = bool(rep.rows)
    pinned = set(level.fix.pinned) if (editing and level.fix) else set()
    lines = []
    for i, tid in enumerate(th):
        here = i == pc and status != S.DONE
        pin = tid in pinned
        lines.append(Line(
            no=i + 1, text=level.tile(tid).text, here=here, done=i < pc, pinned=pin,
            selected=selected == (t, i), editable=editing and not pin,
            arrow=here and (not editing or started)))
    if status == S.DONE:
        run = f"{name} has finished"
    elif status == S.BLOCKED:
        run = f"{name} is blocked"
    elif rep.deadlock:
        run = "Nobody can move"
    else:
        run = f"Run {pc + 1}: {cur.text}"
    return Lane(name=name, status=label, kind=kind, lines=tuple(lines), run_label=run,
                run_enabled=status == S.READY and not rep.deadlock)


@dataclass(frozen=True)
class TraceRow:
    n: int
    t: int
    text: str
    kind: str                  # plain · cs · spin · ok · blocked
    bad: bool                  # the dashed red outline: this is where it went wrong
    state: str                 # the state column, with the note folded in


def trace_rows(rep: S.Replay) -> tuple:
    """The notes' trace, one row per step. A note ("P1 spins", "wakes consumer") is appended to
    the state with a middle dot unless the row is the bad one, whose state is already the reason."""
    out = []
    for i, r in enumerate(rep.rows):
        state = f"{r.state} · {r.note}" if (r.note and not r.bad) else r.state
        out.append(TraceRow(n=i + 1, t=r.t, text=r.text, kind=r.kind, bad=bool(r.bad),
                            state=state))
    return tuple(out)


@dataclass(frozen=True)
class StateRow:
    """One object in the shared-state column. `icon` is "" , "key_free", "key_held"; `slots` is
    (filled, total) for a buffer; `chips` names the threads queued on a semaphore."""
    name: str
    kind_label: str
    value: str
    caption: str
    chips: tuple = ()
    icon: str = ""
    slots: tuple | None = None
    accent: bool = False


def _tiles_in(level: S.Level, code) -> list:
    return [level.tile(tid) for th in code for tid in th]


def state_rows(level: S.Level, code, rep: S.Replay) -> tuple:
    """The shared-state column for the code as it is: the shared variables, each thread's
    register when its code touches one, every semaphore the code uses (with its queue), and the
    buffer last. What a variable IS is read off the code: one that `test_and_set` takes is drawn
    as the key on its hook, one that is tested and set in two steps is "an ordinary variable"."""
    st = rep.st
    sh = st["shared"]
    tiles = _tiles_in(level, code)
    out = []
    capacity = sh.get("N") if "N" in level.shared else None
    for var in level.shared:
        if var == "N" or (capacity is not None and var == "items"):
            continue
        ops = {tl.op for tl in tiles if tl.var == var}
        if "test_and_set" in ops:
            holder = next((level.threads[t] for t in (0, 1) if S.inside(level, st, t)), None)
            out.append(StateRow(
                name=var, kind_label=f"held by {holder}" if holder else "key on the hook",
                value=str(sh.get(var)), icon="key_held" if holder else "key_free",
                caption="test_and_set takes the key and reports whether it was there, in one "
                        "step nobody can split."))
        elif ops & {"spin_while", "set"}:
            out.append(StateRow(name=var, kind_label="ordinary variable", value=str(sh.get(var)),
                                caption="Testing it and setting it are two separate steps."))
        else:
            out.append(StateRow(name=var, kind_label="shared", value=str(sh.get(var)),
                                caption="Both threads read it and write it."))
    for t, reg in enumerate(level.registers):
        if t < len(code) and any(level.tile(tid).reg == reg.name for tid in code[t]):
            v = st["regs"][t].get(reg.name)
            out.append(StateRow(
                name=reg.name, kind_label=f"{level.threads[t]} only",
                value=S.UNSET if v is None else str(v),
                caption=f"Private to the {level.threads[t]}, saved and restored on every "
                        "switch."))
    for sem in level.semaphores:
        if not S.uses(level, code, sem):
            continue
        q = tuple(level.threads[i] for i in st["sems"][sem]["queue"])
        out.append(StateRow(
            name=sem, kind_label=f"semaphore · {level.semaphore_notes.get(sem, '')}".rstrip(" ·"),
            value=S.sem_show(st, sem), chips=q, accent=True,
            caption="Below 0, the count says how many are waiting."))
    if capacity is not None:
        out.append(StateRow(
            name="buffer", kind_label=f"{capacity} slots", value=str(sh.get("items")),
            slots=(int(sh.get("items") or 0), int(capacity)),
            caption="The data itself. One process at a time in the buffer code."))
    return tuple(out)


@dataclass(frozen=True)
class Graph:
    """The progress graph as cells: (m+1) × (n+1) points, the edges between them, the m × n
    squares. Point kinds: current · current_stuck · stuck · visited · reachable · unreachable.
    Edge kinds: path · live · dead. Square kinds: plain · both · wall · both_wall."""
    m: int
    n: int
    path: tuple
    current: tuple
    stuck: bool
    points: dict
    edges: dict
    squares: dict
    aria: str


def graph(level: S.Level, code, rep: S.Replay, ex: S.Exploration) -> Graph:
    m, n = len(code[0]), len(code[1])
    path = tuple(tuple(p) for p in rep.path)
    visited = set(path)
    on = set()
    for a, b in pairwise(path):
        if a != b:
            on.add((a, b))
    cur = path[-1]
    reach = ex.points
    points = {}
    for i in range(m + 1):
        for j in range(n + 1):
            k = (i, j)
            if k == cur and rep.deadlock:
                kind = "current_stuck"
            elif k == cur:
                kind = "current"
            elif k in ex.stuck_points:
                kind = "stuck"
            elif k in visited:
                kind = "visited"
            elif k in reach:
                kind = "reachable"
            else:
                kind = "unreachable"
            points[k] = kind
    edges = {}
    for i in range(m + 1):
        for j in range(n + 1):
            for b in ((i + 1, j), (i, j + 1)):
                if b[0] > m or b[1] > n:
                    continue
                a = (i, j)
                edges[(a, b)] = ("path" if (a, b) in on
                                 else "live" if (a in reach and b in reach) else "dead")
    squares = {}
    for i in range(m):
        for j in range(n):
            both = level.shade_both_inside and level.tile(code[0][i]).cs \
                and level.tile(code[1][j]).cs
            wall = not any(p in reach for p in ((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)))
            squares[(i, j)] = ("both_wall" if (wall and both) else "wall" if wall
                               else "both" if both else "plain")
    aria = (f"Progress graph. This schedule has run {cur[0]} of {m} lines of "
            f"{level.threads[0]} and {cur[1]} of {n} lines of {level.threads[1]}"
            + (", and is stuck." if rep.deadlock else "."))
    return Graph(m=m, n=n, path=path, current=cur, stuck=bool(rep.deadlock), points=points,
                 edges=edges, squares=squares, aria=aria)


@dataclass(frozen=True)
class ResultRow:
    label: str
    count: str                 # "2 of 20" · "possible" · "impossible" · "never" · "proved" …
    kind: str                  # good · bad · neutral
    frac: float | None = None  # the proportion bar, when the level counts schedules
    show_label: str = ""       # "" means no button
    moves: tuple = ()
    start: int | None = None   # a Show me that also switches the start


@dataclass(frozen=True)
class Results:
    summary: str
    rows: tuple
    verdict: str               # "proved" · "not_yet" · ""
    verdict_text: str = ""


def _witness(level: S.Level, r: S.Exploration):
    """A schedule that shows why an exploration is not a proof, shortest kind first."""
    if r.deadlock is not None:
        return r.deadlock
    if r.violation is not None:
        return r.violation
    wrong = next((o for o in r.outcomes if not o.correct), None)
    return wrong.moves if wrong else None


def results(level: S.Level, version_id: str, code, start: int, proof: S.Proof) -> Results:
    """The Every schedule tab for one (level, version, start, code), from a proof that explored
    every start. The verdict box appears only where a proof is the point: in Fix it, or on a
    shipped version the level says is correct."""
    ex = proof.at(start)
    version = level.version(version_id)
    shipped = version.proves if (version_id != "fix" and version is not None) else ""
    fixing = version_id == "fix" or bool(shipped)
    rows = []
    if not ex.outcomes:
        rows.append(ResultRow("finishes", "never", "bad"))
    for o in ex.outcomes:
        if ex.total:
            rows.append(ResultRow(o.label, f"{o.paths} of {ex.total}",
                                  "good" if o.correct else "bad", frac=o.paths / ex.total,
                                  show_label="Show me one", moves=o.moves))
        else:
            rows.append(ResultRow(o.label, "possible", "neutral" if o.correct else "bad",
                                  show_label="Show me one", moves=o.moves))

    def bad(label, moves):
        if moves is not None:
            rows.append(ResultRow(label, "possible", "bad", show_label="Show me the shortest",
                                  moves=moves))
        else:
            rows.append(ResultRow(label, "impossible", "good"))

    if level.mutex_check:
        bad(level.mutex_check, ex.violation)
    bad("deadlock", ex.deadlock)
    if fixing and level.starts:
        for s, r in proof.results:
            if s == start:
                continue
            w = _witness(level, r)
            rows.append(ResultRow(
                f"Buffer starting {level.starts[s].label}",
                "proved" if r.certified else "fails", "good" if r.certified else "bad",
                show_label="Show me" if (not r.certified and w is not None) else "",
                moves=tuple(w) if w else (), start=s))
    where = f" with the buffer starting {level.starts[start].label}" if level.starts else ""
    summary = (f"GINI tried all {ex.total} schedules of this code" if ex.total
               else f"GINI visited all {ex.states} states this code can reach") + where + "."
    verdict, text = "", ""
    if fixing and proof.proved:
        verdict = "proved"
        text = shipped or (level.fix.proves if level.fix else "") or "Proved."
    elif fixing:
        verdict = "not_yet"
        at = ""
        if level.starts and proof.failing_start != start:
            at = f"with the buffer starting {level.starts[proof.failing_start].label}, "
        text = f"Not fixed yet: {at}{proof.reason}."
    return Results(summary=summary, rows=tuple(rows), verdict=verdict, verdict_text=text)


@dataclass(frozen=True)
class Chip:
    id: str
    text: str
    done: bool
    tooltip: str


def goal_chips(level: S.Level, achieved) -> tuple:
    out = []
    for g in level.goals:
        if g.version == "fix":
            where = "Fix it, then Try every schedule"
        else:
            v = level.version(g.version)
            where = (v.label if v else g.version) + (", then Try every schedule" if g.prove
                                                      else "")
        out.append(Chip(id=g.id, text=g.chip, done=f"{level.id}:{g.id}" in achieved,
                        tooltip=f"{g.text} ({where})"))
    return tuple(out)


def progress(level: S.Level, rep: S.Replay, nmoves: int) -> tuple:
    """(text, kind) for the line beside TRACE: the step count, or how the run ended."""
    if rep.deadlock:
        return "Deadlock: nobody can move", "bad"
    if rep.done:
        ok = S.is_correct(level, rep.st)
        return f"Finished · {S.outcome_label(level, rep.st)}" + ("" if ok else " (wrong)"), \
            ("good" if ok else "bad")
    if nmoves:
        return f"{nmoves} step" + ("" if nmoves == 1 else "s"), "plain"
    return "", "plain"


def hint(level: S.Level, editing: bool) -> str:
    return FIX_HINT if editing else level.hint


def fix_bar(level: S.Level, code, sel) -> tuple:
    """(text, can move up, can move down) for the Fix it toolbar."""
    if sel is None:
        return NO_SELECTION, False, False
    t, i = sel
    text = f"{level.threads[t]}, line {i + 1}: {level.tile(code[t][i]).text}"
    return text, S.can_move(level, code, t, i, -1), S.can_move(level, code, t, i, +1)


def random_extend(level: S.Level, code, start: int, moves, rng=None, cap: int = RANDOM_CAP):
    """`moves` extended with random choices until the run finishes or sticks. The randomness is
    here and not in the engine, and it takes an `rng` so a test can seed it."""
    rng = rng or random.Random()
    moves = list(moves)
    st = S.replay(level, code, moves, start).st
    for _ in range(cap):
        if S.is_done(st) or S.is_stuck(level, st):
            break
        t = rng.choice(S.enabled(st))
        st = S.step(level, st, t)[0]
        moves.append(t)
    return tuple(moves)


class Session:
    """What the student has chosen, and nothing Qt. One per window.

    Per level it remembers the version, the start, the edited code and the selected tile, so
    switching levels and back does not lose a half-made fix. The schedule and the results are
    NOT per level: every switch clears them, because a result belongs to exactly one (level,
    version, start, code) and a trace shown against different code would be a lie.

    `on_event(kind, payload)` is told when a `reach` goal is met ("goal") and whenever Try every
    schedule is pressed on a version that can be proved ("proved", with `ok`): both the proof and
    the failed attempt are part of the story the chain tells. It is a callback and not a signal
    so this class stays importable without Qt.

    And, since 2026-11-21, of what the lab ASKS for that is not a goal. A marker found T-Lab 02
    transcripts "quite minimal", and they were: a student who worked every level, ~370 actions,
    left nine entries. The lab's own first question — "of all the orders, how many end at 5?
    Guess, then switch to Lines of C and guess again" — is answered by Try every on a version
    that cannot be proved, which recorded nothing; nor did which levels were opened, so a level
    never tried read the same as one tried and abandoned. So, still never one per click:
      "level"     a level picked (the first is the window's own lab_open)
      "explored"  Try every on a version that cannot be proved, with the counts it showed
    and every payload carries `played`: how many schedules the student ran to the end on this
    level so far (Undo and run on counts again — it is effort, not distinct schedules).
    """

    def __init__(self, lab: S.LevelFile, on_event=None) -> None:
        self.lab = lab
        self.on_event = on_event
        self.level_index = 0
        self._version: dict = {}
        self._code: dict = {}
        self._sel: dict = {}
        self._start: dict = {}
        self.moves: tuple = ()
        self._results_key = None
        self.achieved: set = set()
        self.tab = "graph"
        self._proofs: dict = {}
        self._played: dict = {}
        self._last_end: dict = {}

    # -- what is loaded ------------------------------------------------------ #
    @property
    def level(self) -> S.Level:
        return self.lab.levels[self.level_index]

    @property
    def version_id(self) -> str:
        return self._version.get(self.level.id) or self.level.default

    @property
    def editing(self) -> bool:
        return self.version_id == "fix"

    @property
    def code(self) -> tuple:
        lv = self.level
        if self.editing and lv.id in self._code:
            return self._code[lv.id]
        return lv.code_of(self.version_id)

    @property
    def start(self) -> int:
        return self._start.get(self.level.id, 0)

    @property
    def selected(self):
        return self._sel.get(self.level.id) if self.editing else None

    def version_ids(self) -> tuple:
        return self.level.version_ids() + (("fix",) if self.level.fix else ())

    def version_label(self, version_id: str) -> str:
        if version_id == "fix":
            return "Fix it"
        v = self.level.version(version_id)
        return v.label if v else version_id

    def _key(self) -> tuple:
        return (self.level.id, self.version_id, self.start, self.code)

    # -- derived ------------------------------------------------------------- #
    def replay(self) -> S.Replay:
        return S.replay(self.level, self.code, list(self.moves), self.start)

    def proof(self) -> S.Proof:
        """Every start explored, memoised per (level, code): a result never changes."""
        k = (self.level.id, self.code)
        if k not in self._proofs:
            self._proofs[k] = S.prove(self.level, self.code)
        return self._proofs[k]

    def exploration(self) -> S.Exploration:
        return self.proof().at(self.start)

    def results_shown(self) -> bool:
        return self._results_key == self._key()

    def results(self) -> Results | None:
        if not self.results_shown():
            return None
        return results(self.level, self.version_id, self.code, self.start, self.proof())

    def goal_met(self, goal_id: str) -> bool:
        return f"{self.level.id}:{goal_id}" in self.achieved

    # -- choosing ------------------------------------------------------------ #
    def _clear(self) -> None:
        self.moves = ()
        self._results_key = None

    def pick_level(self, index: int) -> None:
        if not (0 <= index < len(self.lab.levels)):
            return
        moved = index != self.level_index
        self.level_index = index
        self._clear()
        if moved:
            self._emit("level", {})

    @property
    def played(self) -> int:
        """Schedules run to the end (finished or stuck) on this level so far."""
        return self._played.get(self.level.id, 0)

    def pick_version(self, version_id: str) -> None:
        if version_id not in self.version_ids():
            return
        self._version[self.level.id] = version_id
        self._clear()

    def pick_start(self, index: int) -> None:
        if not (0 <= index < max(1, len(self.level.starts))):
            return
        self._start[self.level.id] = index
        self._clear()

    # -- the schedule -------------------------------------------------------- #
    def _commit(self, moves, start: int | None = None) -> None:
        lv = self.level
        if start is not None and start != self.start:
            self._start[lv.id] = start
        self.moves = tuple(moves)
        rep = self.replay()
        if self.moves and (rep.done or rep.deadlock) and self._last_end.get(lv.id) != (
                self._key(), self.moves):
            self._played[lv.id] = self._played.get(lv.id, 0) + 1
            self._last_end[lv.id] = (self._key(), self.moves)
        met = S.goals_reached(lv, self.version_id, self.code, list(self.moves), self.start)
        for gid in met:
            k = f"{lv.id}:{gid}"
            # Told every time a goal is met, not only the first time in this window: the chip is the
            # window's, the entry is the RECORDING's. A goal met before the code was entered, or
            # while paused, lit its chip and wrote nothing, and meeting it again wrote nothing
            # either. The recorder keeps "once per goal per recording" (reported 2026-11-21).
            self.achieved.add(k)
            g = next(x for x in lv.goals if x.id == gid)
            if rep.deadlock:
                result = "deadlock"
            elif g.reach == "both_inside":
                result = "both_inside"
            elif rep.done:
                result = S.outcome_label(lv, rep.st)
            else:
                result = ""
            self._emit("goal", {"goal": gid, "moves": self.moves, "result": result})

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event is None:
            return
        base = {"lab": self.lab.id, "level": self.level.id, "version": self.version_id,
                "start": self.start, "code": self.code, "threads": self.level.threads,
                "played": self.played}
        base.update(payload)
        self.on_event(kind, base)

    def run(self, t: int) -> bool:
        """One step of thread `t`. False when that thread cannot move — the button is disabled
        then, but a test or a key press can still ask."""
        rep = self.replay()
        if rep.st["status"][t] != S.READY or rep.deadlock:
            return False
        self._commit(self.moves + (t,))
        return True

    def undo(self) -> None:
        if self.moves:
            self._commit(self.moves[:-1])

    def reset(self) -> None:
        self.moves = ()

    def random(self, rng=None) -> None:
        self._commit(random_extend(self.level, self.code, self.start, self.moves, rng))

    def show(self, moves, start: int | None = None) -> None:
        """Load a schedule from the results: an ordinary schedule afterwards, so Undo works."""
        self._commit(tuple(moves), start)

    def explore_all(self) -> S.Proof:
        lv = self.level
        proof = self.proof()
        provable = self.editing or bool(lv.version(self.version_id)
                                        and lv.version(self.version_id).proves)
        if proof.proved:
            for g in lv.goals:
                if g.version == self.version_id and (self.editing or g.prove):
                    self.achieved.add(f"{lv.id}:{g.id}")
        self._results_key = self._key()
        self.tab = "all"
        summary = {s: {"states": r.states, "total": r.total,
                       "outcomes": tuple((o.label, o.paths) for o in r.outcomes)}
                   for s, r in proof.results}
        starts = tuple(s for s, _ in proof.results)
        if provable:
            self._emit("proved", {"ok": proof.proved, "reason": proof.reason,
                                  "starts": starts, "summary": summary})
        else:
            # The counting question: what every order does to THIS code. Not a proof, so not a
            # `proved` entry ("held 0 of 3" would be a lie about a version nobody was asked to fix).
            self._emit("explored", {"starts": starts, "summary": summary})
        return proof

    # -- fix it -------------------------------------------------------------- #
    def pick_tile(self, t: int, i: int) -> None:
        if not self.editing:
            return
        lv = self.level
        if lv.fix and self.code[t][i] in lv.fix.pinned:
            return
        self._sel[lv.id] = None if self._sel.get(lv.id) == (t, i) else (t, i)

    def move_sel(self, direction: int) -> bool:
        sel = self.selected
        if sel is None:
            return False
        t, i = sel
        if not S.can_move(self.level, self.code, t, i, direction):
            return False
        self._code[self.level.id] = S.move(self.level, self.code, t, i, direction)
        self._sel[self.level.id] = (t, i + direction)
        self._clear()
        return True

    def restore(self) -> None:
        self._code.pop(self.level.id, None)
        self._sel[self.level.id] = None
        self._clear()


# --------------------------------------------------------------------------- #
# the face
# --------------------------------------------------------------------------- #
try:
    from PySide6.QtCore import QPoint, QRectF, QSize, Qt
    from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
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

    SIDE_W = 290
    ROW_H = 30

    def _qc(spec: str) -> QColor:
        if spec.startswith("rgba("):
            n = [int(float(x)) for x in spec[5:-1].split(",")]
            return QColor(n[0], n[1], n[2], n[3] if len(n) > 3 else 255)
        return QColor(spec)

    @dataclass
    class _Ink:
        """The colours, resolved once per theme. On the Light theme these are the notes' trace
        key verbatim; on a dark theme the same meanings are taken from the theme's tokens."""
        fill: dict
        border: dict
        pill: dict
        bad: str
        head: str
        state_head: str
        accent: str
        accent_soft: str
        text: str
        muted: str
        faint: str
        line: str
        line2: str
        panel: str
        panel2: str
        bg: str
        good_bg: str
        good_fg: str
        bad_bg: str
        bad_fg: str
        hatch: str

    def _ink(t) -> _Ink:
        if not t.dark:
            fill = {"plain": "#ffffff", "cs": "#fbe4e4", "spin": "#fbeed9", "blocked": "#e4e7eb",
                    "ok": "#e1f2e4"}
            border = {"plain": "#8b98a8", "cs": "#c62f2f", "spin": "#b8741a",
                      "blocked": "#7b8591", "ok": "#23863a"}
            pill = {"ready": ("#dfe8fb", "#1f4fb0"), "done": ("#e1f2e4", "#14532d"),
                    "blocked": ("#e4e7eb", "#26303c"), "stuck": ("#fbe4e4", "#8f1d1d"),
                    "spinning": ("#fbeed9", "#6b4208"), "cs": ("#fbe4e4", "#8f1d1d")}
            return _Ink(fill=fill, border=border, pill=pill, bad="#c62f2f", head="#1b2433",
                        state_head="#4f5683", accent=t.accent, accent_soft="#dfe8fb",
                        text=t.text, muted=t.muted, faint=t.faint, line=t.line, line2=t.line2,
                        panel=t.panel, panel2=t.panel2, bg=t.bg, good_bg="#e1f2e4",
                        good_fg="#14532d", bad_bg="#fbe4e4", bad_fg="#8f1d1d", hatch="#d9dee6")
        fill = {"plain": t.panel, "cs": t.danger_soft, "spin": t.warning_soft, "blocked": t.bg3,
                "ok": t.success_soft}
        border = {"plain": t.line2, "cs": t.danger, "spin": t.warning, "blocked": t.faint,
                  "ok": t.success}
        pill = {"ready": (t.accent_soft, t.text), "done": (t.success_soft, t.text),
                "blocked": (t.bg3, t.text), "stuck": (t.danger_soft, t.text),
                "spinning": (t.warning_soft, t.text), "cs": (t.danger_soft, t.text)}
        return _Ink(fill=fill, border=border, pill=pill, bad=t.danger, head=t.bg3,
                    state_head=t.accent2, accent=t.accent, accent_soft=t.accent_soft,
                    text=t.text, muted=t.muted, faint=t.faint, line=t.line, line2=t.line2,
                    panel=t.panel, panel2=t.panel2, bg=t.bg, good_bg=t.success_soft,
                    good_fg=t.text, bad_bg=t.danger_soft, bad_fg=t.text, hatch=t.line2)

    MONO = "ui-monospace,Menlo,Consolas,'DejaVu Sans Mono',monospace"

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

    class _WrapButton(QPushButton):
        """A push button whose label wraps. `QPushButton` hands its whole single-line text width
        to the layout as a minimum, and "Run 3: item = remove_item()" in a 190 px column would
        have pushed the side panel off the screen. The label is painted here, over the button's
        own (stylesheet-driven) background, so it can fold."""

        def __init__(self, label: str = "", parent=None) -> None:
            super().__init__("", parent)
            self._label = label
            self._ink = "#000000"
            self._ink_off = "#888888"
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.setFixedHeight(40)
            self.setToolTip(label)

        def set_label(self, label: str, ink: str = "", ink_off: str = "") -> None:
            self._label = label
            if ink:
                self._ink = ink
            if ink_off:
                self._ink_off = ink_off
            self.setToolTip(label)
            self.update()

        def label(self) -> str:
            return self._label

        def minimumSizeHint(self) -> QSize:
            return QSize(40, self.height())

        def sizeHint(self) -> QSize:
            return QSize(80, self.height())

        def paintEvent(self, e) -> None:
            super().paintEvent(e)
            p = QPainter(self)
            p.setFont(self.font())
            p.setPen(QColor(self._ink if self.isEnabled() else self._ink_off))
            r = self.contentsRect().adjusted(9, 3, -9, -3)
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, self._label)

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
            txt = fm.elidedText(self.text(), Qt.ElideRight, self.width())
            p.drawText(self.rect(), int(self.alignment()) | Qt.AlignVCenter, txt)

    class _Glyph(QWidget):
        """The key on its hook (level 2, test-and-set) or the buffer's slots (level 4)."""

        def __init__(self, ink: _Ink) -> None:
            super().__init__()
            self._ink = ink
            self.kind = ""
            self.slots = (0, 0)
            self.setFixedSize(40, 26)

        def set(self, kind: str, slots=None) -> None:
            self.kind = kind
            self.slots = slots or (0, 0)
            self.setToolTip({"key_free": "key on the hook", "key_held": "hook empty"}.get(kind, ""))
            self.update()

        def paintEvent(self, _e) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            k = self._ink
            if self.kind == "slots":
                filled, total = self.slots
                for i in range(total):
                    r = QRectF(2 + i * 18, 6, 14, 14)
                    if i < filled:
                        p.setBrush(QColor(k.accent)); p.setPen(QPen(QColor(k.accent), 1))
                    else:
                        p.setBrush(QColor(k.panel))
                        pen = QPen(QColor(k.border["plain"]), 1.5); pen.setStyle(Qt.DashLine)
                        p.setPen(pen)
                    p.drawRoundedRect(r, 3, 3)
                return
            if self.kind in ("key_free", "key_held"):
                free = self.kind == "key_free"
                pen = QPen(QColor(k.accent if free else k.faint), 1.8)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen); p.setBrush(Qt.NoBrush)
                p.drawLine(8, 3, 32, 3)                    # the rail
                p.drawLine(20, 3, 20, 7)                   # the hook's stem
                if free:
                    p.drawEllipse(QRectF(16.5, 7.5, 7, 7))     # the key's bow
                    p.drawLine(20, 14, 20, 24)                 # its blade
                    p.drawLine(20, 20, 23, 20); p.drawLine(20, 23, 22, 23)
                else:
                    p.drawArc(QRectF(17, 7, 7, 7), 0, -270 * 16)  # the empty hook

    class _TraceView(QWidget):
        """The trace, painted: a box per row in the column of the thread that moved, the state
        beside it. `columns()` is asked at paint time where the lanes are, so the boxes sit under
        the code whatever the splitter and the scrollbar do."""

        def __init__(self, ink: _Ink, columns) -> None:
            super().__init__()
            self._ink = ink
            self._columns = columns
            self._rows: tuple = ()
            self.setMinimumHeight(ROW_H)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        def set_rows(self, rows) -> None:
            self._rows = tuple(rows)
            self.setFixedHeight(max(ROW_H, 6 + ROW_H * len(self._rows)))
            self.update()

        def rows(self) -> tuple:
            return self._rows

        def paintEvent(self, _e) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            k = self._ink
            cols = self._columns()
            if len(cols) < 3:
                return
            mono = _mono_font(11.5)
            ui = QFont(self.font()); ui.setPixelSize(11)
            num = QFont(self.font()); num.setPixelSize(10)
            for r in self._rows:
                y = 3 + (r.n - 1) * ROW_H
                p.setFont(num); p.setPen(QColor(k.faint))
                p.drawText(QRectF(0, y, max(10, cols[0][0] - 6), ROW_H),
                           Qt.AlignRight | Qt.AlignVCenter, str(r.n))
                x, w = cols[r.t]
                box = QRectF(x + 1, y + 4, w - 2, ROW_H - 8)
                fill = QColor(_qc(k.fill.get(r.kind, k.fill["plain"])))
                pen = QPen(QColor(k.bad if r.bad else k.border.get(r.kind, k.border["plain"])),
                           1.5 if r.bad else 1)
                if r.bad:
                    pen.setStyle(Qt.DashLine)
                p.setBrush(fill); p.setPen(pen)
                p.drawRoundedRect(box, 4, 4)
                # Shrink before eliding: "register1 = register1 + 1" is the trace's whole point
                # and must stay readable in a 180 px column; a smaller face beats an ellipsis.
                f, fm, room = mono, QFontMetrics(mono), int(box.width()) - 8
                if fm.horizontalAdvance(r.text) > room:
                    f = _mono_font(10); fm = QFontMetrics(f)
                p.setFont(f); p.setPen(QColor(k.text))
                p.drawText(box, Qt.AlignCenter, fm.elidedText(r.text, Qt.ElideRight, room))
                sx, sw = cols[2]
                f = QFont(ui); f.setBold(bool(r.bad)); p.setFont(f)
                p.setPen(QColor(k.bad_fg if r.bad else k.text))
                fm = QFontMetrics(f)
                p.drawText(QRectF(sx, y, sw, ROW_H), Qt.AlignCenter,
                           fm.elidedText(r.state, Qt.ElideRight, int(sw) - 4))

    class _GraphView(QWidget):
        """The progress graph: a lattice of points, the current schedule as a staircase, walls
        where no schedule gets, red where both would be inside, × where a schedule is stuck."""

        LEFT, BOTTOM, PAD = 26, 24, 10

        def __init__(self, ink: _Ink) -> None:
            super().__init__()
            self._ink = ink
            self._g: Graph | None = None
            self._seg_h = 40
            self.setMinimumHeight(120)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        def set_graph(self, g: Graph) -> None:
            self._g = g
            self._seg_h = max(26, round(220 / max(g.m, g.n, 1)))
            self.setFixedHeight(self.PAD * 2 + g.n * self._seg_h + self.BOTTOM)
            self.setToolTip(g.aria)
            self.update()

        def graph(self) -> Graph | None:
            return self._g

        def paintEvent(self, _e) -> None:
            g = self._g
            if g is None:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            k = self._ink
            m, n = max(g.m, 1), max(g.n, 1)
            seg_w = (self.width() - self.LEFT - 2 * self.PAD) / m
            seg_h = self._seg_h

            def X(i):
                return self.LEFT + self.PAD + i * seg_w

            def Y(j):
                return self.PAD + (n - j) * seg_h

            red = QColor(k.bad)
            for (i, j), kind in g.squares.items():
                r = QRectF(X(i), Y(j + 1), seg_w, seg_h)
                if kind in ("both", "both_wall"):
                    c = QColor(red); c.setAlpha(40)
                    p.fillRect(r, c)
                if kind in ("wall", "both_wall"):
                    c = QColor(red if kind == "both_wall" else k.hatch)
                    if kind == "both_wall":
                        c.setAlpha(110)
                    p.fillRect(r, QBrush(c, Qt.BDiagPattern))
            for (a, b), kind in g.edges.items():
                if kind == "path":
                    pen = QPen(QColor(k.accent), 4)
                    pen.setCapStyle(Qt.RoundCap)
                else:
                    pen = QPen(QColor(k.line2 if kind == "live" else k.line), 1)
                p.setPen(pen)
                p.drawLine(QRectF(X(a[0]), Y(a[1]), 0, 0).topLeft(),
                           QRectF(X(b[0]), Y(b[1]), 0, 0).topLeft())
            xfont = QFont(self.font()); xfont.setPixelSize(14); xfont.setBold(True)
            for (i, j), kind in g.points.items():
                cx, cy = X(i), Y(j)
                p.setPen(Qt.NoPen)
                if kind == "current_stuck":
                    halo = QColor(red); halo.setAlpha(60)
                    p.setBrush(halo); p.drawEllipse(QRectF(cx - 11, cy - 11, 22, 22))
                    p.setBrush(red); p.drawEllipse(QRectF(cx - 7.5, cy - 7.5, 15, 15))
                    p.setPen(QColor("#ffffff")); p.setFont(xfont)
                    p.drawText(QRectF(cx - 8, cy - 8, 16, 16), Qt.AlignCenter, "×")
                elif kind == "current":
                    halo = QColor(k.accent); halo.setAlpha(60)
                    p.setBrush(halo); p.drawEllipse(QRectF(cx - 9, cy - 9, 18, 18))
                    p.setBrush(QColor(k.accent)); p.drawEllipse(QRectF(cx - 6, cy - 6, 12, 12))
                elif kind == "stuck":
                    p.setPen(red); p.setFont(xfont)
                    p.drawText(QRectF(cx - 8, cy - 8, 16, 16), Qt.AlignCenter, "×")
                elif kind == "visited":
                    p.setBrush(QColor(k.accent)); p.drawEllipse(QRectF(cx - 4.5, cy - 4.5, 9, 9))
                elif kind == "reachable":
                    p.setBrush(QColor(k.border["plain"]))
                    p.drawEllipse(QRectF(cx - 3.5, cy - 3.5, 7, 7))
                else:
                    p.setBrush(QColor(k.panel)); p.setPen(QPen(QColor(k.line2), 1.5))
                    p.drawEllipse(QRectF(cx - 3.5, cy - 3.5, 7, 7))
            lab = QFont(self.font()); lab.setPixelSize(10)
            p.setFont(lab); p.setPen(QColor(k.muted))
            for j in range(1, g.n + 1):
                p.drawText(QRectF(0, Y(j), self.LEFT - 4, seg_h), Qt.AlignRight | Qt.AlignVCenter,
                           str(j))
            for i in range(1, g.m + 1):
                p.drawText(QRectF(X(i - 1), Y(0) + 4, seg_w, self.BOTTOM - 4),
                           Qt.AlignHCenter | Qt.AlignTop, str(i))

    class SchedLab(QDialog):
        """One window: the header with the level and version buttons, the goals and the
        schedule controls, the code-over-trace panel, and the side panel with the two tabs.

        It polls nothing. Every control goes through `Session` and then `_refresh`, which
        redraws the whole face from the session — a few dozen small widgets, well under a frame,
        and no state to get out of step with the model.
        """

        def __init__(self, parent, theme: ThemeManager, lab: S.LevelFile | None = None,
                     device=None, on_event=None) -> None:
            super().__init__(parent)
            self.theme = theme
            self.device = device
            lab = lab or S.get("t-lab-02")
            if lab is None:
                raise S.LevelError("the level file t-lab-02 is not shipped with this gini-core")
            if not lab.levels:
                # A placeholder tile on the hub (released: false, no levels). The hub refuses to
                # open it; this is for anyone opening the face by hand.
                raise S.LevelError(f"{lab.title} has no levels yet")
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
            body = QHBoxLayout(); body.setSpacing(8)
            root.addLayout(body, 1)
            self._build_section(body)
            self._build_aside(body)
            self._refresh()

        # -- construction ----------------------------------------------------- #
        def _btn_css(self, dark: bool = False, dashed: bool = False) -> str:
            k = self._k
            if dark:
                return _scss(f"QPushButton{{background:{k.head};color:#ffffff;border:1px solid "
                             f"{k.head};border-radius:7px;padding:0 12px;font-size:13px;"
                             f"font-weight:600;min-height:32px;}}"
                             f"QPushButton:disabled{{background:{k.panel2};color:{k.faint};"
                             f"border-color:{k.line};}}")
            border = f"1px dashed {k.accent}" if dashed else f"1px solid {k.line2}"
            return _scss(f"QPushButton{{background:{k.panel};color:{k.text};border:{border};"
                         f"border-radius:7px;padding:0 10px;font-size:13px;min-height:32px;}}"
                         f"QPushButton:hover{{border-color:{k.accent};}}"
                         f"QPushButton:checked{{background:{k.accent_soft};color:{k.text};"
                         f"border:1.5px solid {k.accent};font-weight:600;}}"
                         f"QPushButton:disabled{{color:{k.faint};}}")

        def _build_header(self, root) -> None:
            k = self._k
            row = QHBoxLayout(); row.setSpacing(12)
            col = QVBoxLayout(); col.setSpacing(0)
            cap = QLabel(self.session.lab.title.replace(" — ", " · ").upper())
            cap.setStyleSheet(_scss(f"font-size:11px;font-weight:700;letter-spacing:1px;"
                                    f"color:{k.accent};"))
            self._level_title = _ElideLabel("")
            self._level_title.setStyleSheet(_scss(f"font-size:17px;font-weight:700;color:{k.text};"))
            col.addWidget(cap); col.addWidget(self._level_title)
            row.addLayout(col, 1)
            self._level_btns = []
            lv_row = QHBoxLayout(); lv_row.setSpacing(4)
            for i, lv in enumerate(self.session.lab.levels):
                b = QPushButton(str(lv.n)); b.setCheckable(True)
                b.setFixedSize(36, 36); b.setToolTip(f"Level {lv.n}: {lv.title}")
                b.setStyleSheet(_scss(
                    f"QPushButton{{background:{k.panel};color:{k.text};border:1px solid {k.line2};"
                    f"border-radius:7px;font-size:14px;font-weight:700;}}"
                    f"QPushButton:checked{{background:{k.head};color:#ffffff;border-color:{k.head};}}"))
                b.clicked.connect(lambda _c=False, i=i: self._pick_level(i))
                lv_row.addWidget(b); self._level_btns.append(b)
            row.addLayout(lv_row)
            self._version_row = QHBoxLayout(); self._version_row.setSpacing(4)
            self._version_btns: dict = {}
            row.addLayout(self._version_row)
            root.addLayout(row)

        def _build_goals(self, root) -> None:
            k = self._k
            row = QHBoxLayout(); row.setSpacing(6)
            cap = QLabel("GOALS")
            cap.setStyleSheet(_scss(f"font-size:11px;font-weight:700;letter-spacing:1px;"
                                    f"color:{k.faint};"))
            row.addWidget(cap)
            self._chips_row = QHBoxLayout(); self._chips_row.setSpacing(6)
            row.addLayout(self._chips_row)
            row.addStretch(1)
            self._undo = QPushButton("Undo"); self._reset = QPushButton("Reset")
            self._random = QPushButton("Random"); self._explore = QPushButton("Try every schedule")
            for b in (self._undo, self._reset, self._random):
                b.setStyleSheet(self._btn_css()); row.addWidget(b)
            self._explore.setStyleSheet(self._btn_css(dark=True)); row.addWidget(self._explore)
            self._undo.clicked.connect(self._on_undo)
            self._reset.clicked.connect(self._on_reset)
            self._random.clicked.connect(self._on_random)
            self._explore.clicked.connect(self._on_explore)
            root.addLayout(row)

        def _grid(self) -> QGridLayout:
            g = QGridLayout(); g.setSpacing(8); g.setContentsMargins(0, 0, 0, 0)
            g.setColumnMinimumWidth(0, 18)
            g.setColumnStretch(1, 100); g.setColumnStretch(2, 100); g.setColumnStretch(3, 95)
            return g

        def _build_section(self, body) -> None:
            k = self._k
            sec = QFrame()
            sec.setStyleSheet(f"QFrame#sec{{background:{k.panel};border:1px solid {k.line};"
                              f"border-radius:10px;}}")
            sec.setObjectName("sec")
            self._section = sec
            lay = QVBoxLayout(sec); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(8)
            # headers + code, one grid
            g = self._grid()
            self._heads = []
            self._pills = []
            for t in (0, 1):
                h = QFrame(); h.setStyleSheet(f"QFrame{{background:{k.head};border-radius:6px;}}")
                hl = QHBoxLayout(h); hl.setContentsMargins(8, 5, 8, 5); hl.setSpacing(6)
                name = QLabel(""); name.setStyleSheet(_scss("color:#ffffff;font-size:13px;"
                                                           "font-weight:700;"))
                pill = _ElideLabel(""); pill.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                hl.addWidget(name); hl.addWidget(pill, 1)
                g.addWidget(h, 0, 1 + t)
                self._heads.append(name); self._pills.append(pill)
            sh = _ElideLabel("")
            sh.setStyleSheet(_scss(f"background:{k.state_head};color:#ffffff;border-radius:6px;"
                                   f"padding:5px 8px;font-size:13px;font-weight:700;"))
            g.addWidget(sh, 0, 3)
            self._state_head = sh
            self._lanes = []
            self._line_boxes = []
            self._run_btns = []
            for t in (0, 1):
                w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
                lines = QVBoxLayout(); lines.setSpacing(2); v.addLayout(lines)
                run = _WrapButton("")
                run.setStyleSheet(_scss(
                    f"QPushButton{{background:{k.accent};border:1px solid {k.accent};"
                    f"border-radius:7px;font-size:13px;font-weight:600;}}"
                    f"QPushButton:disabled{{background:{k.panel2};border-color:{k.line};}}"))
                run.clicked.connect(lambda _c=False, t=t: self._on_run(t))
                v.addWidget(run); v.addStretch(1)
                g.addWidget(w, 1, 1 + t, Qt.AlignTop)
                self._lanes.append(w); self._line_boxes.append(lines); self._run_btns.append(run)
            state = QWidget(); sv = QVBoxLayout(state); sv.setContentsMargins(0, 0, 0, 0)
            sv.setSpacing(4)
            self._starts_box = QWidget(); srow = QHBoxLayout(self._starts_box)
            srow.setContentsMargins(0, 0, 0, 0); srow.setSpacing(3)
            lbl = QLabel("starts"); lbl.setStyleSheet(_scss(f"font-size:10px;color:{k.faint};"))
            srow.addWidget(lbl); srow.addStretch(1)
            self._start_btns: list = []
            self._starts_row = srow
            sv.addWidget(self._starts_box)
            self._state_col = QVBoxLayout(); self._state_col.setSpacing(4)
            sv.addLayout(self._state_col); sv.addStretch(1)
            g.addWidget(state, 1, 3, Qt.AlignTop)
            self._state_widget = state
            lay.addLayout(g)
            # fix it toolbar
            bar = QFrame()
            bar.setStyleSheet(f"QFrame#fix{{background:{k.panel2};border:1px solid {k.line};"
                              f"border-radius:8px;}}")
            bar.setObjectName("fix")
            bl = QHBoxLayout(bar); bl.setContentsMargins(8, 6, 8, 6); bl.setSpacing(6)
            cap = QLabel("Fix it"); cap.setStyleSheet(_scss(f"font-weight:700;color:{k.accent};"
                                                            "font-size:12px;"))
            self._fix_text = _ElideLabel("")
            self._fix_text.setStyleSheet(_scss(f"color:{k.muted};font-size:12px;"))
            self._up = QPushButton("Move up"); self._down = QPushButton("Move down")
            self._restore = QPushButton("Start over")
            for b in (self._up, self._down):
                b.setStyleSheet(self._btn_css())
            self._restore.setStyleSheet(_scss(
                f"QPushButton{{background:transparent;border:1px solid transparent;color:{k.accent};"
                f"font-size:12px;padding:0 8px;min-height:30px;}}"))
            self._up.clicked.connect(lambda: self._on_move(-1))
            self._down.clicked.connect(lambda: self._on_move(+1))
            self._restore.clicked.connect(self._on_restore)
            bl.addWidget(cap); bl.addWidget(self._fix_text, 1)
            bl.addWidget(self._up); bl.addWidget(self._down); bl.addWidget(self._restore)
            self._fix_bar = bar
            lay.addWidget(bar)
            # trace header
            rule = QFrame(); rule.setFixedHeight(1)
            rule.setStyleSheet(f"QFrame{{background:{k.line};border:none;}}")
            lay.addWidget(rule)
            th = QHBoxLayout()
            tcap = QLabel("TRACE")
            tcap.setStyleSheet(_scss(f"font-size:12px;font-weight:700;letter-spacing:1px;"
                                     f"color:{k.faint};"))
            self._progress = QLabel("")
            th.addWidget(tcap); th.addStretch(1); th.addWidget(self._progress)
            lay.addLayout(th)
            # the intro or the trace
            self._trace_stack = QStackedWidget()
            intro = QWidget(); il = QVBoxLayout(intro); il.setContentsMargins(2, 2, 2, 2)
            il.setSpacing(8)
            self._setup = QLabel(""); self._setup.setWordWrap(True)
            self._setup.setStyleSheet(_scss(f"font-size:13px;color:{k.text};"))
            ask = QFrame(); ask.setStyleSheet(f"QFrame#ask{{background:{k.accent_soft};"
                                              f"border-radius:8px;}}")
            ask.setObjectName("ask")
            al = QHBoxLayout(ask); al.setContentsMargins(10, 8, 10, 8); al.setSpacing(10)
            acap = QLabel("GINI asks")
            acap.setStyleSheet(_scss(f"font-size:11px;font-weight:700;color:{k.accent};"))
            acap.setAlignment(Qt.AlignTop)
            self._asks = QLabel(""); self._asks.setWordWrap(True)
            self._asks.setStyleSheet(_scss(f"font-size:13px;color:{k.text};"))
            al.addWidget(acap); al.addWidget(self._asks, 1)
            self._hint = QLabel(""); self._hint.setWordWrap(True)
            self._hint.setStyleSheet(_scss(f"font-size:12px;color:{k.muted};"))
            il.addWidget(self._setup); il.addWidget(ask); il.addWidget(self._hint); il.addStretch(1)
            self._trace_stack.addWidget(intro)
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet(self._scroll_css())
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            host = QWidget(); hv = QVBoxLayout(host); hv.setContentsMargins(0, 0, 0, 0)
            self._trace = _TraceView(k, self._trace_columns)
            hv.addWidget(self._trace); hv.addStretch(1)
            scroll.setWidget(host)
            self._trace_scroll = scroll
            self._trace_stack.addWidget(scroll)
            self._trace_stack.setMinimumHeight(70)
            lay.addWidget(self._trace_stack, 1)
            # legend
            leg = QHBoxLayout(); leg.setSpacing(5)
            for text, kind in (("critical section", "cs"), ("spinning", "spin"),
                               ("blocked", "blocked"), ("succeeds", "ok")):
                leg.addWidget(self._legend_chip(text, k.fill[kind], k.border[kind]))
            leg.addWidget(self._legend_chip("goes wrong", k.panel, k.bad, dashed=True))
            leg.addStretch(1)
            lay.addLayout(leg)
            body.addWidget(sec, 1)

        def _scroll_css(self) -> str:
            # The viewport and the host widget both get the panel colour explicitly. Left to the
            # palette, the viewport is the window colour, which on the dark themes put white
            # trace text on a light grey field.
            k = self._k
            return (f"QScrollArea{{border:none;background:{k.panel};}}"
                    f"QScrollArea > QWidget > QWidget{{background:{k.panel};}}")

        def _legend_chip(self, text: str, fill: str, border: str, dashed: bool = False) -> QLabel:
            c = QLabel(text)
            c.setStyleSheet(_scss(
                f"font-size:10px;color:{self._k.text};background:{fill};padding:1px 6px;"
                f"border-radius:4px;border:{'1.5px dashed' if dashed else '1px solid'} {border};"))
            return c

        def _build_aside(self, body) -> None:
            k = self._k
            aside = QFrame(); aside.setObjectName("aside")
            aside.setStyleSheet(f"QFrame#aside{{background:{k.panel};border:1px solid {k.line};"
                                f"border-radius:10px;}}")
            aside.setFixedWidth(SIDE_W)
            al = QVBoxLayout(aside); al.setContentsMargins(0, 0, 0, 0); al.setSpacing(0)
            tabs = QHBoxLayout(); tabs.setSpacing(0); tabs.setContentsMargins(0, 0, 0, 0)
            self._tab_graph = QPushButton("Progress graph"); self._tab_all = QPushButton("Every schedule")
            for b, name in ((self._tab_graph, "graph"), (self._tab_all, "all")):
                b.setCheckable(True)
                b.setStyleSheet(_scss(
                    f"QPushButton{{border:0;border-bottom:2px solid transparent;background:{k.panel2};"
                    f"color:{k.muted};font-size:13px;font-weight:500;min-height:36px;}}"
                    f"QPushButton:checked{{border-bottom:2px solid {k.accent};background:{k.panel};"
                    f"color:{k.accent};font-weight:700;}}"))
                b.clicked.connect(lambda _c=False, name=name: self._on_tab(name))
                tabs.addWidget(b, 1)
            al.addLayout(tabs)
            self._side = QStackedWidget()
            # graph page
            gp = QWidget(); gl = QVBoxLayout(gp); gl.setContentsMargins(12, 10, 12, 10)
            gl.setSpacing(8)
            self._graph_intro = QLabel(""); self._graph_intro.setWordWrap(True)
            self._graph_intro.setStyleSheet(_scss(f"font-size:12px;color:{k.muted};"))
            self._graph = _GraphView(k)
            gl.addWidget(self._graph_intro); gl.addWidget(self._graph)
            self._graph_legend = QLabel(""); self._graph_legend.setWordWrap(True)
            self._graph_legend.setStyleSheet(_scss(f"font-size:11px;color:{k.muted};"))
            gl.addWidget(self._graph_legend); gl.addStretch(1)
            gscroll = QScrollArea(); gscroll.setWidgetResizable(True)
            gscroll.setFrameShape(QFrame.Shape.NoFrame)
            gscroll.setStyleSheet(self._scroll_css())
            gscroll.setWidget(gp)
            self._side.addWidget(gscroll)
            # results page
            rp = QWidget(); self._results_col = QVBoxLayout(rp)
            self._results_col.setContentsMargins(12, 10, 12, 10); self._results_col.setSpacing(8)
            rscroll = QScrollArea(); rscroll.setWidgetResizable(True)
            rscroll.setFrameShape(QFrame.Shape.NoFrame)
            rscroll.setStyleSheet(self._scroll_css())
            rscroll.setWidget(rp)
            self._side.addWidget(rscroll)
            al.addWidget(self._side, 1)
            body.addWidget(aside)

        # -- handlers ------------------------------------------------------------ #
        def _pick_level(self, i: int) -> None:
            self.session.pick_level(i); self.session.tab = "graph"; self._refresh()

        def _pick_version(self, vid: str) -> None:
            self.session.pick_version(vid); self.session.tab = "graph"; self._refresh()

        def _pick_start(self, i: int) -> None:
            self.session.pick_start(i); self._refresh()

        def _on_run(self, t: int) -> None:
            self.session.run(t); self._refresh()

        def _on_undo(self) -> None:
            self.session.undo(); self._refresh()

        def _on_reset(self) -> None:
            self.session.reset(); self._refresh()

        def _on_random(self) -> None:
            self.session.random(self._rng); self._refresh()

        def _on_explore(self) -> None:
            self.session.explore_all(); self._refresh()

        def _on_show(self, moves, start) -> None:
            self.session.show(moves, start); self._refresh()

        def _on_tab(self, name: str) -> None:
            self.session.tab = name; self._refresh()

        def _pick_tile(self, t: int, i: int) -> None:
            self.session.pick_tile(t, i); self._refresh()

        def _on_move(self, direction: int) -> None:
            self.session.move_sel(direction); self._refresh()

        def _on_restore(self) -> None:
            self.session.restore(); self._refresh()

        # -- drawing --------------------------------------------------------------- #
        def _trace_columns(self) -> list:
            out = []
            try:
                origin = self._trace.mapTo(self._section, QPoint(0, 0)).x()
                for w in (self._lanes[0], self._lanes[1], self._state_widget):
                    x = w.mapTo(self._section, QPoint(0, 0)).x() - origin
                    out.append((x, w.width()))
            except RuntimeError:
                return []
            return out

        def _refresh(self) -> None:
            s = self.session
            lv = s.level
            k = self._k
            code = s.code
            rep = s.replay()
            ex = s.exploration()
            editing = s.editing
            self._level_title.setText(f"{lv.n} · {lv.title}    {lv.source}")
            self._level_title.setToolTip(f"{lv.n} · {lv.title} — {lv.source}")
            for i, b in enumerate(self._level_btns):
                b.setChecked(i == s.level_index)
            # version buttons: rebuilt per level
            _clear_layout(self._version_row); self._version_btns = {}
            for vid in s.version_ids():
                b = QPushButton(s.version_label(vid)); b.setCheckable(True)
                b.setChecked(vid == s.version_id)
                b.setStyleSheet(self._btn_css(dashed=(vid == "fix")))
                b.clicked.connect(lambda _c=False, vid=vid: self._pick_version(vid))
                self._version_row.addWidget(b); self._version_btns[vid] = b
            # goals
            _clear_layout(self._chips_row); self._chips = {}
            for c in goal_chips(lv, s.achieved):
                lbl = QLabel(("✓ " if c.done else "○ ") + c.text)
                lbl.setToolTip(c.tooltip)
                lbl.setStyleSheet(_scss(
                    "font-size:12px;padding:3px 9px;border-radius:12px;"
                    + (f"background:{k.good_bg};color:{k.good_fg};border:1px solid "
                       f"{k.border['ok']};font-weight:600;" if c.done else
                       f"background:{k.panel};color:{k.text};border:1px solid {k.line2};")))
                self._chips_row.addWidget(lbl); self._chips[c.id] = lbl
            nm = len(s.moves)
            self._undo.setEnabled(nm > 0); self._reset.setEnabled(nm > 0)
            self._random.setEnabled(not rep.finished)
            # lanes
            for t in (0, 1):
                ln = lane(lv, code, rep, t, editing, s.selected)
                self._heads[t].setText(ln.name)
                bg, fg = k.pill[ln.kind]
                self._pills[t].setText(ln.status)
                self._pills[t].setStyleSheet(_scss(
                    f"font-size:10px;font-weight:700;padding:2px 7px;border-radius:9px;"
                    f"background:{bg};color:{fg};"))
                self._fill_lines(t, ln)
                self._run_btns[t].set_label(ln.run_label, "#ffffff", k.faint)
                self._run_btns[t].setEnabled(ln.run_enabled)
            self._state_head.setText(lv.state_head)
            # starts
            self._starts_box.setVisible(bool(lv.starts))
            for b in self._start_btns:
                b.setParent(None); b.deleteLater()
            self._start_btns = []
            for i, st in enumerate(lv.starts):
                b = QPushButton(st.label); b.setCheckable(True); b.setChecked(i == s.start)
                b.setStyleSheet(_scss(
                    f"QPushButton{{background:{k.panel};color:{k.text};border:1px solid {k.line2};"
                    f"border-radius:6px;font-size:11px;min-height:24px;padding:0 5px;}}"
                    f"QPushButton:checked{{background:{k.accent_soft};color:{k.text};"
                    f"border-color:{k.accent};font-weight:700;}}"))
                b.clicked.connect(lambda _c=False, i=i: self._pick_start(i))
                self._starts_row.insertWidget(self._starts_row.count() - 1, b); self._start_btns.append(b)
            # state rows
            _clear_layout(self._state_col)
            for row in state_rows(lv, code, rep):
                self._state_col.addWidget(self._state_row(row))
            # fix it
            self._fix_bar.setVisible(editing)
            if editing:
                text, up, down = fix_bar(lv, code, s.selected)
                self._fix_text.setText(text); self._fix_text.setToolTip(text)
                self._up.setEnabled(up); self._down.setEnabled(down)
            # trace
            ptext, pkind = progress(lv, rep, nm)
            self._progress.setText(ptext)
            self._progress.setStyleSheet(_scss(
                f"font-size:12px;font-weight:600;color:"
                f"{k.bad_fg if pkind == 'bad' else k.good_fg if pkind == 'good' else k.muted};"))
            if nm == 0:
                self._setup.setText(lv.setup); self._asks.setText(lv.asks)
                self._hint.setText(hint(lv, editing))
                self._trace_stack.setCurrentIndex(0)
            else:
                self._trace.set_rows(trace_rows(rep))
                self._trace_stack.setCurrentIndex(1)
            # side panel
            self._tab_graph.setChecked(s.tab != "all"); self._tab_all.setChecked(s.tab == "all")
            self._side.setCurrentIndex(1 if s.tab == "all" else 0)
            self._graph_intro.setText(
                f"Each schedule is a staircase from bottom-left to top-right. Right: "
                f"{lv.threads[0]} runs a line. Up: {lv.threads[1]} runs a line. Numbers are "
                "line numbers.")
            self._graph.set_graph(graph(lv, code, rep, ex))
            self._graph_legend.setText(
                "━ this schedule" + ("   ▪ both inside" if lv.shade_both_inside else "")
                + "   ▨ no schedule gets here   × stuck")
            self._fill_results(s.results())
            self._trace.update()

        def _fill_lines(self, t: int, ln: Lane) -> None:
            k = self._k
            box = self._line_boxes[t]
            _clear_layout(box)
            for line in ln.lines:
                arrow = "▶ " if line.arrow else "   "
                if line.editable:
                    b = _WrapButton(f"{arrow}{line.no}  {line.text}")
                    b.setCheckable(True); b.setChecked(line.selected)
                    b.setFixedHeight(30)
                    b.setFont(_mono_font(11))
                    b.setStyleSheet(_scss(
                        f"QPushButton{{background:{k.panel};border:1px solid {k.line2};"
                        f"border-radius:5px;}}"
                        f"QPushButton:checked{{background:{k.accent_soft};"
                        f"border:1.5px solid {k.accent};}}"))
                    b.set_label(b.label(), k.text, k.faint)
                    b.clicked.connect(lambda _c=False, t=t, i=line.no - 1: self._pick_tile(t, i))
                    b.setProperty("line", line.no)
                    box.addWidget(b)
                    continue
                lbl = QLabel()
                lbl.setWordWrap(True)
                lbl.setTextFormat(Qt.RichText)
                pin = (f" <span style='color:{k.faint};font-size:10px'>pinned</span>"
                       if line.pinned else "")
                lbl.setText(f"<span style='color:{k.accent};font-size:10px'>"
                            f"{'▶' if line.arrow else '&nbsp;&nbsp;'}</span> "
                            f"<span style='color:{k.faint}'>{line.no}</span>&nbsp; "
                            f"{line.text}{pin}")
                lbl.setFont(_mono_font(11, bold=line.here and not line.pinned))
                bg = k.panel2 if line.pinned else (k.pill[ln.kind][0] if line.here else
                                                   "transparent")
                color = k.faint if (line.done and not line.here) else k.text
                lbl.setStyleSheet(_scss(
                    f"QLabel{{background:{bg};color:{color};padding:4px 6px;border-radius:5px;"
                    f"border:1px solid {k.line if line.pinned else 'transparent'};}}"))
                lbl.setProperty("line", line.no)
                box.addWidget(lbl)

        def _state_row(self, row: StateRow) -> QFrame:
            k = self._k
            f = QFrame(); f.setObjectName("srow")
            f.setStyleSheet(f"QFrame#srow{{border:1px solid {k.line};border-radius:6px;}}")
            f.setToolTip(row.caption)
            hl = QHBoxLayout(f); hl.setContentsMargins(8, 4, 8, 4); hl.setSpacing(6)
            col = QVBoxLayout(); col.setSpacing(0)
            name = _ElideLabel(row.name); name.setFont(_mono_font(12, bold=True))
            kind = _ElideLabel(row.kind_label)
            kind.setStyleSheet(_scss(f"font-size:10px;color:{k.faint};"))
            col.addWidget(name); col.addWidget(kind)
            hl.addLayout(col, 1)
            for chip in row.chips:
                c = QLabel(chip)
                c.setStyleSheet(_scss(f"font-size:10px;padding:1px 6px;border-radius:8px;"
                                      f"background:{k.fill['blocked']};"
                                      f"border:1px solid {k.border['blocked']};color:{k.text};"))
                hl.addWidget(c)
            if row.icon or row.slots:
                g = _Glyph(k); g.set("slots" if row.slots else row.icon, row.slots)
                hl.addWidget(g)
            val = QLabel(row.value)
            val.setStyleSheet(_scss(f"font-size:18px;font-weight:700;"
                                    f"color:{k.accent if row.accent else k.text};"))
            hl.addWidget(val)
            return f

        def _fill_results(self, res: Results | None) -> None:
            k = self._k
            col = self._results_col
            _clear_layout(col)
            self._show_btns: list = []
            if res is None:
                lbl = QLabel(GUESS_FIRST); lbl.setWordWrap(True)
                lbl.setStyleSheet(_scss(f"font-size:13px;color:{k.muted};"))
                col.addWidget(lbl); col.addStretch(1)
                return
            summ = QLabel(res.summary); summ.setWordWrap(True)
            summ.setStyleSheet(_scss(f"font-size:12px;color:{k.muted};"))
            col.addWidget(summ)
            for r in res.rows:
                fr = QFrame(); fr.setStyleSheet(f"QFrame{{border-top:1px solid {k.line};}}")
                fl = QVBoxLayout(fr); fl.setContentsMargins(0, 8, 0, 8); fl.setSpacing(5)
                top = QHBoxLayout(); top.setSpacing(8)
                lab = QLabel(r.label); lab.setWordWrap(True)
                lab.setStyleSheet(_scss(f"font-size:13px;color:{k.text};border:none;"))
                bg, fg = {"good": (k.good_bg, k.good_fg), "bad": (k.bad_bg, k.bad_fg)}.get(
                    r.kind, (k.panel2, k.text))
                pill = QLabel(r.count)
                pill.setStyleSheet(_scss(f"font-size:11px;font-weight:700;padding:2px 8px;"
                                         f"border-radius:9px;background:{bg};color:{fg};"))
                top.addWidget(lab, 1); top.addWidget(pill)
                fl.addLayout(top)
                if r.frac is not None:
                    track = QFrame(); track.setFixedHeight(6)
                    track.setStyleSheet(f"QFrame{{background:{k.panel2};border-radius:3px;"
                                        f"border:none;}}")
                    bar = QFrame(track)
                    bar.setStyleSheet(f"QFrame{{background:{k.border['ok'] if r.kind == 'good' else k.bad};"
                                      f"border-radius:3px;border:none;}}")
                    bar.setGeometry(0, 0, max(2, int((SIDE_W - 26) * r.frac)), 6)
                    fl.addWidget(track)
                if r.show_label:
                    b = QPushButton(r.show_label); b.setStyleSheet(self._btn_css())
                    b.clicked.connect(lambda _c=False, m=r.moves, s=r.start: self._on_show(m, s))
                    b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                    fl.addWidget(b, 0, Qt.AlignLeft); self._show_btns.append(b)
                col.addWidget(fr)
            if res.verdict:
                good = res.verdict == "proved"
                v = QLabel(res.verdict_text); v.setWordWrap(True)
                v.setStyleSheet(_scss(
                    f"font-size:13px;font-weight:600;padding:8px 10px;border-radius:7px;"
                    f"background:{k.good_bg if good else k.bad_bg};"
                    f"color:{k.good_fg if good else k.bad_fg};"))
                v.setObjectName("verdict")
                col.addWidget(v)
                self._verdict = v
            else:
                self._verdict = None
            col.addStretch(1)

        def resizeEvent(self, e) -> None:
            super().resizeEvent(e)
            self._trace.update()

    def open_sched_lab(parent, theme: ThemeManager, device=None, on_event=None,
                       lab_id: str = "t-lab-02") -> SchedLab:
        """The entry point: opens the lab with no machine, no provider and no container. A T-Lab
        is a simulator; nothing here needs Docker."""
        w = SchedLab(parent, theme, lab=S.get(lab_id), device=device, on_event=on_event)
        w.show(); w.raise_()
        return w
