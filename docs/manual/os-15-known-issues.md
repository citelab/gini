---
id: os-known-issues
title: Known issues — verified bugs and stale comments
subsystem: platform
layer: [kernel-patch, agent, domain]
kernel_files: [kernel/console.c, kernel/trap.c]
endpoints: [/control]
keywords: [bug, known issue, Ctrl-G, policy, unreachable, boardreset, stack-growth, integer wrap, stale comment, GINI_SCHED_HASH, step switch, gdb, swtch, detach]
---

# Known issues — verified bugs and stale comments

Found during the 2026-08-30 documentation pass, by static reading of the patch
and parsers. Each item states the evidence; none has yet been confirmed against
a running kernel unless noted — #11 is the exception, reported from a live lab
and then traced back to the code.

## 1. `POST /control?policy=N` is broken for N > 0 — Ctrl-G interception

**Fixed in source; ships on the next image rebuild (2026-09-09).** Policy now
has its own terminated digit-entry — Ctrl-B `<digits>` newline — in a new
`gini_polidx` machine (console.c §4f4b), mirroring the Ctrl-G shadow-index one,
so the two cannot collide. The two dead switch cases were removed and the
Ctrl-G machine is untouched, so shadows keep working. The agent's policy route
now writes one atomic `\x02<digits>\n` and clamps to `GINI_NPOLICY-1`, leaving
no pending state a following `/procs` poll could terminate. The original bug:

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

**Fixed in source; ships on the next image rebuild (2026-09-09).** Every
`uint64` counter and ring `seq` printed via `(int)`+`%d` now prints
`(uint64)`+`%lu`, verified against the pinned `kernel/printk.c` (`%lu` →
`printint(uint64, 10, 0)`). The fix went beyond the board dumps to every
subsystem with the same latent wrap — FLT/TC/TR/SC/TRACE, the board matrix
(BSUB/BEDGE/BEOBS/BDOOR/BSAMP/BPATH/BUSER), MODETIME, VMF, BC, BUF lastuse, BA
allocs, LOCK acquires/spins — since a board-only fix would still drop rows
elsewhere. Byte-identical below the wrap, so no parser changed and only rows
past 2.1e9 (previously dropped as negative) are now kept. The original bug:

`BSUB/BEDGE/BUSER` (and SC/TC/FLT seq) print via `(int)`; past ~2.1e9 they go
negative and the `(\d+)` parser regexes silently drop the line. Ring *indices*
were widened to `uint64` for exactly this hazard (comments at ~338–340); the
printed values were not. Long-running machines will quietly lose rows.

## 4. `gini_boardreset()` is dead code

Declared (~1801) and defined (~2061–2074) but bound to no console key and no
HTTP route. Board counters clear only on reboot. Either bind it (a key + route)
or delete it.

## 5. `GINI_SCHED_HASH` is never defined at build time

**Documented in source (2026-09-09).** Took the zero-risk option: a comment at
the `#define` and at the `gini_shadowdump` emit site now says the kernel-side
hash is a `baseline` placeholder the agent re-stamps (`_stamp_manifest`), not
the source of truth. Behaviour is unchanged and correct as designed; the raw
dump is no longer misleading to a reader. The original note:

No `-D` in the Dockerfile or `_rebuild()`, so the kernel always emits
`present=0 hash=baseline`; the agent's md5 re-stamping is what makes the
manifest honest.

## 6. Stale comments

**Fixed in source (2026-09-09).** The two "64-entry ring" comments now say
"256-entry ring" (`GINI_RING` is 256). The `machine_lab.py` shared-serial
hazard comment is left in place — it is still true and doing its job.

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

## 11. Step switch does not freeze the kernel it is meant to freeze

**Reported from a running lab (2026-09-08), then traced in the code.** Unlike
the entries above, this one started as an observation: on a kernel running only
`init` and `sh`, Step switch always shows the same stack —

```
#0 scheduler  kernel/proc.c:468
#1 main       kernel/main.c:44
```

— and once a program is launched (`spin`, `walker`, anything) Step stops doing
anything useful at all.

**The cause is that Step is two gdb sessions, and the first lets go before the
second starts.** `Xv6Bridge.step()`:

```python
def step(self):
    self.agent.post("/step")          # session 1: tbreak swtch; continue
    return self._detail_snapshot()    # session 2: GET /snapshot -> registers + bt
```

