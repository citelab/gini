---
id: os-glossary
title: Glossary — GINI terms mapped to textbook terms
subsystem: platform
layer: [domain, ui]
kernel_files: []
endpoints: []
keywords: [glossary, terminology, doors, shadow, face, wedge, residency, edges, direct path, wait channel, definitions, textbook mapping, vocabulary]
---

# Glossary — GINI terms mapped to textbook terms

GINI introduces vocabulary you will not find in Silberschatz–Galvin–Gagne (SG),
OSTEP, or the xv6 book. This page defines every non-standard term, gives the
**standard term** a textbook index would list, and links the manual page where
the mechanism is documented. Entries marked **[GINI]** are our coinage — do not
search a textbook for them. Entries marked **[xv6]** are xv6-source vocabulary
that general textbooks may not use. Entries marked **[RISC-V]** are architecture
terms the SG textbook does not cover (the xv6 book does).

## The doors

**doors** [GINI] — the three ways control enters the kernel from user mode,
classified by *agency* (whose idea the entry was). Textbook: the classes of
**trap** — system calls, exceptions/faults, and interrupts (SG 1.2, 2.3; xv6
book ch. 4: "three kinds of event which cause the CPU to set aside ordinary
execution"). Counted per user→kernel crossing in `usertrap`; see
[os-kernel-board](os-08-kernel-board.md).

- **asked** [GINI] — the deliberate door: the program executed `ecall`.
  Textbook: **system call** (via the ecall *exception*, scause 8).
- **couldn't** [GINI] — the accidental door: the program could not proceed.
  Textbook: **exception** or **fault** — page fault (scause 12/13/15), illegal
  instruction (2), and every other non-ecall exception.
- **seized** [GINI] — the uninvited door: something else wanted attention.
  Textbook: **interrupt** (scause bit 63 set) — timer or device/external.

**trap** [standard, but overloaded] — GINI and the xv6 book use *trap* as the
umbrella for all three doors. Beware: some texts (and some architectures' docs)
use "trap" narrowly for intentional exceptions only. In GINI it always means
"any transfer into the kernel via the trap vector."

**a fault is not an error** [GINI slogan] — lazy allocation, copy-on-write, and
stack growth arrive as the *same hardware event* as a segfault; the kernel's
decision, not the event, determines the outcome. Textbook: **demand paging /
minor vs major fault handling** (SG 10.2; OSTEP ch. 21).

**trap taxonomy / six buckets** [GINI] — the finer classification the Traps
face uses: syscall · pagefault · timer · device · illegal · other. The doors
are the same decode collapsed to three by agency. See [os-traps](os-06-traps.md).

## The shadows

**shadow** [GINI] — a student-written decision function given **first refusal**
on one kernel decision, with the shipped implementation as fallback. Textbook:
no direct equivalent; closest are **pluggable policy** / policy-mechanism
separation (SG 2.6). The seven: rr_sched, prio_sched, lottery_sched, vmfault,
bget_evict, balloc, kalloc. See [os-13-shadows](os-13-shadows.md).

- **first refusal** [GINI] — the dispatch order: shadow asked first; returning
  0 ("not implemented") or an invalid answer falls back to the primary.
- **primary** [GINI] — the shipped xv6 implementation behind every shadow.
- **reject** [GINI] — a shadow answer refused by the validator (e.g. a
  non-RUNNABLE proc, an in-use buffer). The correctness signal: a shadow may be
  a poor policy but must never hand the kernel an illegal answer.
- **validator** [GINI] — the kernel-side legality check on a shadow's answer.
- **wedge** [GINI] — the machine stops responding with a shadow enabled.
  *Hard wedge*: dumps stop (panic or interrupts-off loop). *Soft wedge*: dumps
  answer but nothing runs (a picker that never picks). Textbook: **hang /
  livelock / crash** — "wedge" is used because blame, detection, and recovery
  (reboot clears all shadows) are part of the mechanism.
- **manifest** [GINI] — the per-shadow status record (present / enabled /
  active / faults / rejects / calls / hash).

## The instruments

**face** [GINI] — one view of the Machine Lab (Scheduler, Memory, Storage,
Traps, Syscalls, plus the board, locks, fingerprints…). Just "a screen"; the
word exists so "the Memory face" is unambiguous.

**Machine Lab** [GINI] — the whole xv6 instrument panel.

**Kernel Board / OS HUD / the board** [GINI] — the subsystem map showing
residency, call edges, doors, and the direct lane. See
[os-kernel-board](os-08-kernel-board.md).

**residency** [GINI] — timer-sampled CPU time per kernel subsystem — *where the
CPU was when the tick landed*. Textbook: **sampling profiler** output; "CPU
time attribution". Block shade on the board.

**edges / call matrix** [GINI] — exact counts of calls crossing from one
subsystem into another. Arrow width on the board. Textbook: a **call graph**
with edge counts.

**instructions-per-kernel-entry** [GINI] — retired user-mode instructions
divided by kernel entries (sum of the doors); the board's headline number.
Textbook: no named equivalent; it quantifies **kernel overhead / mode-switch
frequency**.

**direct path / direct lane** [GINI] — execution with no kernel instruction
running: user code whose loads and stores reach hardware through the MMU.
Textbook: ordinary **user-mode execution**; the point of drawing it as a lane
is that it is where a program spends essentially its whole life. Caveat taught
with it: on a TLB miss the hardware walker reads tables the kernel wrote.

**dashed blue line ("configuration, not a call")** [GINI] — the kernel set up
page tables and the trap vector, then stepped aside. Textbook: **mechanism
set-up**; the kernel *arranged* to be bypassed.

**observer traffic / "that's us" / edges_obs** [GINI] — kernel work provoked by
GINI's own polling, counted separately and drawn grey/dashed. Textbook:
**observer effect / probe effect** in measurement.

**trail** [GINI] — the ring of real sampled CPU positions the board's marker
draws from (never interpolated). **path trace** [GINI] — the Ctrl-Q-armed
ordered record of subsystem transitions for one operation. Textbook: an
**execution trace**.

**X-RAY / swimlanes** [GINI] — the per-event lanes (syscall, proc, memory, fs,
trap) under the board. **event clock / seq** [GINI] — the global monotonic
stamp (`gini_stamp()`) that orders events across rings, because 0.5 s ticks
cannot.

**wire format / dump** [GINI] — the plain-text records the kernel prints on
serial (FLT, TRACE, BDOOR…), bracketed by 0x1e/0x1f. See
[os-wire-protocol](os-01-wire-protocol.md).

**live / real mode vs demo mode** [GINI] — data read from the running kernel
vs a deterministic offline stand-in. Never auto-switched; provenance is stamped
in the data (`source="real"|"demo"`).

## Processes and sleeping

**wait channel / chan** [xv6] — the address a blocked process is sleeping on;
`wakeup(chan)` matches it by equality. Textbook: closest is a **condition
variable's wait queue** (SG 6.7; OSTEP ch. 30) — but xv6 has no queue, only an
address compared for equality. GINI's addition is only the *naming* ("a disk
block", "console input"); the mechanism is stock xv6.

**sleep_prepare / the runnable-with-a-channel row** [GINI] — the visible
two-step of going to sleep: interest registered before the final state change,
so a wakeup landing in the gap is not lost. Textbook: the **lost wakeup
problem** (OSTEP ch. 30; xv6 book ch. 7). The row is a photograph of the race
being handled.

**nothing wakes itself** [GINI slogan] — every wakeup comes from an interrupt
or another process; hence a stopped machine is a cycle of waits. Textbook:
**deadlock as a wait-for cycle** (SG 8.2).

**process fingerprint** [GINI] — the 5-axis behavioral shape of a process
(cpu, syscalls, io_wait, faults, forks). Textbook: **workload
characterization**; "CPU-bound vs I/O-bound" (SG 3.2, 5.1).

**starving / monopolising flags** [GINI] — the ⚠ annotations on the process
tree. Textbook: **starvation** (SG 5.3).

## Scheduling and time

**quantum / time slice** [standard] — but note GINI's tick is **0.5 s** (10×
stock xv6), so quantum N = N/2 seconds; chosen to make the Gantt watchable.

**Gantt strip** [standard-ish] — per-CPU who-held-the-CPU timeline. SG draws
Gantt charts for scheduling examples (SG 5.2); GINI's is live.

**aging / wait_ticks** [standard] — SG 5.3.3; GINI exposes the counter per
process, and the shipped priority policy's weak aging is a deliberate bug.

**tickets** [standard] — lottery scheduling (OSTEP ch. 9); the shipped lottery
deliberately ignores them (fixing it is the lab).

## Storage

**WAL / write-ahead log / "intentions first"** [standard] — SG 14.7 "log-based
recovery"; the xv6 book calls it simply "the log" (ch. 8). Phases shown:
building → committing → idle ("installed" is narrated, not a kernel state).

**cache grid / recency heat / lastuse** [GINI] — the buffer-cache display;
`lastuse` is a GINI-added field. Textbook: **LRU replacement** state (SG 10.4).

**mean gap** [GINI] — mean distance between successive block allocations; the
locality/fragmentation score. Textbook: **external fragmentation / allocation
locality** (SG 9.2, 11.5).

**max free run / maxrun** [GINI] — largest contiguous run of free physical
pages. Textbook: **external fragmentation** measure for memory (SG 9.2).

**the cliff** [GINI, informal] — the hit-rate collapse when a working set
exceeds the 30-buffer cache (`sgrind 60`). Textbook: **thrashing**, relocated
to the buffer cache (SG 10.6).

## Games and pedagogy

**deck** [GINI] — a fixed set of (default 10) cases in a graded game run.
**practice / graded** [GINI] — endless-with-hints vs fixed-deck-silent modes.
**hint** [GINI] — a rule-based classifier's guess, shown in practice only ("a
baseline to beat, not an oracle"). **confusion matrix** [standard — but from
machine learning, not OS texts] — true class × guessed class; the diagonal is
correct; one hot off-diagonal cell = one fixable misconception.

