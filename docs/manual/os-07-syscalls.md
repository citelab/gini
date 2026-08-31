---
id: os-syscalls
title: Syscalls face — histogram and the strace ring
subsystem: syscalls
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/syscall.c]
endpoints: [/sc]
keywords: [system call, syscall, histogram, strace, trace, a0, a7, return value, ecall, syscall numbers, custom syscall, syscall builder]
---

# Syscalls face — histogram and the strace ring

## What is on the screen

`syscall_lab.py`: a histogram of the top 12 syscalls by count **in the last
60 s** (`SyscallRate` differencing cumulative counters), and an strace-style
trace — `pid name(a0) = ret` — from the TRACE ring. 1.5 s poll.

## What it is doing

This is the entire contract between programs and the kernel, on one screen —
about twenty verbs, and everything a computer does is built from them. Typing in
the shell makes `read`/`write` climb; running `grind` changes the histogram's
shape completely (the C01 activity in the course plan).

## How it is bolted into xv6

§4g wraps the dispatch in `syscall()`:

```c
uint64 gsc_a0 = p->trapframe->a0;          // first argument, captured BEFORE dispatch
p->trapframe->a0 = syscalls[num]();        // stock dispatch
gini_sccount[num]++;                       // histogram
gini_ring[gsi] = { pid, num, gsc_a0, p->trapframe->a0, gini_stamp() };
```

`a0` is both the first-argument register and the return register on RISC-V —
hence the capture before dispatch. 64 counter slots; ring of 256 with the global
event-clock stamp so syscall events merge-sort with traps and faults into one
ordered story (`os_events.py` routes each call into the proc / fs / syscall
X-ray lanes).

**Numbering**: stock xv6 syscalls are 1–22. Custom syscalls created by the
Syscall Builder start at **23**; counters are keyed by number, and names resolve
through `SYSCALL_NAMES` + an `extra` map, so a Builder-added syscall appears in
the histogram automatically.

## Wire format

`SC <num> <cumulative count>` (zero rows suppressed) ·
`TRACE <pid> <num> <a0> <ret> <seq>` (last 256, oldest first). See
[os-wire-protocol](os-01-wire-protocol.md).

## Limits and honesty

- Only the first argument is traced — one register, captured cheaply; full
  argument decoding is out of scope for a ring this hot.
- Counts are cumulative; the face windows them to 60 s. The very first snapshot
  is treated as base = {} so the histogram shows counts-so-far instead of a
  blank first frame.
- A library call (`printf`) never appears — only the `write` it eventually
  issues does. That distinction is course material (C01's exit question 3).

## Cross-references

[os-traps](os-06-traps.md) · [os-kernel-board](os-08-kernel-board.md) ·
[os-wire-protocol](os-01-wire-protocol.md)