and `gini_agent.gdb_run` **always appends `detach`**, deliberately — "resume the
guest before gdb exits", so a client that dies mid-read cannot leave the kernel
halted. Correct in general, fatal here: session 1 halts at `swtch` and then
resumes the guest, so by the time session 2 attaches the moment is gone. The
comment on `step()` says "halted at swtch -> full frozen detail". It is not
frozen.

**Why the idle case looks like it works.** With `init` and `sh` both asleep,
`gini_pick()` returns NULL, the harts sit in `wfi`, and `swtch` is never called
at all. Session 1 waits out `TIMEOUT = 6` and returns `"gdb-timeout"` — nothing
was halted and nothing resumed. Session 2 then attaches to a genuinely idle
kernel and truthfully reports where the hart is: in `scheduler()`, called from
`main()`, which is the per-hart scheduler stack that belongs to no process (see
[architecture](os-00-architecture.md)). It looks like a successful step. Nothing
stepped, and it is identical every time because nothing is moving.

**Why the busy case is worse.** With a program running `swtch` IS reached, so
session 1 halts there, detaches, and the kernel runs on. Session 2 reads an
arbitrary later instant, so the registers and stack no longer describe the
context switch that Step exists to show.

**A second, independent squeeze.** `TIMEOUT = 6` seconds has to cover spawning
`gdb-multiarch`, connecting to the stub, loading kernel symbols, AND waiting for
the next context switch. At the default quantum that is fine; at the 10-tick
slice the UI offers (`~5.0 s`) it is marginal, so on a busy kernel Step will
sometimes time out and halt nothing.

**Fixed in source; ships on the next image rebuild (2026-09-09).** The fix is
structural, exactly as diagnosed: ONE gdb session now does both halves —
`tbreak swtch; continue; <read registers, bt, procs>; detach` — so the read
happens while the kernel is still stopped and the detail is frozen AT the
switch. `POST /step` now returns `{"switched", "registers", "bt", "procs",
"ticks"}` in one round trip, and `Xv6Bridge.step()` parses that instead of
making a second `/snapshot` call. Because the agent ships inside the image, the
fix is live only once the image is rebuilt (it rides the same rebuild as the
trap-capture work). A new gBuilder against an OLD image detects the missing
fields and falls back to the legacy two-session read, so it degrades to the old
behaviour rather than breaking.

The two smaller matters named above are handled too. The timeout now scales with
the quantum — floored at the old `TIMEOUT` so the default slice is unchanged,
and stretched for the 10-tick slice that used to time out. And a step that never
saw a switch now returns `switched=false`: the bridge keeps the last known
registers/stack but flags the miss, and the Lab shows "No context switch
happened while Step was waiting — the kernel was idle" in the stack panel instead
of presenting the idle scheduler stack as a captured switch.

Before the rebuild lands, the old caveat still holds on the running image: Step
is trustworthy only as "show me the idle scheduler stack", and the `Run`/`Pause`
sampling path (see [scheduler](os-02-scheduler.md)) is the honest way to watch
switching.

## 12. The agent is single-threaded, so a trap catch stalls every other reading

`gini_agent` runs on `http.server.HTTPServer`, which handles one request at a
time. `POST /trapcatch` holds that one request for its whole poll window (10 s by
default, 30 s at most), so for the duration of a catch every other reading queues
behind it: the Scheduler face's `/procs` (Gantt, HUD) and the Traps face's own
`/traps` histogram. The Traps face's `_busy` guard stays set while its blocked
read waits, so its live feed visibly freezes until the catch resolves. Nothing is
lost or wrong; it is a stall, and it ends when the catch does.

**Known; elected to skip (2026-09-09).** The fix is `ThreadingHTTPServer`, but
the serial stream is shared: two concurrent dumps would interleave inside one
0x1e/0x1f frame, so threading needs a serial lock on top of the existing gdb
`_LOCK`. Worth doing, not urgent.

## 13. The catch probe is itself a device interrupt — "any" and "device" usually catch GINI's own poll

