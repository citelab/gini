# Shadow solutions + the end-to-end harness

Reference solutions for every shadow, the deliberately-wrong counterparts, and a harness that
proves the whole chain works. **None of this ships to students** — the Dockerfile copies only
`gini_patch.py`, `gini_agent.py` and `boot.sh`, so `solutions/` never enters the image. Students
get the stubs the patcher writes into `kernel/shadows/`.

## Why the wrong variants exist

A mission that cannot fail is decorative. The reference set proves an objective is *achievable*;
the wrong set proves it *discriminates*. Both are needed before a mission can be trusted.

| variant | what it is | what must happen |
|---|---|---|
| `reference/` | the correct implementation | the objective PASSES, `rejects == 0` |
| `wrong/` | legal C, bad policy — the validator has no complaint | the objective FAILS |
| `hostile/` | an illegal answer (wild pointer, in-use page, allocated block) | the validator REJECTS it, `rejects` climbs, **the machine keeps running** |

The hostile set is also the safety proof: with it loaded the kernel must stay usable, because
every answer is refused and the shipped code runs instead.

## Layout

```
solutions/
  reference/  gini_sched.c  gini_vm.c  gini_fs.c
  wrong/      gini_sched.c  gini_vm.c  gini_fs.c
  hostile/    gini_sched.c  gini_vm.c  gini_fs.c
  harness.py
```

One file per subsystem, matching what the student edits. A file holds every shadow in that
subsystem, so the harness enables exactly **one** shadow at a time — that is what makes a
measurement attributable to the shadow under test.

## Running it

```sh
# no machine needed: check the expectations themselves discriminate
python3 harness.py --selftest

# the real thing, against a RUNNING xv6 Machine (agent port 5000)
python3 harness.py --agent http://localhost:5000 --shadow all

# one at a time while debugging
python3 harness.py --agent http://localhost:5000 --shadow bget_evict --variant reference
```

For each case the harness installs the variant, rebuilds the kernel (`/rebuild`), enables just
that shadow (`/shadow/enable?i=N`), runs a workload that makes the effect measurable, samples the
live telemetry, and computes the verdict with **the app's own parsers and `Xv6Runner`** — so a
pass means the real chain works, not a reimplementation of it. It reverts to the shipped stubs at
the end so the machine is left in a known-good state.

## Expectations

| shadow | reference must show | wrong must show |
|---|---|---|
| `rr_sched` | `every_runnable_runs == 1` | `== 0` (first-runnable, no rotation → starvation) |
| `prio_sched` | `cpu_share(highest_priority) >= 0.45` | `< 0.45` (priority ignored) |
| `lottery_sched` | `share_ratio <= 0.15` | `> 0.15` (draws per-process, not per-ticket) |
| `vmfault` | `faults_handled > 0`, `fellthrough == 0` | `fellthrough > 0` (handles loads only) |
| `bget_evict` | `cache_hit_rate >= 0.5` | `< 0.5` (evicts the MRU buffer) |
| `balloc` | `mean_gap <= 4` | `> 4` (allocates from the far end) |
| `kalloc` | `max_free_run > 0` | recorded, not asserted — see below |

**Thresholds are first guesses.** They were chosen from the shape of each measure, not from
observed runs, and the first live session is what calibrates them. If a reference solution fails,
suspect the threshold before the solution. `kalloc`'s wrong variant is deliberately not asserted:
fragmentation depends on the allocate/free pattern, so a short workload may not separate the two
policies — record what you see and set a real bar afterwards.

## Notes

- **`rr_sched`'s reference is also the shipped worked example** in the student stub — it is the
  one policy that is correct out of the box, included so students can read the contract.
- **Scheduler shadows need their policy made current** (`/control?policy=N`) or the kernel never
  consults them; the harness does this.
- The harness needs the workload programs (`spin`, `alloc`, `writer`, `grind`), which the patcher
  and stock xv6 provide.
- Everything here compiles: all four sets (stubs, reference, wrong, hostile) are built against
  the patched kernel with the sandbox cross-toolchain before being committed.
