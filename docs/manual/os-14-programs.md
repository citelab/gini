---
id: os-programs
title: Launchable programs — the workload zoo
subsystem: programs
layer: [kernel-patch, agent, ui]
kernel_files: [user/]
endpoints: [/run, /programs, /kill]
keywords: [spin, busy, walker, toucher, alloc, writer, sgrind, mgrind, grind, forktest, workload, launcher, user program]
---

# Launchable programs — the workload zoo

## What is on the screen

The Scheduler face's **Launcher**: a program combo, an argument box (disabled
for programs that take none, placeholder showing the arg spec), a long inline
hint per program, and a red refusal label — the agent's "this xv6 image does not
know '<prog>' — it was built before the program was added. Rebuild the image."
surfaces here instead of vanishing.

## What it is doing

The programs are ordered as *a tour of the machine*: CPU · CPU-with-a-moving-PC
· memory-by-pattern · memory-lazily · storage · storage-under-pressure ·
allocator-under-pressure · everything-at-once · the process table. Each isolates
a subsystem so what the faces show is attributable.

| Program | Args (default) | What it exercises |
|---------|----------------|-------------------|
| `spin` | seconds (forever) | Pure CPU loop; the PC **parks on one instruction**. |
| `busy` | seconds | CPU-bound but varied code, so the sampled user PC actually moves. |
| `walker` | pages ticks laps (64/1/∞) | Builds a corridor of NOPs on an **executable heap** and jumps in — the PC itself walks low→high, one page per tick. Needs SBRK_EXEC. |
| `toucher` | pages [seq\|rand\|stride] (48) | Touches N pages **twice**: pass 1 faults them in, pass 2 faults zero times. Data pointer moves, PC stays put. |
| `alloc` | pages (48) | Grows the heap lazily, then touches each page so it demand-faults — new mappings appear live in the Memory face. |
| `writer` | — | create/write/remove loop → a stream of write-ahead-log transactions. |
| `sgrind` | blocks (20; 30 fits) | Reads K distinct files round-robin. NBUF = 30, so 20 stays hot and 60 evicts what the next pass wants — **the cliff is the lesson**. Thrashing lives in the buffer cache, since a user program can't thrash 128 MB of RAM. |
| `mgrind` | pages (16) | Hammers the physical page allocator, the page-table walk, and fork's address-space copy — isolates one subsystem so lock attribution is unambiguous. |
| `grind` | — | Stock xv6. The only workload that spends real time in KERNEL mode (random syscall mix). What makes the trap taxonomy, syscall histogram, and CPU Journey kernel band show anything interesting. |
| `forktest` | — | Fills the process table, then exits. |

`init` and `sh` are never launchable or killable (kill guarded pid > 2 on both
sides).

## How it is bolted into xv6

§5 of `gini_patch.py` writes the user programs (~lines 2301–2770) and registers
them in the Makefile. `walker` required the SBRK_EXEC / `growproc_exec()` patch
(§4i) because sbrk pages are W-without-X. Programs count time as
`uptime() + seconds*2` because of the 0.5 s tick.

Two lists must stay in step — `PROGRAMS` in `gini_agent.py` (the `/run`
allow-list) and `_LAUNCHABLE` in `machine_lab.py` (the menu); both carry a
comment saying a name in one but not the other is either a dead menu entry or a
refused launch, and `GET /programs` exists to make the mismatch visible.

## Wire format

`POST /run?prog=&args=` writes `"<prog> <args> &\n"` to the real shell —
arguments sanitized to lowercase alphanumerics + spaces, 32 chars, allow-listed
names only (command-injection defence).

## Limits and honesty

- Programs print to the same serial the agent parses; a chatty program (grind
  prints a progress byte every 500 iterations) can corrupt one poll's dump.
  Documented: "the fix is a dedicated dump channel, not dropping the program."
- Launching is live-mode only.

## Cross-references

[os-scheduler](os-02-scheduler.md) · [os-memory](os-04-memory.md) ·
[os-storage](os-05-storage.md) · [os-architecture](os-00-architecture.md)