The agent arms the capture over the serial line and then polls it with Ctrl-R
over the same line. Every byte that arrives is a UART receive interrupt, which
the PLIC raises as a supervisor external interrupt (scause 9), and `gini_kind`
classifies every external interrupt — UART and virtio-disk alike — as
`GT_DEVICE`. The capture hook (`gini_traprec`) runs at trap entry, before
`devintr` has claimed the interrupt and learned which device it was, and it does
not consult the observer flag. So the first trap after arming is, on an idle
machine, almost always the poll's own Ctrl-R interrupt. A student who picks
"device" catches it every time; "any" catches it whenever no workload trap beats
the probe to it.

**Known; elected to skip (2026-09-09).** This is inaccurate ATTRIBUTION, not a
false trap. The probe genuinely is a device interrupt: the kernel really took an
external trap with the scause, sepc and hart the frame shows, and a student
dissecting it sees exactly what a device interrupt looks like. What the frame
cannot say is that the device was GINI asking the kernel a question rather than
the workload's disk. The kernel cannot tell at capture time (the device identity
appears later, in `devintr`), so the principled fix — defer the capture decision
for external interrupts until the device is known and skip UART — is a real
design change, not a tweak. The cheap alternative would drop "any" from the combo
and relabel "device". Neither is being done now. Taught explicitly, "the trap you
just caught is the observer observing" is itself a sound lesson.

## 14. Each catch poll dumps the whole 256-entry trap ring

A catch polls Ctrl-R every 250 ms, and `gini_trapdump` prints the CATCH line,
six TC lines and up to `GINI_RING` (256) TR lines each time — roughly 260 lines,
40 times over a default catch, about ten thousand lines of serial output, every
byte a guest UART interrupt. It makes the catch slower than it needs to be and it
is self-perturbing: the observation manufactures the very device interrupts that
#13 then attributes.

**Known; elected to skip (2026-09-09).** The fix is a dedicated one-line
CATCH-only dump on a free console-mux selector, so a poll costs one line instead
of 260. Would ride a kernel rebuild alongside #13.

## 15. Three memory-ordering nits in the capture, all moot on QEMU

Found by reading the capture against RISC-V's weak memory model (RVWMO). None is
reachable on QEMU, whose TCG is effectively sequentially consistent, and the third
is tiny even on hardware. Recorded so the next reader does not re-derive them.

- **Arm order.** The console-mux arm writes `gini_catch_kind` and then
  `gini_catch_ready = 0`. RVWMO does not order two stores to different addresses
  without a fence, so another hart could observe the new kind, win the CAS,
  publish a frame and set `ready = 1` before the arm's clear lands and erases it:
  a completed capture reported as a timeout. Correct-by-model fix: clear `ready`
  first, `__sync_synchronize()`, then publish `kind`.
- **No acquire on the reader.** `gini_trapdump` reads `ready` and then the frame
  fields with no fence; the writer's fence is a release with no matching acquire,
  so in principle a reader could see `ready = 1` beside stale fields. Unreachable
  in practice: the poll runs milliseconds after a microsecond-scale write.
- **Same-kind re-arm.** The agent's kind-match closes the re-arm race only across
  DIFFERENT kinds. If a hart is mid-capture when the SAME kind is re-armed, the
  earlier frame can be returned as the new catch's result. The window is a few
  dozen instructions and the frame is still a genuine trap of the requested kind;
  the harm is a violated "next trap after arming" contract. A per-arm sequence
  number would close it.

**Known; elected to skip (2026-09-09).** All three would ride the same rebuild
as #13/#14 if that is ever taken up.

## 16. Kernel-mode timers and device pids are narrated from the user-mode, owner point of view

Two attribution gaps in the CPU journey, both parked under the deferred C-11
journeys (see `CPU_JOURNEY_CORRECTIONS.md`):

- A timer taken in **kernel mode** opens the preemption walkthrough, whose
  captions describe `uservec` saving a trapframe. The banner ("from KERNEL mode,
  no trapframe written"), the KERNEL band and the unlit save cards tell the
  truth, so the student gets a mixed signal rather than a clean error. The proper
  fix is the KTRAP journey.
- For a **device** interrupt the captured pid is whoever was on the core, not the
  event's owner (the kernel records this deliberately; `wakeup()` resolves the
  real owner later). The lab shows "pid 4 (sh)" for a disk interrupt with no
  bystander caveat. The DEVICE journey carries it. Today this interacts with #13,
  since the "device" a student catches is usually the probe.

