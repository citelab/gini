---
id: os-storage
title: Storage face — disk layout, buffer cache, block allocator, WAL
subsystem: storage
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/bio.c, kernel/buf.h, kernel/fs.c, kernel/log.c]
endpoints: [/fs]
keywords: [file system, buffer cache, bget, eviction, LRU, lastuse, hit rate, block allocator, balloc, fragmentation, mean gap, bitmap, write-ahead log, WAL, commit, install, superblock, inode]
---

# Storage face — disk layout, buffer cache, block allocator, WAL

## What is on the screen

`storage_lab.py`, fed entirely by `/fs`:

- **Disk strip** — boot | super | log | inodes | bitmap | data with block ranges,
  derived from the superblock.
- **Block map** — the data region as a free/used map from the on-disk bitmap;
  cells aggregate several blocks and shade proportionally; the newest allocation
  is ringed in amber. Caption: allocations + mean gap between consecutive ones
  ("lower = better locality = fewer seeks"). *Fragmentation is a shape, not a
  number.*
- **Inodes table** and **directory tree**.
- **Cache grid** — one cell per buffer (NBUF = 30): fill = recency heat, amber
  ring = in use (refcnt > 0, never a legal victim), **red flash for 1200 ms on a
  recycle**. "A table of numbers cannot show a replacement POLICY; motion can."
  Title carries hits / misses / hit-rate / evictions.
- **Write-ahead log panel** — phase chip (idle / building / committing) and the
  staged destination blocks. Button is Refresh live, **Simulate write** in demo;
  live tooltip says launch `writer`.

## What it is doing

Three lessons on one face. The cache grid shows *policy as motion* — which
buffer gets recycled and why. The block map shows *allocation locality as
shape*. The log panel shows the file system's hard problem: surviving a crash
halfway through a write — intentions written first, then acted on.

Log phase is **derived, not dumped**: `committing` flag set → "committing";
else staged blocks present → "building"; else "idle". Install is rendered as
narrative during commit; the transaction returning to idle with no blocks *is*
"installed".

## How it is bolted into xv6

- **buf.h** gains `uint lastuse` — the recency stamp an LRU shadow sorts on.
- **Hit path** (bget's found-it branch): `gini_bc_hits++`, `lastuse = ticks`.
- **Miss path + eviction shadow (index 4, "S1")**: hooked at the `// Not cached.`
  anchor, *before* the stock LRU scan. The student picks the victim from
  `{dev, blockno, bcache.buf, NBUF}`; validator requires the pointer inside
  `bcache.buf[]`, entry-aligned, `refcnt == 0`. On acceptance the **kernel**
  does the bookkeeping (dev/blockno/valid=0/refcnt=1/lastuse, `evicts++`,
  release, acquiresleep). The stock recycle path gets identical bookkeeping "so
  the two policies are compared on equal terms". S1 is the safest shadow — a
  wrong answer can't corrupt anything.
- **Block-allocator shadow (index 5, "S4")**: hooked at the top of
  `balloc(dev)`. The student returns only a block *number*; the kernel marks the
  bitmap, `log_write`s, `bzero`s — policy is the lesson, the on-disk bitmap
  never depends on student code. Validator: `bmapstart < bno < sb.size` and the
  real bitmap says free. Telemetry: `gini_ba_note()` accumulates
  `gapsum += |bno − last|`; the shipped first-fit path is instrumented
  identically.
- **Dumps**: `gini_fsdump()` prints the superblock in gdb-print shape (so
  existing parsers accept it), then `gini_bcdump()`, `gini_bmapdump()`,
  `gini_logdump()`.

## Wire format

`BC hits misses evicts nbuf` · `BUF slot blockno refcnt valid lastuse` ·
`BA allocs meangap last nblocks` · `BMAP <hex>` (LSB-first: bit i of byte n =
block n·8+i) · `LOG start outstanding committing n block={…}` · superblock
`size = … bmapstart = …`. Parsers in `xv6_fs.py`; `\bstart` boundary keeps LOG
parsing off `logstart`/`inodestart`/`bmapstart`. `have` gains
"blockmap"/"bcache" only when those lines are present, so an older kernel shows
"not available (real)" rather than fake data.

## Limits and honesty

- Cache counters are since-boot totals (not windowed) — a long-running machine's
  hit rate moves slowly.
- `Buf.dirty` is always False on real hardware — xv6 has no per-buffer dirty
  bit; writes go through the WAL. The field exists for the demo.
- `gini_bmapdump` does one `bread` per bitmap byte inside a console interrupt —
  real work, correctly charged to the observer (`gini_edge_obs`) since the
  bracket flag is up.
- NBUF = 30 is the whole point of `sgrind N`: N ≤ 30 stays hot, N ≥ 60 evicts
  what the next pass needs — the cliff is the lesson.

## Cross-references

[os-memory](os-04-memory.md) · [os-shadows](os-13-shadows.md) ·
[os-programs](os-14-programs.md) · [os-wire-protocol](os-01-wire-protocol.md)
