---
id: os-process-tree
title: Process tree — states, sleep channels, and the control plane
subsystem: process
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/proc.c, kernel/proc.h, kernel/console.c, kernel/trap.c]
endpoints: [/procs, /kill, /control/priority, /control/tickets]
keywords: [process tree, ppid, sleeping, wait channel, chan, wakeup, sleep_prepare, lost wakeup, kill, init, sh, sigalarm, zombie, runnable]
---

# Process tree — states, sleep channels, and the control plane

## What is on the screen

`process_tree.py` — a tree built from each proc's ppid, columns
process / pid / state / kill. States are color-coded. For sleeping processes the
state cell appends the **wait-channel label** ("sleeping · a child to exit")
with a `wakeup(0xADDR)` tooltip — "without it a blocked process is a dead end".
Scheduling flags (starving / monopolising CPU, from
`MachineState.scheduling_flags()`) override the cell with a red ⚠ reason. Every
user process (pid > 2) gets a Kill button whose `killing…` pending state
survives the ~0.5 s tree rebuilds.

## What it is doing

The tree answers *which processes exist, how they are related, and what each is
waiting for*. The channel column is the teaching payload: xv6 blocks on an
**address** — `sleep(chan)` parks the process, `wakeup(chan)` walks the process
table matching that address. An idle machine is two processes parked on two
addresses (init on "a child to exit", sh on "console input"), and a machine that
has stopped is a cycle of processes each waiting on the next.

A process can show **runnable with a channel still set** — that is not a bug but
the `sleep_prepare`/`sleep` window, the snapshot of the exact instant the
lost-wakeup design protects. (Course guide: *Who Wakes You?*)

## How it is bolted into xv6

- **ppid**: `gini_dump` prints `p->parent->pid` read best-effort without
  `wait_lock` — proc[] slots are never freed, so the pointer is always valid.
  Stock Ctrl-P procdump also gains the ppid column (§4b′).
- **Channel naming** (§4h6, ~2118–2158): each subsystem registers the address or
  range it sleeps on with a human name at init — registration rather than a
  lookup table, because the interesting structs (bcache, log, cons) are static to
  their own files and cannot be named from outside. Registry `GINI_NCHAN = 12`;
  registered: console input, the log, a child to exit, a disk block, an inode,
  room in the UART buffer, a free disk descriptor, the next timer tick. Pipes are
  allocated per-pipe so they stay raw addresses — an honest gap: two processes on
  different raw addresses wait on different things, and a reader and writer on
  the *same* address is a found deadlock.
- **WAIT lines** are printed for every proc with `p->chan != 0`, not only
  SLEEPING ones — that is what makes the lost-wakeup window visible.
- **Control-plane kill** (§4f2): Ctrl-Y digit-entry fires `gini_kill(pid)`
  straight from the UART interrupt — no shell scheduling, so the Kill button
  cannot be starved by the workload it is killing (typed `kill <pid>` could).
  Both agent and kernel refuse pid ≤ 2 (init, sh).
- **Priority/tickets setters** (§4f3): Ctrl-O / Ctrl-N two-number entry
  (`<pid> <val>`), same interrupt-context firing.
- **sigalarm fields** (§1d): `gini_alarm_handler/interval/ticks/on` are
  GINI-owned proc fields (so the dump always compiles) that the *student's*
  `sigalarm`/`sigreturn` implementation drives; shown in the Trap Lab strip.

## Wire format

Proc rows `pid state name ppid`; `PROC` sched fields; `WAIT pid addr name`;
`ALARM pid interval ticks handler on`. See
[os-wire-protocol](os-01-wire-protocol.md).

## Limits and honesty

- The state word is a point-in-time sample at ~2 Hz; short-lived processes can
  be missed entirely between polls.
- `"?"` channels are almost always pipes; the raw address still distinguishes
  waits.
- A chatty user program can corrupt one poll's dump and momentarily drop a
  process from the table (shared serial wire — see
  [os-architecture](os-00-architecture.md)).

## Cross-references

[os-scheduler](os-02-scheduler.md) · [os-traps](os-06-traps.md) ·
[os-wire-protocol](os-01-wire-protocol.md)
