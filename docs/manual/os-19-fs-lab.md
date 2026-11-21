---
id: os-fs-lab
title: T-Lab 04 — You are the file system
subsystem: fslab
layer: [domain, ui, teaching-center]
kernel_files: []
endpoints: []
keywords: [T-Lab, file system, i-node, path walk, open, symbolic link, buffer, indirect block, single-indirect, double-indirect, hole, append, superblock, block reads, block writes, trace, simulator]
---

# T-Lab 04 — You are the file system

## What is on the screen

`fs_lab.py`, opened from a **T-Lab tile on the User Code hub** (Machine Lab → User Code).
One window, 940 × 700, like T-Lab 02. It needs **no machine running and no Docker**: a T-Lab
runs in GINI's own simulator.

Top: the level buttons **1–4**, the **goal chips** (`○` not yet, `✓` met; the goal is the chip's
tooltip) and **Undo · Reset · Random · Try every …** (*Try every path*, *Try every block* or
*Try every length*, by level). Under them, on the left, the **starts**: *Tutorial* (the FS
tutorial's own disk, so every answer in the tutorial can be watched coming out of the window),
*Yours* (new numbers whose answers are printed nowhere) and *Random* (a fresh disk from a seed;
the seed shows on the button, `Random · 7`). Level 3 has one start, *New file*.

**The disk map** (levels 1 and 4) is every block of the disk as a numbered square, 20 per row,
coloured by what it holds: boot, superblock, i-node table (darker where a block holds an i-node in
use), directory, file data, index block, link path, other files, free (dashed). A block read gets
a blue ring and the badge `R3` (its row in the trace), a block written an orange ring and `W5`,
both rings and `3·4` for both. Pointing at a block says what it holds in the strip under the map,
in words: `i-node block 4 holds i-nodes 16–23 (2 + ⌊i/8⌋ = 4). 19 /users/raj (dir, 80 bytes) ·
20 /users/raj/m (symbolic link)`.

**Level 1 · Walk the path** (Problem 1, `open()`). Pick a path in `open("…", O_RDONLY)` and
click the blocks the kernel reads, in order. A block already read in this call is free (rule 3):
clicking it gives a grey, unnumbered *no read* row, and clicking the next block to read passes
over the buffer hits before it, with a line saying so. A wrong click says what the kernel needs
next and, for an i-node, the formula (`i-node 52 is in block 2 + ⌊52/8⌋`) — never the answer —
and the strip shows what the clicked block actually is. The trace is the tutorial's table
(# · Block · Why). Side tabs: **Table** (the tutorial's table — name, i-node, i-node block,
data blocks, contents; click a name to open it; *Hide i-node blocks* turns that column into `?`
for practice) and **Every path** (every name opened cold, with its reads and buffer hits).

**Level 2 · Find byte n** (Problem 2(b), a 1-byte `read()` of an open file). The i-node is drawn
in memory with its 12 direct pointers and the single, double and triple pointers; each index
block that exists is a 16 × 16 grid of its 256 entries (rows labelled 0, 64, 128, 192), an entry
in use filled — amber for a data block, orange for an index block — with its name and value as
the tooltip (`double[1][61] → 67`). Pick an offset, click the i-node's pointer, then one entry in
each index block the kernel reads. A wrong pointer says what it serves (`single[46] serves file
block 58: bytes 59,392–60,415.`). **At or past the end** is right only when offset ≥ size. The
finished reads build the tutorial's table: Offset · File block · Pointer · Reads. Side tabs:
**The file** (the writes that made it, size, data and index blocks, the reach table) and **Every
block** (every file block coloured by what a 1-byte read there costs — data with 1, 2 or 3 reads
in blues, holes found with 0, 1 or 2 reads in oranges — with a count of each).

**Level 3 · Make a file with holes** (Problem 2(a), (d), (e)). The same i-node and grids,
read-only; `write(fd, buf, n) at offset off` with **Write**, the tutorial's lines as presets, and
a strip with the size, data blocks, index blocks (and their numbers) and the next free block.
Each write says which file blocks it touched and which blocks it took. Side tabs: **Writes** (each
write with its R + W) and **Every block** (each file block coloured by how many blocks a 1-byte
write there would take now: 0 where it exists, up to 3 in the double range; the triple range is
given as a number).

**Level 4 · Append and overwrite** (Problem 3). `append` or `write at` (`lseek` + `write`), a
byte count, and presets holding the tutorial's cases (`+3,000`, `+788`, `+789`, `1,024 at
1,024`, `1,024 at 1,000`). The **file strip** shows the file blocks around the write — filled to
how much data they hold, missing ones dashed, the bytes this write covers shaded, the pointer
under each (`d[11]`, `s[0]`, `[0][24]`). Click a block on the disk, then **R** or **W**: the step
is accepted (a row with the tutorial's why) or refused in red with the reason
(`Block 62 is new: there is nothing on it to keep, so it is written without a read (rule 4).`,
`Not yet: write data 62 before data 61: …`) and counted. **That's all** says how many accesses
are still missing and points at rules 5 and 6. A block written takes its new colour on the map.
Side tabs: **Plan** (which file blocks, which exist, which blocks are taken — answered only when
*Show the plan* is pressed), **Table**, and **Every length** (R and W against 1 … 6,000 bytes as
a step chart, and the intervals: `1–788: 2 R + 2 W`, `789–1,812: 3 R + 5 W`, …).

**Show me** does the next step for the student and **Watch** does it every 550 ms; both count,
and a walk, read or trace that used either reaches no goal.

## What it is doing

The engine (`gini.domain.fslab`) is the tutorial's toy UNIX-like file system and its seven
counting rules, fixed once:

- 1 KB blocks, 4-byte pointers, so an index block holds 256 pointers. Block 0 boot, block 1
  superblock, blocks 2–33 the i-node table (8 i-nodes per block, i-node *i* in block
  2 + ⌊*i*/8⌋), data from block 34. 12 direct pointers (file blocks 0–11), single (12–267),
  double (268–65,803), triple. 16-byte directory entries, 64 per block; the root is i-node 1;
  free blocks are handed out in increasing order.
- (1) a block read is one R, a block written one W; (2) no caching between calls; (3) within a
  call the kernel keeps what it read and writes each changed block once; (4) changing part of a
  block costs R + W, a new block or one replaced whole costs W; (5) a call that allocates costs
  R + W of the superblock, once; (6) an open file's i-node is in memory, and a call that changes
  it costs R + W of its i-node block; (7) timestamps are ignored.

Three calls are modelled. `open_call` walks a path (following a symbolic link by walking the path
it holds from the root, with the same buffer) and returns R and *hit* rows, each with its why and
what the kernel needs next. `read_call` follows the pointers to one byte and returns `data`,
`hole` (with where the 0 was) or `eof`. `write_call` returns the canonical trace in the
tutorial's order, the **partial order** any student order must respect, the plan, the blocks
taken and the file system after; a missing index block is taken just before the blocks under it
(*index block first*). The partial order is exactly "every block is read before it is written,
and written before the block that points to it", plus reading an index block before touching a
block whose number is in it and reading the superblock before writing a block it hands out. The
tutorial's 3,000-byte append has 11,070 acceptable orders; `check_step` judges each click
against them and says why a refusal is one.

The engine is a port of the reference engine the professor approved with the mockup, and every
number the tutorial prints is a golden test (`tests/test_fslab_golden.py`): 9, 10, 7, 8 and
10 reads for Problem 1; 601,000 bytes in 5 data and 3 index blocks and the five read costs for
Problem 2; the 3,000-byte append row by row (3 R + 7 W), index-last, 788 and 789 bytes, and the
two overwrites for Problem 3.

The sweeps run on the GUI thread, well under a frame each: Try every block is one `read_call` per
file block (no copy of the disk); Try every length calls `write_call` only once per distinct
(last block, ends on a boundary, grows the file) — the only things the counts depend on — and the
golden tests hold it equal to one call per length; level 3's sweep counts missing blocks along
each route (`blocks_to_take`) instead of writing. There is no worker thread, so the hazard in
[known issue 17](os-15-known-issues.md) cannot arise; Watch is a `QTimer`.

## How it is bolted in

It is not, to xv6: **no kernel is involved.** What is bolted in is the proof chain. Opening the
lab records a `lab_open`. Reaching a goal records **one entry per goal**, the first time, never
one per click:

| kind | when | carries | re-checked with |
|---|---|---|---|
| `fs_walk` | level 1 goals | level, start, seed, path, every block clicked (wrong ones too), hints, goal | `walk_clicks` |
| `fs_read` | level 2 goals | level, start, seed, offset, the pointers clicked (`["double", 1, 61]` or `["eof"]`), hints, goal | `pointer_clicks` |
| `fs_make` | level 3 goals | level, the writes `[[offset, n], …]`, goal | replaying the writes |
| `fs_trace` | level 4 goals | level, start, seed, op, offset, n, every step clicked `[[block, "R"], …]` including refused ones, hints, goal | `write_call` + `trace_steps` |

A Random start records its seed: `fslab.start_world(lab, level, "Random", seed)` rebuilds the
same disk on any machine. The Teaching Center lists these entries as **not checked** for now;
re-checking them with the replays above is the next step, and the entries already carry what it
needs.

The goals (design §5): *main.c in order*, *Through a link*, *Second directory block* (a walk that
reads a directory block that does not hold the name); *Three reads*, *A hole for free*, *A hole
that costs*, *Past the end* (pressed, not shown); *Big and thin* (≥ 1,000,000 bytes in at most
3 blocks), *All three ranges*; *No new block* (takes nothing and reads something), *Index block
first*, *Write without reading*. Past the end, Big and thin and All three ranges are about the
answer, so they do not care about Show me; every other goal needs a walk, read or trace without
it.

## Wire format

There is no wire. The level file is `gini/domain/tlabs/t-lab-04.yaml`, with `engine: fs` at the
top so `tlab_catalog` hands it to `fslab`. It declares the **disks** (each a `first_free`,
`other` blocks in use by files not in the table, and its names: a directory with its blocks and
entries, a file with its size and data blocks — runs written `[first, last]` — and a
`single`-indirect block if it has one, a link with its block and the path it holds) and the four
**levels**: `call` (`open`, `read`, `write`), the starts (a disk and a path, a file and its write
presets `{append: n}` / `{at: off, bytes: n}`, or a `new_file` with the writes that make it and
the offsets to offer; `random: true` for Random) and the goals (`kind`: `path`, `link`,
`second_block`, `reads`, `shape`, `trace`, with their numbers). The loader refuses a bad file in
one sentence (`level 'walk', start 'T' opens /users/nobody, which the disk cannot walk (…)`), so
a typo is found at load, not on a student's screen.

## Limits and honesty

- **No create, unlink or hard links** (the help notes' steps 4–7). The engine's model can take
  them with a small extension; the levels do not ask for them yet.
- **No FAT** version of the same disk (the help notes' Part D).
- **A walk is not animated**; Watch steps it on a timer, which is as close as this release gets.
- Files on a level disk stop at the single range; only writes made in the lab reach double and
  triple. Level 3 takes one write of at most 300,000 bytes, level 4 at most 20,000 bytes at an
  offset up to 300,000, so the trace stays a table.
- The disk map draws a 20-wide grid and grows by rows; a disk past a few hundred blocks would
  push the window, and no shipped or Random disk comes near it.
- The prototype drew level 2's Random offsets from anywhere in the first four data blocks, so an
  offset in the file's last, part-filled block could land past the end; the engine draws them
  from the bytes the block holds.
- **Progress is not persisted** between sessions: the goal chips reset when the window closes.
  What persists is the chain, when a code is armed.

## Cross-references

[os-architecture](os-00-architecture.md) · [os-storage](os-05-storage.md) ·
[os-scheduler-lab](os-17-scheduler-lab.md) (the frame this lab shares) ·
[os-known-issues](os-15-known-issues.md) §17 · [os-glossary](os-16-glossary.md) ·
the User Code hub (`ui/user_code_lab.py`) · the engine (`core/src/gini/domain/fslab.py`) ·
the design (`docs/design/t-lab-04.md`).
