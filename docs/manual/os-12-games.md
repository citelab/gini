---
id: os-games
title: The OS Games — engine, decks, matrices, scoring
subsystem: games
layer: [domain, ui]
kernel_files: []
endpoints: [/traps, /procs]
keywords: [games, practice, graded, deck, confusion matrix, hint, classifier, scoring, tolerance, rank, estimate, Belady, paging, translate, thrashing, live cases, demo deck]
---

# The OS Games — engine, decks, matrices, scoring

## What is on the screen

`games_lab.py` is a list-left / game-right shell over the catalog; each Lab
face also has a "Play" door that opens one game as its own window. A game shows
a **signature** (radar, Gantt snippet, raw scause, reference string, page
table…), answer controls for its kind, a score line, and — for classification
games — a live **confusion matrix** (true × guessed, diagonal green,
off-diagonal red, alpha scaled to the hottest cell).

Registered games (`game_catalog.py`): process-classify · guess-policy ·
trap-cause · fault-type · thrash-diagnose · faults-estimate · belady-spot ·
policy-showdown · next-evict · policy-rank · addr-translate.

## What it is doing

The games ask: *shown only the evidence, can you say what the operating system
was doing?* Wrongness is the mechanism — the **pattern** of mistakes is more
informative than the score. One hot cell in one direction = one nameable,
fixable misconception (the best possible result); scattered off-diagonal =
guessing; a full column with an empty row = a favorite guess, hedging.

- **Practice** deals cases forever, reveals immediately, and shows the
  rule-based classifier's hint — a baseline to beat, not an oracle.
- **Graded** deals a fixed deck (default 10) with hints suppressed, then
  finishes: "Run complete — 7/10 (70 %)."

### Live vs demo dealing

Every catalog entry's `source()` tries live first, falls back to its demo deck:
guess-policy builds cases from the real Gantt + the kernel's actual policy
(needs ≥6 pids); trap-cause and fault-type from the real trap ring;
**addr-translate from real page tables** of the running machine. The best move
in the system: a live case is your own machine asking what you just did to it.
The paging six (faults-estimate, belady-spot, policy-showdown, next-evict,
policy-rank, thrash-diagnose) are simulator-backed and offline by construction.
The deck is re-sourced on every new mystery, so a live deck grows as programs
run.

## How it is bolted into xv6

No kernel instrumentation of its own. The engine is pure and deterministic
(`diagnose.py`, 202 lines): a `Case` is `(id, signature, truth, subtitle, hint,
options)` — "the label on every Case is derived from real kernel state — never
guessed, never from an LLM." Case selection uses a **seeded** RNG so a mission
can replay the same deck.

**Scoring** by answer kind: class/spot = exact; estimate = within `tolerance`
(absolute or relative; 0 = exact, which addr-translate uses); rank = normalized
pairwise (Kendall-style) order agreement with partial credit. Score line adds
mean error (estimate) or order % (rank).

**Confusion matrix**: the session stores only `pairs: [(truth, guess)]`,
folded on demand; in-memory, per-session, cleared by reset — **no persistence
today** (relevant to using graded decks as C-lab instruments: capture would
need to be added or read out at submit time). Per-class recall/precision is
computed but not currently drawn.

**Hints** are separate, inspectable heuristics per game — e.g. policy:
dominant >55 % → priority, strictly periodic → round-robin, else lottery
("explicitly not the grader").

## Wire format

None of its own; live sources consume `/traps`, `/procs`, and the VM snapshot.

## Limits and honesty

- Graded results are not persisted anywhere — closing the window loses the run.
- Fault-type's live source depends on real faults existing; launch `alloc`.
- Over a short Gantt window lottery is indistinguishable from priority — the
  games treat "you need a longer sample" as a skill, not a failure.

## Cross-references

[os-fingerprints](os-11-fingerprints.md) · [os-memory](os-04-memory.md) ·
[os-traps](os-06-traps.md) · [os-scheduler](os-02-scheduler.md)
