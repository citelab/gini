---
id: os-banker-lab
title: T-Lab 03 — Simulate deadlock handling
subsystem: banklab
layer: [domain, ui, teaching-center]
kernel_files: []
endpoints: []
keywords: [T-Lab, deadlock, resource-allocation graph, claim edge, request edge, allocation edge, graph reduction, knot, banker's algorithm, safe state, safe sequence, unsafe, detector, wait-for graph, recovery, victim, terminate, rollback, checkpoint, preempt, every order, simulator]
---

# T-Lab 03 — Simulate deadlock handling

## What is on the screen

`bank_lab.py`, opened from a **T-Lab tile on the User Code hub** (Machine Lab → User Code).
One window, 940 × 700, the size T-Lab 02 and the Machine Lab's own pages open at. It needs **no
machine running and no Docker**: a T-Lab runs in GINI's own simulator.

Top: the level buttons **1–5**, the **version** buttons on level 2 (*Grant when free · Graph
algorithm*), the **goal chips** (`○` not yet, `✓` met) and **Undo · Reset · Random · Try every
order** (no Random on the bank level). Under them the **Start** switch: every level opens on a
start taken from the notes, so a student can follow a worked example with its solution beside
them; goals that could be answered by copying the notes are set on a start called **Yours**.

**Run levels (1, 2, 4, 5).** Left, top: **the graph**, drawn as the notes draw it (Figure 14.1) —
a circle per process, a box per resource with one dot per unit; a *claim* edge dotted and grey,
a *request* edge dashed and orange, an *allocation* edge solid and navy, leaving from the dot of
the unit held. Under each circle a word: *active*, *blocked*, *deadlocked*, *done*,
*terminated*, *waits — would close a cycle*. When the detector finds a deadlocked set those
circles are filled red; edges on a cycle are thicker; under the graph algorithm, the cycle a
refused grant would have closed is shaded. **Hide the graph** blanks it, so a student can draw
it on paper from the Tables tab first and then compare. On level 5, from a start where every
resource has one unit, a switch shows the **wait-for graph** instead (Figure 14.9). Below the
graph, one row per process — a pill (`running`, `waits for R2`, `waits: would close a cycle`,
`done`, `terminated`) and a button per possible step (`P2 asks for R2`, `P2 finishes`) — and the
**trace**, one line per move, ending with how the run stands (`Deadlock: nobody can move. P1,
P2, P3 wait forever.`).

Right, the tabs: **Tables** (Hold, Request, Max where declared, CPU used where recovery is
offered, and Avail), **Reduce** (every simple cycle in the graph, then the graph reduction of
§14.3.1 as rows — erase · request ≤ Work · Work after — and who is left; the circles are
numbered in erase order), **Recover** (only where the start declares recovery: the work thrown
away so far, each live process's CPU used, checkpoint and when it took each resource, and one
button per recovery action with its cost — `Terminate · 2 h lost`, `Roll back to minute t ·
n min lost`, `Preempt R · n min lost`; once the detector finds no deadlock it says so, and
whether nobody could have got out for less, never how), and **Every order** (what Try every
order found: each outcome, `possible`, the length of the shortest order, *Show me the shortest*;
on the Graph algorithm, the green `Proved: …` box or a red `Not proved: …`).

**The bank level (3).** Left: the notes' tables (Worked example 14.7) — **Max · Hold · Need**,
with a provisional change written `2 → 3` — and Avail; the **Requests** list (*No request: is
this state safe?* first); and the three tests of "a request arrives" in the notes' order
(`1. Req ≤ Need2? (1, 0, 1) ≤ (1, 2, 1): yes`). On *Yours*, Need and Avail show as `?` until
**Show Need and Avail** is pressed: they are to be worked out by hand first. Right: the **Safety
check**, run by the student in the notes' columns (Worked example 14.5: *try · Need ≤ Work? ·
result*, "no" rows kept), with a **Try Pn** button per process and **Nobody fits: unsafe**, then
**Grant · Wait · Error** and GINI's verdict in one sentence; and **Every order**, every safe
sequence of the state (after the provisional grant, when one applies).

Colour is never the only signal: every process state, every check answer (`yes` / `no`) and every
provisional change is also written in words or arrows.

## What it is doing

The engine is `gini.domain.banklab`, pure Python with no Qt. One model for all five levels:
processes, resource types with units, and per process what it holds, what it waits for, and
(where declared) its maximum. A start may bring its own resources and processes
(`banklab.world`), so level 3 has a one-resource bank beside a three-resource one and level 5
detectors of three and four resource types.

On a run level the student picks which process takes its next step — the next line of its script
(ask for units, or finish) or, where the level allows, finishing early — and a **policy** decides
each request: *Grant when free*, or the **graph algorithm** of §14.6.2 (refuse a grant that would
close a cycle counting claim edges; single-unit resources only, and the loader enforces it). A
refused or unsatisfiable request makes the process wait; after every release the waiters are
re-tried, oldest first.