**signature** [GINI] — the evidence a game shows (a radar shape, a Gantt
snippet, one raw scause value).

**GINI Says / activity slide** [GINI, course] — the in-lecture exercise
markers used by the Fall 2026 deck kit.

## Architecture terms (standard, but not in SG)

These are **[RISC-V]** / **[xv6]** vocabulary; the SG textbook is
architecture-neutral, so students meet them only in the xv6 book and here:

**hart** — a RISC-V hardware thread; what other texts call a core/CPU.
**scause / sepc / stval** — supervisor CSRs: why the trap happened, where, and
the offending address. **sstatus.SPP / SPIE** — came-from privilege and
were-interrupts-on bits. **sie / sip** — interrupt enable/pending by source.
**satp** — page-table base + mode register (Sv39 here). **Sv39** — RISC-V
39-bit, 3-level paging. **PTE flags V R W X U G A D + RSW** — RSW are the two
software-reserved bits (a student COW implementation typically claims one).
**ecall / sret** — enter/leave supervisor mode. **PLIC** — the
platform-level interrupt controller (external/device interrupts). **instret**
— the retired-instruction counter behind instructions-per-kernel-entry.
**trapframe vs context** [xv6] — all user registers saved on a trap vs the 14
callee-saved registers saved on a `swtch`; the difference (who agreed to what)
is a course centerpiece. **trampoline / trapframe pages** [xv6] — the fixed
top-of-address-space mappings every process shares.

## Cross-references

Every manual page's frontmatter carries `keywords` including its coined terms,
so a glossary lookup and a page lookup converge. Course-side vocabulary
(gating vs breadth lectures, C/T/A/P lab series) lives in the Fall 2026 course
plan, not this manual.