**Known; deferred with C-11 (2026-09-09).** Frontend-only; no rebuild.

## 17. Fire-and-forget worker threads that emit into a dialog — a class of bug, not one lab

**Read this before adding a background read to any face.**

**CLOSED (2026-09-12) — the guarantee now lives at the destroy site, not in each face.** Both
death modes are covered for every face, present and future:

- *Destroyed on the worker thread* — `run_off_gui` (`ui/worker_host.py`) pins the owner in a
  GUI-thread registry for the call, so the widget's last reference never dies on a worker.
- *Destroyed under an in-flight `emit()`* — `join_owner()` waits for that owner's workers, and the
  **two places that actually destroy a face** call it: `MainWindow._retire_lab` and
  `MachineLab._retire`. Everything else in `ui/` that calls `deleteLater()` is clearing child
  widgets out of a layout, not retiring a face that runs workers.

Putting the join at the destroy site rather than in seventeen `stop_polling` methods is the point.
"Remember to give your new face a stop_polling" is not a rule anyone keeps — it is the same trap as
the `gini-core` floor in `scripts/release.sh`, which is checked rather than remembered. A face
added next year inherits this without knowing it exists. The three faces on `LivePollMixin`
(Traps, Memory, File System) keep their own joined `stop_polling`, which still runs first; the
join at the destroy site is what covers the rest.

Order is load-bearing and matches the mixin's: `stop_polling()` if present, then `close()` (which
sets `_closed`, so no worker may BEGIN an emit), then `join_owner()` (which waits out the one
already in flight), and only then `setParent(None)` + `deleteLater()`.

The join is **bounded** (`JOIN_TIMEOUT`, 2s). An unbounded one would hang the window on any wedged
read. A straggler that outlives the bound is not left dangerous: by then it holds no strong
reference to the owner, and the owner's `_closed` guard stops it emitting.

**Measured, because a race does not reproduce on demand and "it did not crash" proves nothing.**
Driving the real `MachineLab._retire` against a genuinely in-flight read: **0.00s before the fix
(it did not wait at all), 0.51s after** for a 0.5s worker, zero threads leaked. Pinned by
`tests/test_worker_outlives_dialog.py` — that retire waits, that the bound is honoured and reports
its straggler, and that a worker is joinable from the instant it starts rather than after a gap.

The rest of this entry is the original write-up, kept because the mechanism is still what anyone
adding a background read needs to understand.

**Scope, measured rather than assumed (2026-09-09).** Of the instances checked so far, exactly one
was ever a PRODUCTION crash — the Traps face, where `_retire()` destroyed a dialog with a catch in
flight, and which is fixed. The others found since are **test-harness artifacts**, and the
difference was established by experiment, not by reading:

- **SourceBrowser.** Crashed 5/5 when two Qt test modules ran in the wrong order. Give it the shape
  production uses — parented and kept alive, as `MainWindow` does with `self.source_browser`, which
  is built once and never destroyed — and the same run is 0/5. It needs `parent=None` and being
  dropped without closing, which only the tests do.
- **MachineLab.** Driving the real `_retire_lab()` sequence against a genuinely in-flight read
  (start a slow `_fetch`, then close + `setParent(None)` + `deleteLater()`) SURVIVED, with no
  threads leaked. Re-opening the Machine Lab mid-read does not crash gBuilder.

So the pattern is real and worth avoiding, but do not infer a production crash from a test crash:
check the three ingredients below for the case in front of you. Two things remain true and worth
fixing at leisure — neither is a user-facing defect:

1. **The suite's green is order-dependent.** Those pairs do not fail occasionally, they fail every
   time in that order; the full run passes only because pytest's alphabetical collection avoids the
   sequence. A `release.sh` run can therefore abort on a segfault that has nothing to do with the
   release. The fix is in the tests: close and delete what a test builds (the snapshot-and-flush
   fixture in `test_cpu_journey.py` is the pattern).
2. **MachineLab has no `stop_polling`,** so `_retire_lab()` finds nothing to call and returns
   immediately instead of waiting for an in-flight read. It survives today because `closeEvent`
   disconnects `snap_ready`, so a late emit has no receiver — a mitigation standing exactly where
   `_retire_lab`'s own docstring says a join belongs.

