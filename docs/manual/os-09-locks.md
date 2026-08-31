---
id: os-locks
title: Locks face — contention telemetry
subsystem: locks
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/spinlock.c, kernel/spinlock.h]
endpoints: [/locks, /locks/reset]
keywords: [lock, spinlock, contention, acquires, spins, test-and-set, multicore, SMP, hot lock, cores]
---

# Locks face — contention telemetry

## What is on the screen

`lock_lab.py`: horizontal bars for the top 8 locks by acquires, scaled to the
worst offender, **red above 0.5 spins/acquire**, each labeled
`N.NN spins/acq · N,NNN acq`. A "Reset counters" button (→ Ctrl-Z) zeroes the
counters so one workload can be measured in isolation. A note line branches on
the core count: on 1 core it says contention is **impossible by construction**
and tells the student to set the element Size to L/XL and reboot. 1.2 s poll;
offline shows "open this on a running xv6 Machine".

## What it is doing

Contention is the canonical invisible problem: a core spinning on a lock looks
exactly like a core doing work. The two numbers separate it:

- **acquires** — times the lock was successfully taken (frequency of use).
- **spins** — failed test-and-set attempts inside the acquire loop (CPU time
  burned waiting).

Their ratio is the number that matters. Run several CPU-heavy programs on a
multi-core machine and watch which lock heats up (course activity C10).

## How it is bolted into xv6

§3f. Counters are **aggregated by lock name, not per instance** — xv6 has 64
per-proc locks all named "proc"; the slot is resolved once in `initlock`
(`gini_lock_slot`) and cached in `lk->gstat`, keeping `acquire()` O(1).
`GINI_NLOCK = 24`; a 25th distinct name goes uncounted rather than mis-counted.
`spins` increments inside the atomic-exchange loop; both counters use
`__sync_fetch_and_add` since same-named locks share a slot. Rationale for
instrumenting here at all: this is the one phenomenon in the kernel that is
normally taught by reading numbers a test program prints — here you change
something and watch the number move.

Core count comes from the element Size tier (S/M→1, L→2, XL→4 harts), reported
as `LOCKCPU` so the UI can explain a structurally-zero display.

## Wire format

`LOCKCPU <ncpu>` · `LOCK <name> <acquires> <spins>`. Parser sorts most-spins
first; `contention = spins/acquires`. See
[os-wire-protocol](os-01-wire-protocol.md).

## Limits and honesty

- Cumulative counters with a manual reset — no windowing; measure one workload
  by resetting first.
- Name aggregation means "proc" is the *family* of 64 locks, not one lock.
- Single-core machines legitimately show all zeros; the UI says so rather than
  looking broken.
- Spinlock code is deliberately excluded from kernel-board probes — lock time
  belongs on block borders, and is measured here instead.

## Cross-references

[os-kernel-board](os-08-kernel-board.md) · [os-architecture](os-00-architecture.md)
