---
id: os-traps
title: Traps face — taxonomy, live feed, trap-catch
subsystem: traps
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/trap.c]
endpoints: [/traps, /trapcatch, /procs]
keywords: [trap, interrupt, exception, scause, taxonomy, syscall, page fault, timer, device, illegal, sigalarm, trap catch, freeze, sepc, stval, sstatus]
---

# Traps face — taxonomy, live feed, trap-catch

## What is on the screen

`trap_lab.py`:

- **Six taxonomy bars** — syscall · pagefault · timer · device · illegal ·
  other, as **rates over the last 60 s** (`TrapRate` differencing the cumulative
  TC counters). All six always drawn, zeros included, so the mix is fully
  visible.
- **Live feed** — `pid kind epc tval` lines from the TR ring.
- **sigalarm strip** — shown when an alarm is armed: "⏰ pid N · every K ticks ·
  fires in R · handler 0x… · handler RUNNING" (parsed from `/procs` ALARM lines).
- **"Step a trap ▸"** with a `catch:` combo (any / pagefault / syscall / timer /
  illegal / device) → freezes a live trap via gdb and opens the **CPU Journey**
  seeded with the frozen frame.
- Play → decode-the-trap game.

## What it is doing

The face teaches the course's punchline: a system call, a timer preemption, and
a page fault are **one hardware mechanism with different causes**. The taxonomy
is computed from `scause` alone; the feed shows individual traps with the CSRs
*as they were at trap time*.

Classification (`gini_kind`, mirrors `usertrap`'s own dispatch):

| scause | kind |
|--------|------|
| bit 63 set, low byte 9 | device (PLIC external) |
| bit 63 set, else | timer (timer/software int) |
| 8 | syscall (ecall from U-mode) |
| 12, 13, 15 | pagefault (instruction / load / store) |
| 2 | illegal |
| anything else | other |

## How it is bolted into xv6

- **`gini_traprec()`** (§2a2) does both jobs: bumps `gini_trapcount[kind]`
  (histogram) and appends to a 256-entry ring (feed) — per event: pid, kind,
  scause, epc, stval, plus **sstatus/sie/sip captured at trap time** (a
  poll-time CSR read could only ever describe the console interrupt the dump
  itself caused).
- Hooked in **both** trap paths: `usertrap` (same early anchor as the fault
  ring, so fatal exceptions that `exit()` are still recorded) and `kerneltrap`
  (§2a3) — UART rx and virtio disk completion arrive in kerneltrap, so without
  the kernel-side hook **the device bucket stays empty**.
- Deliberately never re-calls `devintr()` — classifying from scause alone means
  no interrupt is consumed by observation.
- **Trap-catch** is the gdb path: `tbreak usertrap` conditioned on scause
  (syscall→8, pagefault→12|13|15, illegal→2, timer/device→interrupt codes), then
  prints scause/sepc/stval + pid + trapframe registers, and always detaches.
- The kernel board's entry-class counting (system call / exception / device
  interrupt) is a separate, simpler classifier in `gini_doorrec()` — see
  [os-kernel-board](os-08-kernel-board.md); the taxonomy here is the six-bucket
  refinement of the same scause decode.

## Wire format

`TC <kind> <name> <count>` (cumulative, all six kinds) ·
`TR <pid> <kind> <cause> <epc> <tval> <sstatus> <sie> <sip> <seq>` (ring of
256, CSR triple optional on older images). `TrapEvent.came_from` decodes
sstatus.SPP into user/kernel. See [os-wire-protocol](os-01-wire-protocol.md).

## Limits and honesty

- The observer appears in the data: every GINI poll raises a UART interrupt →
  device-kind traps that are the measurement, not the workload. (The board
  separates observer *edges*; the trap taxonomy does not have an observer
  split.)
- scause 14 does not exist (reserved) — the 12/13/15 gap is a deliberate
  teaching point in the decode game.
- On an idle machine the feed is nearly all timer — that *is* the lesson about
  what idle consists of.

## Cross-references

[os-kernel-board](os-08-kernel-board.md) · [os-cpu](os-10-cpu-journey.md) ·
[os-memory](os-04-memory.md) · [os-games](os-12-games.md)
