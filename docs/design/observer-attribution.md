# Observer attribution: presume-grey for UART interrupts

**Status: implemented 2026-08-31, in a reduced form — see "What changed on contact
with the code" at the end. Needs a kernel build to verify.**

## The problem

The kernel board separates workload traffic (blue, `gini_edge`) from traffic
provoked by GINI's own polling (grey dashed, `gini_edge_obs`). The observer
flag `gini_obs[hart]` is raised by `gini_obs_begin()` — which runs inside
`consoleintr`, *after* the control byte has been read and dispatched. But the
byte arrives by interrupt, and an interrupt is anonymous at trap time: the
PLIC says "UART wants attention," not why. So everything on the delivery path —
the trap record, the door count (a *seized* / device interrupt, if it landed in
user mode), the plic→console edges — is committed as **blue before the kernel
can know it was us**.

Measured shape of the error: ~3 events per poll, times every poll (several per
second across `/procs`, `/board`, and whichever faces are open). On an idle
machine this leak is essentially **all** the blue on the board — the worst
possible place for it, since the idle board is exactly where we teach "everything
blue is the workload." A second, larger leak nobody flags today: **TX-completion
interrupts**. A dump is kilobytes of serial output; the UART interrupts that
drain it arrive after `gini_obs_end()` and are all counted blue.

## The insight that fixes it

Attribution does not need to be decided at trap entry — only **before the
counters are committed**, and the byte's identity is learned *inside the same
trap* (devintr → uartintr → consoleintr all run before the handler returns).
So no retroactive un-counting is ever needed. Combine that with the base rate —
on a GINI-observed machine, GINI causes the overwhelming majority of UART
traffic (tens of interrupts/second of polling and dump-drain vs. a human's
~5 keys/second peak) — and the right default inverts:

> **Presume a UART interrupt is observation (grey). Reclassify it as workload
> (blue) in the same trap, at the moment the byte turns out to be a real
> keystroke.**

The error term flips from "GINI's delivery counted as workload on every poll"
(systematic, frequent, and on the dominant path) to "a keystroke's delivery
briefly presumed grey until the byte is read µs later" — which is then
corrected before commit, i.e. **zero** for the RX path. What residual error
remains is rare and biased toward *under*-reporting workload, which is the
safe direction for the board's teaching claim.

## Design

### RX path (control bytes and keystrokes)

1. `devintr()`: on `irq == UART0_IRQ`, set `gini_obs_provisional[hart] = 1`
   before calling `uartintr()`. (Not the real `gini_obs` flag — see doors
   below.)
2. `consoleintr(c)`: first thing, resolve the provisional:
   - `c` is a GINI control byte (any of the dump/control set) →
     confirm observation; the existing `gini_obs_begin()` continues to bracket
     the dump body as today.
   - anything else (a keystroke destined for the shell) → clear the
     provisional; the interrupt is workload.
3. Deferred commits: the counters currently bumped before identity is known
   move to commit-at-resolution:
   - the plic→console **edge** increments buffer into a per-hart scratch
     (2–3 slots suffice; the events of one interrupt are sequential on one
     hart) and commit to `gini_edge` or `gini_edge_obs` at resolution;
   - the **trap ring** entry gains an `obs` bit stamped at resolution (wire:
     one extra field on `TR`, parser-optional like the CSR triple);
   - the **wakeup** `consoleintr` issues already happens after resolution, so
     it attributes correctly with no changes.
4. Multi-byte interrupts (keystroke and control byte drained by one interrupt):
   resolve as observation if *any* GINI byte was processed — rare, bounded,
   and errs grey (the safe direction).

### Doors

`gini_door` has no observer split at all today, so poll-delivery interrupts
inflate *seized* and deflate instructions-per-kernel-entry. Two options:

- **(a) door_obs twin**: add `gini_door_obs[3]`, commit external-interrupt
  doors at resolution into one or the other. Wire: a `BDOOROBS a b c` line
  (parsers ignore unknown lines, so old frontends are safe); the HUD shows
  workload doors and can annotate "+N us".
- **(b) suppress**: simply don't count observer-resolved entries as doors.
  Cheaper, but hides the observer entirely — against the board's honesty rule.

Recommend (a): the board's whole ethos is *show the footprint, labeled*.

Note `gini_doorrec()` runs at the very top of `usertrap` (it must — the
residency sample needs `gini_sub` still reading USER). The residency sample
stays at the top; only the **door commit** for external interrupts (scause
bit 63, code 9) defers to resolution. Timer interrupts (code 5) are never
UART and commit immediately, as today.

### TX path (dump drain — the bigger, unflagged leak)

RX identity comes from the byte; TX identity must come from **provenance of
the queued bytes**. Add a parallel bit-ring alongside `uart_tx_buf`: bytes
queued while `gini_obs[hart]` is up are marked grey (that is precisely the
dump body). The TX-completion interrupt attributes by the bytes it drained —
majority rule over the drained span is ample. User `printf` output queued
outside a dump stays blue. This closes the after-the-bracket drain leak that
the current design doesn't even list.