**The pattern, and why it kills.** A face reads off the GUI thread by spawning a raw
`threading.Thread`, and the worker hands the result back with `signal.emit()`, guarded only by
`if not self._closed`. Nothing joins the worker on close. That guard is check-then-act: a worker
already past the check can emit into a dialog that is being destroyed. It dies two ways —
(a) `_retire()` or a `deleteLater()` destroys the dialog under the in-flight emit; (b) the
worker's closure over `self` is the *last* Python reference, so the QObject is destroyed **on the
worker thread** when the closure dies. Both are undefined in Qt; both are a SIGSEGV. It is
nondeterministic (thread timing), which is exactly why it hid.

**The three ingredients.** It crashes only when all three coincide: (1) a worker that emits back
to the dialog; (2) the dialog destroyable while the worker is in flight — `parent=None` and
dropped, or a `deleteLater()` path such as `MachineLab._retire()`; (3) no join. In this codebase
(1) and (3) are the norm; (2) is what varies. Most production dialogs are Qt-parented and held by
attribute, which is why this was never a visible epidemic. The tests construct `parent=None` and
close at once, which is why they exposed it.

**The safe idiom is already in the tree: `LivePollMixin` (`ui/live_poll.py`).** `_closed` is set
*first*, then the worker is **joined** in `stop_polling()`, which `closeEvent` calls — and which
`MachineLab._retire()` looks up and calls before its `deleteLater()`. Once the flag is set no emit
can happen, and the join only waits for the read itself to return. A one-shot worker that may
block too long for a full join (the Traps catch, up to the agent's ~10 s) holds a **weak**
reference to the dialog plus a bounded join, so a straggler can neither emit into a dead dialog
nor become its last owner. The mixin's docstring is the specification; `TrapLab` is the worked
example of adopting it after the fact.

**Proof this is the mechanism, from the TrapLab case (fixed 2026-09-09).** Making every worker
synchronous — no production change — took the crash from 1–2 per 20 two-module runs to 0/20.
The fatal trace put the main thread in the Qt event flush with a worker mid-`emit()`. TrapLab had
no `stop_polling()`, so `_retire()` silently skipped the join; re-opening the Traps card or
closing the Machine Lab with a catch in flight was a gBuilder segfault path. After the fix:
0/40 crashes, and `_retire()` mid-catch is observed to block for the catch's duration — it joins.

**Census (2026-09-09) — a screen, not a verdict per file.** On the mixin, safe by construction:
`memory_lab`, `storage_lab`, `trap_lab`. Spawning a raw `threading.Thread` *and* emitting a
signal somewhere in the same file (18): `assistant`, `cpu_lab`, `fingerprint_lab`, `first_run`,
`flow_hud`, `fragment_manager`, `inspector`, `lock_lab`, `machine_lab`, `main_window`,
`mark_dialog`, `mcast_hud`, `peripherals`, `proof_strip`, `router_lab`, `routing_hud`,
`source_browser`, `syscall_lab`. Spawning without an emit: `hud`. The grep is a proxy only: a
`.join(` count includes string joins, and an `.emit(` in a file need not come from the worker.
Each entry needs one look — does its worker emit back, and can the dialog be destroyed mid-flight?
`machine_lab` is the one examined so far: fire-and-forget `_fetch`/`_bg`, not joined, mitigated
by disconnecting `snap_ready` on close and by Qt parenting. A disconnect is a mitigation, not the
fix — a worker can still be mid-emit when it lands.

**The rule for any new or touched face.** A read off the GUI thread uses `LivePollMixin`, or
tracks its thread and joins it in a `stop_polling()` that `closeEvent` calls. Never
`threading.Thread(...).start()`-and-forget inside a QDialog. A worker that must outlive close
holds a `weakref` to the dialog, never `self`.

**Known; documented and set aside (2026-09-09).** The confirmed crash (TrapLab) is fixed. The
rest is a face-by-face sweep against the census above, deliberately *not* bundled into one
change. A cheap guard when someone takes it up: a test that fails on any new raw
`threading.Thread(` under `ui/` in a file that is not a mixin adopter.

## Cross-references

[os-wire-protocol](os-01-wire-protocol.md) · [os-kernel-board](os-08-kernel-board.md) ·
[os-memory](os-04-memory.md) · [os-games](os-12-games.md)
