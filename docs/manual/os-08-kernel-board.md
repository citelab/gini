---
id: os-kernel-board
title: Kernel Board / OS HUD — residency, edges, the three doors
subsystem: kernel-board
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/trap.c, kernel/riscv.h, kernel/start.c, kernel/console.c]
endpoints: [/board, /sc, /faults, /traps, /source]
keywords: [kernel board, OS HUD, three doors, asked, couldn't, seized, residency, edges, call matrix, instructions per kernel entry, instret, direct path, observer effect, path trace, Ctrl-Q, X-ray, swimlanes, timeline scrub]
---

# Kernel Board / OS HUD — residency, edges, the three doors

## What is on the screen

`os_hud.py` (the densest face; toggled from the main window, not the Machine
Lab). Two readings of the same ~10 s window:

- **BOARD** — a hand-authored, permanent layout of 12 kernel blocks
  (syscall / file·memory·proc / pipe·inode·log / bcache spanning as "a shared
  floor" / disk·console·plic outlined as drivers). No auto-layout, ever:
  "spatial memory is how a structure is learned, and a block that moves between
  frames cannot be learned at all." Above: a "your program" strip with instr/s
  and the **three doors by agency** (asked / couldn't / seized). Right: the
  permanent wide **direct lane** ("no kernel runs · the MMU checks every address
  in hardware") and a **blue dashed** kernel→lane line meaning *configuration,
  not a call*. Below: the machine cut in half (kernel-only devices vs
  direct-reachable CPU/MMU/RAM).
  - **Encoding rule** (the panel's whole argument): arrow width = CALLS, block
    shade = CPU TIME. They disagree — bcache "601 · 2%" sits above disk
    "12 · 12%".
  - Grey dashed edges = kernel work provoked by **GINI's own polling**, drawn
    first so workload edges sit on top; block notes read `601+12 2%` where
    `+12` is ours.
  - **CPU marker** = a trail of 12 real timer-tick samples, newest solid —
    "animating a position from the residency distribution would look livelier
    and be a fabrication."
  - **Ctrl-Q path trace**: when armed, the one real ordered path lights up in
    orange with numbered stops; legend `TRACE syscall → file → inode → bcache →
    disk · deepest: disk`.
- **X-RAY swimlanes** — five lanes (syscall, proc, memory, fs, trap) of
  individual events in kernel order, optionally focused on one pid; rails drawn
  even when empty ("an empty window and a missing feature look identical if the
  lanes disappear").
- **Interactions**: click a block/door → focus the lanes it can produce (others
  stay drawn but empty, "so you can see WHAT WAS EXCLUDED"); click the X-RAY
  header → collapse to board-only; **double-click a block → open its kernel
  source** (via `/source` from the patched tree); hover → door help or
  "N calls, M of them provoked by GINI reading the machine".
- **Timeline scrub** — 120 s of retained frames; drag to replay, release near
  the end to go live.

## What it is doing

The board exists to make one fact measurable rather than asserted: **the kernel
is almost never running**. The headline `instructions-per-kernel-entry` is user
instructions retired between kernel entries — enormous for CPU-bound work,
collapsing to a handful while typing.

### The three doors — exact criteria

`gini_doorrec()` runs at the very top of `usertrap()` on **every user→kernel
crossing** and decodes raw `scause`, in this priority order:

```c
if (c & 0x8000000000000000L) door[2]++;   // SEIZED — any interrupt (bit 63)
else if (c == 8)             door[0]++;   // ASKED  — ecall from U-mode
else                         door[1]++;   // COULDN'T — every other exception
                                          //   (page faults 12/13/15, illegal 2, …)
```

By **agency**: asked = your idea; couldn't = nobody's, but your doing; seized =
nothing to do with you. Traps landing while already in the kernel go to
`kerneltrap`, which only samples residency — **doors count entries, not trap
events**, which is what keeps instr-per-entry honest.

### The other measurements

- **Residency** (`BSUB`): sampled only on the supervisor **timer** tick
  (bit 63 ∧ code 5) — deliberately not on device interrupts, which arrive
  correlated with device activity and would over-report disk/console. "A slow
  honest estimate beats a fast biased one." Two halves: `gini_doorrec` samples
  from usertrap *before* anything touches `gini_sub` (so the tick is charged to
  USER — the direct path's time), `gini_ktick` samples from kerneltrap.
- **Edges** (`BEDGE`): exact call counts crossing subsystem boundaries, counted
  only when the subsystem actually changes (bio→bio is not block traffic).
  Observer-provoked calls go to `BEOBS` instead, keyed off the dump bracket
  flag.
- **User instructions** (`BUSER`): `r_instret() − gini_umark[hart]` banked on
  each entry; the mark is set in `prepare_return` on the way out. First sample
  (mark 0) skipped. Emitted as two numbers, never a ratio — ratios cannot be
  differenced.
- **Path trace** (`BPATH`): real ordered subsystem transitions, recorded only
  while armed and only when the observer flag is down — a trace of GINI reading
  the machine is excluded. The trace stops at `swtch` (assembly — no C function
  for the tracer to sit in), which is itself the teaching moment.

## How it is bolted into xv6

§4h (~1717–2219). Probes are one `GINI_SUB(GSUB_x)` line at the top of each
subsystem **entry point**, using `__attribute__((cleanup))` so every exit path
restores the previous subsystem — no exit bookkeeping to forget. Deliberately
NOT instrumented: spinlocks/sleeplocks (called from everywhere; contention is
measured separately and belongs on block borders), string/printk, and anything
named `gini_*` (the instrument never measures itself). Subsystem ids 0–13:
user trap syscall proc memory file pipe inode log bcache disk console plic
other. Requires `r_instret()` (§4h1) and `mcounteren |= 6` (§4h2).

Frontend: `kernel_board.py` — `Sample` (one raw dump, all cumulative), `Window`
(differences two samples; a decrease = counter restart, adopted as-is;
`MAX_MISSES = 4` before declaring no board support), `Frame.instr_per_entry`,
`resid_trustworthy` (≥ 8 samples). Controller does four blocking reads per
900 ms poll (`/board /sc /faults /traps`) merged by `os_events.merge`.

## Wire format

`BOARDN BSUB BEDGE BEOBS BDOOR BSAMP BTRAIL BARM BPATH BUSER` — full field
reference in [os-wire-protocol](os-01-wire-protocol.md).

## Limits and honesty

- ~2 residency samples/s (0.5 s tick): a 1 s window can only read 0/50/100 %,
  so below 8 samples the board drops shading and says
  "sampling — only N residency samples this window".
- The doors have **no observer split** (`gini_door` has no `_obs` twin): a GINI
  poll's UART interrupt landing in user mode counts as one *seized*. On idle
  machines most polls land in kerneltrap and don't door-count; busy machines
  carry a small observer inflation in seized. Related stated limit: ~3 events
  per poll are charged as real because the delivering interrupt precedes the
  bracket flag.
- Ctrl-Q has no HTTP route — arming is typed in the console; the HUD only
  reports `BARM`/`BPATH`.
- `gini_boardreset()` is unbound — board counters clear only on reboot.
- "No kernel runs on the direct path" is true as *no kernel instruction
  executes*; on a TLB miss the hardware walker reads tables the kernel wrote.
  RISC-V walks in hardware, MIPS traps to software — the boundary is a design
  decision, not a law.

## Cross-references

[os-traps](os-06-traps.md) · [os-wire-protocol](os-01-wire-protocol.md) ·
[os-known-issues](os-15-known-issues.md) · [os-programs](os-14-programs.md)
