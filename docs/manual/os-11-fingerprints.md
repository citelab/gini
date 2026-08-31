---
id: os-fingerprints
title: Process Fingerprints — radar, scatter, classifier vs grader
subsystem: fingerprints
layer: [domain, ui]
kernel_files: []
endpoints: [/procs, /sc, /traps]
keywords: [fingerprint, radar chart, scatter, classify, cpu-bound, io-bound, memory, fork-heavy, mixed, workload, personality, accumulator, thresholds]
---

# Process Fingerprints — radar, scatter, classifier vs grader

## What is on the screen

`fingerprint_lab.py`, two tabs:

- **Explore** — a process list feeding a 5-axis radar chart (cpu, syscalls,
  io_wait, faults, forks) and a scatter board where programs sort themselves
  (x = CPU-bound ↔ IO-bound, y = compute ↔ heavy-kernel).
- **Classify game** — the shared diagnose-game widget on the process spec:
  guess cpu-bound · io-bound · memory · fork-heavy · mixed from the shape alone.

## What it is doing

Programs have personalities, and the personality is measurable — "know your
workload" as a coordinate rather than advice. The tells: real I/O spikes
syscalls *and* io_wait together (syscalls without waiting is something else);
memory work shows in faults; fork-heavy is unmistakable once seen.

In live mode a `FingerprintAccumulator` builds features over time on a 1.2 s
poll, ingesting procdump states plus **deduplicated** `/sc` and `/traps` events
(a 4000-entry seen-set that clears on overflow). The catalog's classify game is
demo-only (`live=False`) precisely because it needs accumulation; the live
version lives here, where the accumulator is owned.

### Classifier vs grader — deliberately separate

- The **hint** is `fingerprint.classify()` — ordered threshold rules
  (`fork 0.4, flt 0.3, io 0.35, sys 0.4, cpu 0.5`), inspectable and tunable
  ("thresholds are tunable" *is* an assignment).
- The **grader** is `GROUND_TRUTH` — the oracle's knowledge of what each shipped
  program really is. "It grades, it never classifies."
- `grind` is labeled fork-heavy and can honestly be confused with io-bound —
  that overlap is the lesson, and it shows up as a hot confusion-matrix cell.

## How it is bolted into xv6

No dedicated kernel instrumentation — fingerprints are a pure-domain derivation
(`fingerprint.py`) over feeds documented elsewhere: proc states
([os-process-tree](os-03-process-tree.md)), syscall counters
([os-syscalls](os-07-syscalls.md)), trap taxonomy ([os-traps](os-06-traps.md)).

## Wire format

None of its own. Consumes `/procs`, `/sc`, `/traps`.

## Limits and honesty

- Features need time to accumulate — a just-launched program has no shape yet.
- "mixed" is a real class, not a shrug; a full mixed *column* with an empty
  mixed *row* in the confusion matrix means hedging (see
  [os-games](os-12-games.md)).

## Cross-references

[os-games](os-12-games.md) · [os-programs](os-14-programs.md) ·
[os-syscalls](os-07-syscalls.md)
