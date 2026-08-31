---
id: os-scheduler
title: Scheduler face — gini_pick, policies, quantum, Gantt
subsystem: scheduler
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/proc.c, kernel/proc.h, kernel/trap.c, kernel/console.c]
endpoints: [/procs, /control, /control/priority, /control/tickets, /step, /snapshot, /run, /kill]
keywords: [scheduler, round-robin, priority, lottery, quantum, time slice, Gantt, aging, starvation, tickets, gini_pick, yield, preemption]
---

# Scheduler face — gini_pick, policies, quantum, Gantt

## What is on the screen

Opened from the Machine Lab as its own window (`machine_lab.py`):

- **Controls bar**: Time-slice slider 1–10 ticks (label "~N×0.5 s slice";
  committed once on release, off the GUI thread); Policy combo populated from
  the kernel's live `POLICY` roster; a "kernel: <name>" confirmation label;
  Play → guess-the-scheduler game; **Step** (one context switch via gdb) and
  **Run/Pause** (500 ms poll — polling, not stepping); a `N sw · X/s · Q<q>`
  counter.
- **Gantt strips** — one per CPU, one cell per snapshot, golden-angle color per
  pid, pid label only at color changes, current pid in a right gutter.
- **Launcher** — program combo (see [os-programs](os-14-programs.md)), argument
  box with per-program placeholder, inline hint, and a red refusal label (the
  agent's "image built before this program" message surfaces here).
- **Scheduling controls** — pid combo (synced to live procs), priority spinbox
  0–30 ("lower = higher"), tickets spinbox 1–100, Set.
- **Shadow bar** — status line, "Use my shadow" checkbox, Load (rebuild), Revert,
  inline compile log. Six distinguishable states — see
  [os-shadows](os-13-shadows.md).
- **Four panels**: Processes (tree), CPU registers per hart, Scheduling (ready
  queue + `████░░ 62%` share bars), Kernel stack (populated only after Step —
  the empty state explains why).

## What it is doing

The Gantt strip is the policy made visible: round-robin reads as even stripes,
priority as one process holding on, lottery as favoritism without regularity.
The quantum slider changes stripe width. Everything on this face except
Step/Snapshot comes from the `/procs` dump at 500 ms.

`SchedTimeline.add_run` records a slot when the pid changes *or* the tick
advances, so at the ~0.5 s tick the strip is a faithful switch history, not a
sampling artifact.

## How it is bolted into xv6

**The picker** (`gini_patch.py` §1, ~79–240). Two globals in proc.c:
`sched_policy` (0 RR, 1 priority, 2 lottery) and `sched_quantum` (ticks per
slice). §1b replaces the stock scheduler loop — the anchor requires both
`if (p->state == RUNNABLE)` and `swtch(...)` so it can't hit `allocproc`'s loop
— with:

```c
p = gini_pick();
if(p){ acquire(&p->lock);
       if(p->state == RUNNABLE){ p->state = RUNNING; c->proc = p;
         swtch(&c->context, &p->context); c->proc = 0; }
       release(&p->lock); }
else { intr_on(); asm volatile("wfi"); }
```

The pick is lock-free and re-validated RUNNABLE under the lock — a stale read
costs at most one wasted slice. If the anchor misses, the kernel builds and runs
stock RR.

**The three policies** inside `gini_pick` (~168–224), each consulted *after*
giving the shadow first refusal:

- **0 round-robin**: static cursor scans `proc[(rr+i) % NPROC]`, first RUNNABLE
  wins, cursor advances past it. Matches stock xv6.
- **1 priority with aging**: for each RUNNABLE proc, `wait_ticks++`, effective
  priority `eff = priority − wait_ticks/8`, minimum eff wins; ties go to the
  lowest proc[] slot; winner's `wait_ticks` resets. The weak `/8` aging plus
  low-slot ties is the **documented starvation bug** — deliberately left as lab
  material.
- **2 lottery, deliberately flawed**: xorshift PRNG (seed 2463534242, shifts
  13/17/5), uniform draw over RUNNABLE procs — it **ignores `p->tickets`**.
  Weighting the draw by tickets *is* the assignment.

**Per-proc fields** (§1c, proc.h after `name[16]`): `priority` (default 10),
`tickets` (1), `level` (0, MLFQ for student policies), `wait_ticks` (0).

**Quantum** (§2, ~313–326): per-CPU `gini_qticks[NCPU]` (a single global would be
bumped by every hart, shrinking slices ×1/ncpu). Both preemption points rewritten:
usertrap and kerneltrap yield only when `++gini_qticks[cpu] >= sched_quantum`.

**Tick rate** (§2b): `clockintr` timer reprogrammed from 1 000 000 to 5 000 000
on QEMU's 10 MHz timebase → **~0.5 s per tick, ~2/s**. That is what makes the
Gantt watchable; consequences: quantum 1–10 = 0.5–5 s slices, user programs
count `seconds*2` ticks, board residency accrues at ~2 samples/s, and ticks
cannot order events — hence the separate monotonic event clock (`gini_seq`)
stamped into the trap/syscall/fault rings.

**Controls**: quantum Ctrl-\ / Ctrl-] ; policy Ctrl-B (+ Ctrl-G, currently
broken — see [os-known-issues](os-15-known-issues.md)); per-proc setters Ctrl-O
(priority) and Ctrl-N (tickets), fired from the UART interrupt so a setter can't
be starved by the workload it targets.

## Wire format

`SCHED policy <n> quantum <n>` confirms a control landed; `POLICY <id> <name>` is
a data-driven roster so a new kernel policy auto-appears in the combo; `PROC`
lines carry pri/tickets/level/wait_ticks; `CPU`/`REGS` feed the Gantt and the
register panel. See [os-wire-protocol](os-01-wire-protocol.md).

## Limits and honesty

- CPU-share numbers are sampled from the Gantt (last 60 slots, non-idle), not
  read from a kernel accountant — coarse but a real observation.
- `wait_ticks` is the kernel's own aging counter and doubles as the starvation
  metric (`Xv6Runner.max_wait_slices`).
- Step uses gdb (`tbreak swtch; continue`) and halts the kernel briefly; on an
  idle kernel it times out harmlessly.
- Lottery over a short window is indistinguishable from priority — length of
  evidence is part of the lesson (C17 in the course plan).

## Cross-references

[os-shadows](os-13-shadows.md) · [os-process-tree](os-03-process-tree.md) ·
[os-programs](os-14-programs.md) · [os-games](os-12-games.md)
