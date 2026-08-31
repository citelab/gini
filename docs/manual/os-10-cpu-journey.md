---
id: os-cpu
title: CPU & Registers face + CPU Journey
subsystem: cpu
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/trap.c]
endpoints: [/procs, /traps, /trapcatch, /snapshot]
keywords: [CPU, registers, CSR, sstatus, sie, sip, satp, Sv39, mode bar, user kernel idle, privilege, trapframe, context, swtch, journey, step, frozen trap, SPP, SPIE]
---

# CPU & Registers face + CPU Journey

## What is on the screen

**CPU & Registers** (`cpu_lab.py`) — the HARDWARE face, explicitly not a subset
of the scheduler ("which process runs" vs "the registers the CPU runs *with*"):

1. **Mode-time bar** — user / kernel / idle over the last ~1 s, from MODETIME
   deltas.
2. **CSR / interrupt strip** — the three S-mode interrupt sources from
   `sie`/`sip` (filled = enabled, dot = pending), `sstatus.SPP` ("came from
   user|kernel"), `SPIE` ("interrupts were on|off"). The note is candid: "this
   row describes the console interrupt that GINI's own poll caused."
3. **Trap history** — CSR state recorded *at trap time* from `/traps`, newest
   first, uncontaminated by polling. Empty state: run `grind` or `alloc`.
4. **Register tiles** per hart, grouped by role, with **satp decoded** to
   `Sv39 · root 0x…`.

**CPU Journey** (`cpu_journey.py`) — three walkthrough modes (syscall / context
switch / preemption): stage chips, Prev/Step, a privilege-band line ("CPU is
in: USER mode · process A · privilege change"), and two **save-area cards** —
trapframe (all user registers, written on every TRAP) vs context (14
callee-saved registers, written on every `swtch`) — the active one highlighted
"▸ trapframe (writing)". With a frozen trap from the Trap Lab's "Step a trap"
it splices **real values** into the captions (saved ra/sp/a0/a7 at `uservec`;
scause → kind, sepc, stval at `usertrap`; a7/a0 at `syscall()`); without one, a
banner says "couldn't freeze a trap (kernel idle) — showing the reference
walkthrough."

## What it is doing

Two lessons. First, privilege is a bit you can watch flip: the mode bar answers
"how busy is this machine, and *where*" — `spin` is almost all user, `grind` is
not. Second, the journey's closing question: **why does a trap save everything
while a context switch saves fourteen registers?** Because of who agreed to
what — a trap interrupts a program that agreed to nothing; a switch happens at
a call the compiler already knew about. That difference is the cost model of
both.

## How it is bolted into xv6

- **MODETIME** counters: `gini_ut++` in usertrap's timer hook; kerneltrap's hook
  splits `gini_it` (no proc → idle) vs `gini_kt`. No CSR reads — the trap entry
  path already knows where the CPU was.
- **CSR line** in `gini_dump` reads the dumping hart's CSRs inside the trap
  handler — which is why `SIE` (the global enable) reads 0 there, documented in
  the face; the UI leans on `sie` (per-source enables) for honest state.
- **REGS** lines are live trapframe registers per hart (`s0` added for the
  backtrace lab).
- **Trap-catch** (gdb, `/trapcatch?kind=`) freezes a real trap:
  conditioned `tbreak usertrap`, prints scause/sepc/stval + trapframe fields
  from `cpus[$tp].proc->trapframe`, always detaches.
- `/snapshot` (gdb) supplies registers + backtrace + a proc[] walk for the
  scheduler face's kernel-stack panel after a Step.

## Wire format

`MODETIME user u kernel k idle i` (cumulative ticks) · `CSR sstatus sie sip
stvec scause sepc` · `REGS cpu pid pc sp ra s0 a0 a7 satp sz` — see
[os-wire-protocol](os-01-wire-protocol.md). Trap history reuses TR records.

## Limits and honesty

- Poll-time CSRs describe the poll; trap-time CSRs (TR ring) describe the
  workload. The face uses both and labels which is which.
- The mode bar's resolution is the 0.5 s tick — 2 samples/s.
- Trap-catch halts the kernel briefly and can time out harmlessly when idle.

## Cross-references

[os-traps](os-06-traps.md) · [os-scheduler](os-02-scheduler.md) ·
[os-kernel-board](os-08-kernel-board.md)
