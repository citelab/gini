---
id: os-shadows
title: Shadows — student code with first refusal and a safety net
subsystem: shadows
layer: [kernel-patch, agent, domain, ui]
kernel_files: [kernel/proc.c, kernel/vm.c, kernel/bio.c, kernel/fs.c, kernel/kalloc.c, kernel/defs.h, kernel/shadows/gini_sched.c, kernel/shadows/gini_vm.c, kernel/shadows/gini_fs.c]
endpoints: [/shadows, /shadow/toggle, /shadow/enable, /rebuild, /revert, /reboot]
keywords: [shadow, student code, first refusal, fallback, validator, reject, wedge, manifest, bind mount, rebuild, revert, rr_sched, prio_sched, lottery_sched, vmfault, bget_evict, balloc, kalloc]
---

# Shadows — student code with first refusal and a safety net

## What is on the screen

The **shadow bar** on the Scheduler face: a status line rendering six
distinguishable states from the manifest — *wedged N× · N answers REJECTED ·
active ✓ · on-but-never-answers · on (stub → primary) · off (primary running)* —
plus the file hash; a "Use my shadow" checkbox; **Load** (rebuild) and
**Revert** buttons; an inline compile log scoped to lines naming the shadow
file.

## What it is doing

A shadow is a student-written decision function that gets **first refusal** on a
kernel decision, with the shipped implementation as the always-present safety
net. The student writes *policy*; the kernel keeps *mechanism*. The dispatch,
one path for all seven (`gini_shadow_call`):

```c
if (!enabled || !shadow) { active = 0; return 0; }      // → primary, silently
calls++;  ans = shadow(arg);
if (!ans) { active = 0; return 0; }                     // "not implemented" → primary
if (valid && !valid(arg, ans)) { rejects++; active = 0; return 0; }  // wrong → primary, counted
active = 1; return ans;
```

### The registry (indices are the wire format)

| idx | name | sub | decision | validator requires |
|-----|------|-----|----------|--------------------|
| 0 | rr_sched | sched | round-robin pick | pointer inside proc[], entry-aligned, RUNNABLE |
| 1 | prio_sched | sched | priority pick | same |
| 2 | lottery_sched | sched | lottery pick | same |
| 3 | vmfault | vm | lazy/demand fault handling | PA page-aligned in [KERNBASE, PHYSTOP), VA actually mapped afterwards |
| 4 | bget_evict | fs | buffer-cache victim ("S1") | inside bcache.buf[], aligned, refcnt == 0 |
| 5 | balloc | fs | disk-block allocation ("S4") | bmapstart < bno < sb.size, on-disk bitmap says free |
| 6 | kalloc | vm | physical-page allocation ("S3") | page-aligned, in range, ≥ end[], not set in the authoritative bitmap |

The scheduler validator exists because of a real bug: the old code handed the
student's pointer straight to `scheduler()`, which `acquire`d it and panicked
before the RUNNABLE recheck. S1 is the safest shadow (a wrong victim cannot
corrupt anything); S3 the most dangerous, which is why kalloc ships with an
authoritative allocation bitmap. In S4 and S1 the kernel performs all the
dangerous bookkeeping itself — the student only names a block/victim.

### Manifest fields and the wedge story

`SHADOW <name> present= enabled= active= faults= rejects= calls= sub= hash=`:

- **present/hash** — the kernel always says `0/baseline`; the **agent**
  re-stamps per subsystem by md5 against the pristine reference.
- **enabled** — boots 0 for every shadow; a reboot always brings the machine
  back without student code.
- **active** — the dispatcher used the shadow on the last decision.
- **faults** — the kernel cannot count its own crash; the agent's `Wedge`
  detector (10 s of silent dumps) blames whichever shadows were enabled. A
  *soft* wedge (picker loops with interrupts on) is caught frontend-side
  (`MachineState.stall()`, 8 s).
- **rejects** — validator refusals: the correctness signal
  (`Xv6Runner.shadow_rejects` asserts == 0: "may be a poor policy, but must
  never hand the kernel an illegal answer").
- **calls** — separates "my code is wrong" from "my code never runs".

`ShadowStatus.verdict` folds these into: not-started → wedged → off → rejected
→ never-runs → running.

## How it is bolted into xv6

- Registry + dispatcher in proc.c (§ lines ~113–162); adapters
  (`gsh_*`) shim naturally-typed student functions into the generic
  `void*(*)(void*)` slots; validators live next to what they check (vm.c needs
  `ismapped`, bio.c needs bcache, etc.).
- **Student files**: `kernel/shadows/gini_sched.c` (three functions:
  `pick_rr_shadow` correct — the worked example of the contract;
  `pick_prio_shadow` with the same weak-aging bug as the primary;
  `pick_lottery_shadow` uniform, ignoring tickets — fixing these *is* the lab),
  plus `gini_vm.c` and `gini_fs.c` stubs. Contract: return an answer or 0 to
  fall back; read-only; take no locks.
- **Bind mount**: a host directory `~/.gini/xv6-shadows/<element>/` is mounted
  over `kernel/shadows/` (a directory, not a file, because editors save via
  rename; under the GINI home so edits survive Stop/Run). The agent seeds
  missing files from `/opt/gini_*_ref.c` before starting QEMU (all three .o are
  in the Makefile, so a missing one fails the link).
- **Load loop**: edit → `POST /rebuild` (incremental make, 180 s, scoped
  errors) → QEMU restart. `POST /revert?sub=` restores one subsystem's stub.
- Console: Ctrl-K toggles the *current policy's* shadow only; Ctrl-G
  `<index><term>` toggles any of the seven (this is why the index route
  exists); Ctrl-X zeroes rejects+calls (no HTTP route).

## Wire format

See [os-wire-protocol](os-01-wire-protocol.md) — `/shadows` (agent-stamped).

## Limits and honesty

- Wedge blame is circumstantial by construction — "the shadows that were
  enabled when dumps stopped" — and deliberately never auto-reboots; the
  student owns the recovery, and the reboot-clears-enabled invariant makes it
  safe.
- `GINI_SCHED_HASH` is never `-D`'d at build time; the honest hash comes from
  the agent. Kernel-emitted present/hash are placeholders.
- Rejects are counted but the *reason* is not carried on the wire; the student
  reads the validator's rules (or this page) to interpret them.

## Cross-references

[os-scheduler](os-02-scheduler.md) · [os-memory](os-04-memory.md) ·
[os-storage](os-05-storage.md) · [os-architecture](os-00-architecture.md)