| level | notes | the lesson |
|---|---|---|
| 1 · Read the graph | 14.3, Worked examples 14.1, 14.2, Figure 14.3 | with single-unit resources a cycle is a deadlock (P3 asking for the scanner closes 14.1's chain); with two units a cycle may reduce away (Figure 14.3 erases P2, P1, P3, P4) — until it becomes a knot |
| 2 · Claim edges | 14.6.2, Worked example 14.4, Figure 14.8 | granting when free, the shortest order to deadlock is 6 steps; under the graph algorithm P3's request for a free R3 is refused, and Try every order **proves** that no order ends in deadlock |
| 3 · The banker | 14.6.3, Worked examples 14.5 to 14.7 | the three tests and the safety check by hand; in 14.7, P2's request is granted (⟨P2, P3, P1, P4⟩), P1's and P3's wait, P4's is an error |
| 4 · Unsafe, but lucky | Worked examples 14.6, 14.11, Your turn 14.12 | an unsafe state is not a deadlock: from 14.6's state some orders finish and some deadlock |
| 5 · Detect and recover | 14.7, Figure 14.9, Worked example 14.8 | the detector is the reduction with Request in place of Need; who is deadlocked (14.8: P2, P3, P4) is not everyone who waits; termination, rollback to a checkpoint and preemption each throw work away, and the cheapest victim is not always the cheapest way out |

**Recovery is rollback.** Terminating a process rolls it back to its start (it loses all its CPU
time and gives back everything); rolling back returns it to its checkpoint and gives back what it
took since; preempting R returns it to just before it took R and gives back R and what it took
after. A rolled-back process runs again and asks again — and **the detector counts what it must
take again as its request**, so a rollback that gave back nothing does not pass for a recovery.

**Try every order** is a breadth-first search over the states the processes can reach (recovery
actions left out: they are a repair, not a future), so the first order found for each outcome is
a shortest one. It is deterministic. On the bank level it lists every safe sequence instead.

The goals: on run levels a goal is met when the state after a move meets it, on the goal's start
and version — some process deadlocked, a cycle that is not a deadlock, a process waiting
although the units it asked for are free, everyone finished, one termination that breaks the
deadlock and is the cheapest that does, or a recovery that throws away no more work than the
cheapest possible. On level 2 the proof is met by Try every order on the Graph algorithm. On the
bank level: a hand-run check of the state with *No request*, run to the end; **Grant** pressed on
a request the banker grants, after a check run to the end; **Wait** pressed on a request that
would leave the state unsafe, after *Nobody fits: unsafe* was accepted — which GINI does only
when no unfinished process fits in Work, and otherwise says which one still does.

## How it is bolted in

It is not. **No kernel is involved.** The face polls nothing and runs no worker thread — Try
every order on a shipped level visits at most a few hundred states on the GUI thread in a few
milliseconds — so the hazard in [known issue 17](os-15-known-issues.md) cannot arise here.

What IS bolted in is the proof chain, with event kinds of its own (T-Lab 02's `schedule` would be
replayed with the wrong engine). Opening the lab records a `lab_open`. Reaching a goal records
**one entry per goal per level, the first time** — never per click:

- **`allocate`** on a run level: lab, level, version, start, the moves (`[[process, kind], …]`,
  exactly as `banklab.replay` takes them, up to the step that met the goal), the goal and a few
  words of result (`deadlock: P1, P2, P3`).
- **`bank`** on the bank level: the request (or none), the hand-run check rows, the decision and
  the sentence GINI said.
- **`orders`** on every *Try every order* of a version that can be proved (level 2, Graph
  algorithm), held or not: the verdict, the reason, and per outcome how many orders reach it.

The Teaching Center does **not yet re-check** these entries: its report shows them as *not
checked*, never as failing. The engine ships in gini-core, so a later Teaching Center can replay
an `allocate` entry and re-explore an `orders` one exactly as it does T-Lab 02's.

## Wire format

There is no wire. The level file is `gini/domain/tlabs/t-lab-03.yaml`; its top-level
`engine: bank` is what makes `tlab_catalog` read it with `banklab` rather than `schedlab`. A level
declares `mode` (`run` or `bank`), its resources (`name`, `units`, optionally `preemptable`) and
processes (`name`, optionally `max`, `cpu`, `took`, `checkpoint`), `versions` (each a `policy`:
`free`, `graph` or `banker`, optionally `proves`), `starts` and `goals`. A start gives `hold`,
`pending`, the run levels' `scripts` (`{ask: [...]}` or `finish`, per process), the bank level's
`requests`, a `layout` for the graph (positions in 0..1), optionally its own `resources` and
`processes`, `recovery`, `note` and `hide_need`. Vectors list one number per resource, in the
order the resources are declared. The loader refuses a bad file with one sentence naming what is
wrong (`claims: the graph algorithm needs single-unit resources`).

## Limits and honesty

- **Recovery actions are not explored.** Try every order leaves them out by design; the cheapest
  recovery is found separately, over at most three actions.
- **The banker policy exists in the engine but no shipped run level uses it**; the bank level is
  where the banker is taught, by hand.
- **The graph layout is the level file's.** Nothing re-lays the graph out when edges cross; a new
  start needs positions that read well.
- **No animation** of the reduction: the circles are numbered in erase order instead.
- Exploration stops at 20 000 states and then says so; nothing below that line is a proof. No
  shipped level comes near it.
- **Progress is not persisted** between sessions: the goal chips reset when the window closes.
  What persists is the chain, when a code is armed.

## Cross-references

[os-scheduler-lab](os-17-scheduler-lab.md) (T-Lab 02, the same frame) ·
[os-known-issues](os-15-known-issues.md) §17 · [os-glossary](os-16-glossary.md) ·
the User Code hub (`ui/user_code_lab.py`) · the engine (`core/src/gini/domain/banklab.py`) ·
the catalog (`core/src/gini/domain/tlab_catalog.py`) · the Teaching Center's report
(`activities.recheck_tlabs`).
