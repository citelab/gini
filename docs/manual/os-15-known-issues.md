---
id: os-known-issues
title: Known issues — verified bugs and stale comments
subsystem: platform
layer: [kernel-patch, agent, domain]
kernel_files: [kernel/console.c, kernel/trap.c]
endpoints: [/control]
keywords: [bug, known issue, Ctrl-G, policy, unreachable, boardreset, stack-growth, integer wrap, stale comment, GINI_SCHED_HASH]
---

# Known issues — verified bugs and stale comments

Found during the 2026-08-30 documentation pass, by static reading of the patch
and parsers. Each item states the evidence; none has yet been confirmed against
a running kernel unless noted.

## 1. `POST /control?policy=N` is broken for N > 0 — Ctrl-G interception

The Ctrl-G shadow-index state machine (§4f4, patch ~1662–1672) is inserted
*before* `switch(c)` in `consoleintr` and swallows `C('G')` unconditionally, so
the switch's `case C('G'): sched_policy++` (~1606) is **unreachable**. The
agent's policy route sends Ctrl-B then N × Ctrl-G with no terminator:

- `policy=1`: arms shadow-index entry and leaves it **pending**; the policy
  never changes, and the next console byte (typically the `\x14` of the next
  `/procs` poll) both fires `gini_shadow_toggle(0)` — silently flipping
  `rr_sched` — and is swallowed, so that dump returns nothing.
- `policy=2`: the second Ctrl-G terminates the entry, toggling shadow 0; policy
  stays 0.
- Knock-on: `Xv6Bridge.set_shadow` depends on `/control?policy=idx` and can only
  ever reach shadow 0. The Scheduler face's policy combo is affected the same
  way. `policy=0` (Ctrl-B alone) works. Quantum control is unaffected.

**Fix directions**: give policy its own byte (e.g. repurpose Ctrl-B as
`<index><term>` entry like Ctrl-G), or have the agent send a terminator after
the last Ctrl-G *and* remove the interception order dependency. Update
`_sync_policy_combo` expectations accordingly.

## 2. `stack-growth` classification is dead on live kernels

`classify_faults(..., regions)` only produces `stack-growth` when a region map
is supplied (branch 3), but the Memory face calls it without regions and
`_VmReader` leaves regions empty on real hardware — so a live stack-growth
fault classifies as **illegal**. `stack-growth` appears only via the demo path.
Fix directions: dump region extents from the kernel, or infer "just below the
current stack leaf" in the classifier.

## 3. Board counters wrap through `(int)` casts

`BSUB/BEDGE/BUSER` (and SC/TC/FLT seq) print via `(int)`; past ~2.1e9 they go
negative and the `(\d+)` parser regexes silently drop the line. Ring *indices*
were widened to `uint64` for exactly this hazard (comments at ~338–340); the
printed values were not. Long-running machines will quietly lose rows. Fix:
print `%lu`-style via the 64-bit path or emit high/low words.

## 4. `gini_boardreset()` is dead code

Declared (~1801) and defined (~2061–2074) but bound to no console key and no
HTTP route. Board counters clear only on reboot. Either bind it (a key + route)
or delete it.

## 5. `GINI_SCHED_HASH` is never defined at build time

No `-D` in the Dockerfile or `_rebuild()`, so the kernel always emits
`present=0 hash=baseline`; the agent's md5 re-stamping is what makes the
manifest honest. Working as designed *today*, but the kernel-side fields are
misleading to anyone reading the raw dump — worth a comment in the stub or
removal.

## 6. Stale comments

- §2a header (~329) and §2a2 (~394) still say "64-entry ring"; `GINI_RING` is
  **256** (defs ~970).
- `machine_lab.py` documents the shared-serial dump-corruption hazard
  (~41–45) — still true; kept here so it isn't re-discovered.

> **Proposed fix for #7 and the related delivery/TX leaks:**
> `docs/design/observer-attribution.md` — presume UART interrupts are
> observation, resolve to workload in the same trap when the byte is a
> keystroke; tag TX bytes by provenance. (Mahesh, 2026-08-30.)

## 7. Doors carry a small observer inflation

`gini_door` has no `_obs` twin, so a GINI poll's UART interrupt that lands
while a process is in user mode counts as one *seized*. Mostly invisible on
idle machines (polls land in kerneltrap). Documented on
[os-kernel-board](os-08-kernel-board.md); listed here because anyone comparing
door counts to an external count will see the delta.

## 8. Graded game runs are not persisted

The confusion matrix and score live in the session object only
(`diagnose.py`); closing the window loses the run. Relevant to the Fall 2026
plan to use graded decks as logged C-lab instruments — needs a capture path.

## 9. The trap ring is unsynchronised across harts

`gini_traprec()` writes into one shared ring, and neither the slot index nor the
per-kind counters are atomic:

```c
gini_trapcount[kind]++;                                   // read-modify-write
struct gini_trap *e = &gini_traps[gini_traps_i % GINI_RING];
...
gini_traps_i++;                                           // read-modify-write
```

`gini_stamp()` **is** atomic (`__sync_fetch_and_add`), so `seq` is sound; the
ring around it is not. On two or more harts two traps can take the same slot and
one is overwritten, and a `TC` count can lose an increment. At `XV6_CPUS=1`
(sizes S and M) it cannot happen; at L (2) and XL (4) it can.

**Accepted, not fixed.** The cost of a fix is a lock or a CAS on the hottest path
in the kernel — every trap on every hart — which would distort the very timing
the board measures. The board is a teaching instrument, not an audit log, and a
lost sample in a 64-entry ring changes no lesson.

What it means when reading the panels: on a multi-core machine, trap counts are
a **lower bound** and the history may have gaps. The `h<hart>` field on `TR`
(see [wire protocol](os-01-wire-protocol.md)) makes the interleaving visible, so
two adjacent rows from different cores are a normal sight rather than a symptom.

## 10. A losing hart still records a trap

`gini_traprec()` runs at the top of `usertrap`/`kerneltrap`, **before**
`devintr()` — and therefore before `plic_claim()`. A PLIC external interrupt is
asserted to every enabled hart, so on two cores both trap and both record, while
only the claim winner services the device. The loser's record is real (it did
take a trap) but it is not evidence of device activity, and external-interrupt
counts are inflated up to the core count.

Visible now that `TR` carries the hart: the same interrupt appears on two cores
one `seq` apart. Fixing it means either recording after the claim — which loses
the record for traps `devintr` does not handle — or stamping the claim result,
which is the better shape and is not yet done.

## Cross-references

[os-wire-protocol](os-01-wire-protocol.md) · [os-kernel-board](os-08-kernel-board.md) ·
[os-memory](os-04-memory.md) · [os-games](os-12-games.md)