### What does not change

- The 0x1e/0x1f bracketing, the agent, and every existing parser field.
- Non-UART interrupts (virtio disk, timer): committed immediately, as today.
- `BEOBS` semantics — this proposal *adds* the delivery prefix and TX drain to
  the grey side; it doesn't move anything else.

## Residual error after the change

| Path | Today | After |
|------|-------|-------|
| Poll delivery (RX) | ~3 events/poll counted blue, every poll | 0 (resolved in-trap) |
| Keystroke delivery | correct (blue) | correct (blue, resolved in-trap) |
| Dump TX drain | all blue (unflagged leak) | grey via queued-byte tagging |
| Mixed RX interrupt | blue | grey (rare; errs toward under-reporting workload) |
| Doors | seized inflated by every poll | split via BDOOROBS |

The remaining bias is small, rare, and grey-leaning — the board would slightly
*understate* workload in pathological interleavings rather than overstate it on
every poll. For the classroom claim ("on an idle machine, nothing is blue"),
that is the right side to err on.

## Why not the dedicated channel instead?

A second UART/virtio console for GINI would make the interrupt itself
identifiable at the PLIC (attribution at entry, no presumption needed) and
would also fix the shared-wire dump-corruption hazard. It remains the *right
long-term architecture*, but it costs a QEMU device, a driver, agent changes,
and image-format churn. Presume-grey needs only patch-level changes inside
`gini_patch.py`, touches no wire compatibility, and removes ~all of the error
now. Do presume-grey first; keep the dedicated channel as the eventual
structural fix (it also subsumes the TX tagging).

## Implementation checklist (when scheduled)

- [ ] `gini_obs_provisional[NCPU]` + scratch commit buffer (trap.c)
- [ ] devintr hook: provisional on UART0_IRQ (trap.c §4h vicinity)
- [ ] consoleintr resolution at top of the GINI byte dispatch (console.c §4f)
- [ ] deferred door commit for scause-external; `gini_door_obs[3]` + `BDOOROBS`
- [ ] TR ring `obs` bit (optional trailing field)
- [ ] TX provenance bit-ring in uart.c; attribute TX-completion interrupts
- [ ] frontend: `kernel_board.py` parse BDOOROBS + TR obs bit; HUD "+N us" on doors
- [ ] update manual: os-08 limits, os-01 wire format, os-15 known-issues #7 → resolved
- [ ] classroom check: idle machine shows **zero blue**; typing shows blue console
      traffic (the C13 exercise still works, now cleanly)


---

## What changed on contact with the code

Three things, found while implementing.

**The TX-drain leak does not exist in this xv6.** The doc calls it "a second, larger leak nobody
flags today" and spends a bit-ring in `uart.c` on it. But a dump is `printk` → `consputc` →
`uartputc_sync`, and the source's own comment on that function reads: *"write a byte to the uart
without using interrupts, for use by kernel printk() ... it spins waiting for the uart's output
register to be empty."* The interrupt-driven writer is `uartwrite`, used *"only from write() system
calls"* — a program's own output, correctly blue. **A dump generates no TX interrupts at all**, so
there is nothing to tag and stage 2 is deleted rather than deferred.

**Presume-grey is wrong for half the interrupts.** `uartintr` wakes the transmitter BEFORE it reads
any byte, so an interrupt carrying no byte never reaches `consoleintr` and never resolves. Those are
exactly the write() completions above. Presuming them grey would paint a printing program as
measurement, permanently, with nothing to correct it. So the rule is narrower than the doc's:
**presume grey only where the guess gets checked**, which is the RX path. The default is workload;
observation is confirmed, not assumed.

**The window has to open before `plic_claim`.** The doc's design starts at `devintr`'s UART branch,
which is after the claim — and the plic edges are the *largest* share of the error, two per
interrupt (claim and complete) against console's one. That matches the board: `plic 16 · console 8`.
`plic_complete` also runs after `gini_obs_end()` has already lowered the real flag, so the window
has to close at the end of the branch rather than at resolution.

Kept from the doc unchanged: the central insight (settle before commit, never un-count), the
deferred edge buffer, `gini_door_obs` + `BDOOROBS` over suppression, and the judgement that a
dedicated channel is the right long-term architecture.

The buffer overflows into the OLD behaviour — counted as workload — rather than into a new and
unfamiliar wrongness, and a dump's own edges are never deferred because the real flag already
answers for them.

## What is verified, and what is not

Verified here: the patch applies to a real xv6-riscv clone; the hooks land in the right order
around `plic_claim` and `plic_complete`; the generated C compiles clean under `cc -fsyntax-only
-Wall -Wextra`; the parser reads `BDOOROBS` and a kernel that predates it still reads correctly.

**Not verified: that the kernel builds, or that an idle board goes black.** There is no RISC-V
toolchain on the machine this was written on. The classroom check at the end of the checklist is
still owed.
