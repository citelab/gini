---
id: os-memory
title: Memory face — page tables, fault ring, allocator, COW
subsystem: memory
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/vm.c, kernel/vm.h, kernel/trap.c, kernel/kalloc.c, kernel/proc.c, kernel/riscv.h, kernel/start.c]
endpoints: [/vm, /vmall, /faults]
keywords: [virtual memory, page table, Sv39, PTE, page fault, lazy allocation, copy-on-write, COW, stack growth, guard page, kalloc, fragmentation, A/D bits, RSW, vmprint, SBRK_EXEC]
---

# Memory face — page tables, fault ring, allocator, COW

## What is on the screen

`memory_lab.py`:

- **Region strip** — the address-space map (text, data, heap, guard, stack,
  trapframe, trampoline), width-weighted with a 7 % floor so the one-page
  trapframe stays visible. *Demo-only today: real kernels don't dump regions.*
- **Page-table table** — `VA | PA | perms | A/D | region`. The A/D column shows
  the hardware accessed/dirty bits (amber when A set) — every page-replacement
  policy reads them.
- **Allocator bars** — used/free physical pages, plus a fragmentation bar
  drawing free memory *and the largest contiguous free run* as an inner block:
  "N% of free memory is NOT in that run · healthy|fragmented".
- **Page-fault panel** — the live classified ring: `pid | VA | cause | kind`
  colored by kind (cow-write / lazy-alloc / stack-growth / illegal). Button is
  **Refresh** live, **Simulate page fault** only in demo; live tooltip says to
  launch `alloc`.
- **COW / sharing panel** — per-process resident-vs-virtual meters (lazy
  allocation reads as a gap that fills in), a `phys page | shared by | cow`
  table, and Simulate-COW-write (demo only).
- Two Play buttons → the thrash-diagnose and translate-the-address games.

## What it is doing

The face renders a process's memory as the fiction the kernel maintains: leaf
mappings with permissions, the physical pages behind them, sharing derived by
matching PAs across processes, and the faults by which the fiction is extended.
The core lesson it serves: *a fault is not an error* — lazy allocation, COW, and
stack growth arrive as the same hardware event as a segfault, distinguished only
by what the kernel decides to do.

### Fault classification (Python-side, `xv6_vm.py:287–311`)

Applied in order, per fault:

1. **cow-write** — a leaf PTE exists for the page, is not writable, and
   `scause == 15` (store).
2. **lazy-alloc** — no leaf, pid known, and `page_va < p->sz` (inside the brk'd
   heap).
3. **stack-growth** / lazy-alloc — no leaf and a region map was supplied:
   "stack" region → stack-growth, else lazy-alloc. **Dead on live kernels** —
   real builds never supply regions, so a live stack fault classifies as
   `illegal` (see [os-known-issues](os-15-known-issues.md)).
4. **illegal** — no leaf, nothing matched.
5. Leaf exists but not the COW shape → the raw scause name.

## How it is bolted into xv6

- **Fault ring** (§2a): `gini_fault_note()` hooked into `usertrap` right after
  `p->trapframe->epc = r_sepc()` — before any student lazy/COW handler runs, so
  the fault is recorded whether or not it is then repaired. Records only scause
  12/13/15. Ring `GINI_RING = 256`; index deliberately `uint64` (an int would
  wrap negative → OOB kernel write).
- **vmprint** (§3): recursion when `(pte & (R|W|X)) == 0`; the 6.1810 lab
  function, reused.
- **gini_vmdump_all** (§4d1): walks every proc's table emitting `VL` leaves with
  the low 10 PTE bits — including the student's RSW COW bit — so parent and
  child sit side by side and **sharing is derived from identical PAs, with no
  kernel refcount**. `Pte.cow` = user ∧ ¬writable ∧ RSW≠0 (agnostic about which
  RSW bit the student picked).
- **vmfault shadow (index 3)** (§3b): hooked *inside* `vmfault()` rather than at
  the usertrap call site so copyin/copyout share the seam. Validator: PA
  page-aligned, in `[KERNBASE, PHYSTOP)`, and the faulting VA genuinely mapped
  afterwards — "a half-finished handler can't leave the process running on a
  lie". Counters → `VMF handled/fellthrough`.
- **kalloc shadow (index 6, "S3")** (§3e): ships with an authoritative
  **allocation bitmap** (1 bit per physical page, ~4 KB, maintained inside the
  kmem lock) because a wrong page here silently corrupts unrelated kernel
  memory. Validator: page-aligned, in range, ≥ `end[]`, not already set. Dump:
  `KA free/total/maxrun/shadow`; `maxrun` is the fragmentation score a
  buddy-allocator mission grades on.
- **SBRK_EXEC** (§4i): heap pages come back W-but-not-X, so jumping into sbrk
  memory dies on scause 12. `growproc_exec()` + `sbrkexec()` allocate
  `PTE_W|PTE_X` heap so the `walker` program's PC can walk a NOP corridor.
  Deliberately violates W^X; meant to be said out loud (xv6 has no mprotect).
- **instret enablement** (§4h1/4h2): `r_instret()` plus `mcounteren |= 6` —
  without bit 2 the csrr itself traps as an illegal instruction.

## Wire format

`/vm`: `VMF`, `KA`, `page table` + `..N: pte … pa …` (depth = ".." count, leaf
at depth 3). `/vmall`: `VP pid name sz`, `VL pid va pa flags`. `/faults`:
`FLT pid scause va epc seq` (oldest first, last 256). See
[os-wire-protocol](os-01-wire-protocol.md).

## Limits and honesty

- Superpages: `vmprint` treats R/W/X at inner levels as non-recursion; the
  leafwalk in vmdump_all emits them as leaves.
- `parse_faults` drops the `seq` field; the event-merge path (`os_events`) keeps
  it and drops *unstamped* events instead.
- The sharing table restricts to user+valid leaves so trampoline/trapframe
  (mapped everywhere) don't swamp the signal.
- Region map and the two Simulate buttons are demo-only; live mode observes,
  demo mode provokes.

## Cross-references

[os-storage](os-05-storage.md) · [os-traps](os-06-traps.md) ·
[os-shadows](os-13-shadows.md) · [os-games](os-12-games.md) ·
[os-known-issues](os-15-known-issues.md)
