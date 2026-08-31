---
id: os-wire-protocol
title: Wire protocol — control characters, brackets, and every record format
subsystem: platform
layer: [kernel-patch, agent, domain]
kernel_files: [kernel/console.c, kernel/proc.c, kernel/trap.c, kernel/vm.c, kernel/bio.c, kernel/fs.c, kernel/log.c, kernel/kalloc.c, kernel/syscall.c, kernel/spinlock.c]
endpoints: [/procs, /vm, /vmall, /faults, /fs, /sc, /traps, /locks, /shadows, /board, /control, /kill, /input, /shadow/enable, /shadow/toggle, /locks/reset]
keywords: [control characters, Ctrl-T, Ctrl-D, dump, bracket, 0x1e, 0x1f, record format, BDOOR, BSUB, FLT, TRACE, TC, TR, SHADOW, cumulative]
---

# Wire protocol — control characters, brackets, and every record format

## What is on the screen

Nothing directly — this is the plumbing every face reads through. The in-app
Terminal can emit any control character by hand (`terminal_view.py:480` maps
Ctrl-A..Z → 0x01..0x1a).

## What it is doing

The kernel exposes its state as plain-text **dumps** triggered by console control
characters, each wrapped in 0x1e (start) / 0x1f (end). The agent turns HTTP GETs
into control bytes and returns the framed block. Control *actions* (kill, policy,
quantum, shadow toggles) are also control characters — fired straight from the
UART interrupt so they can never be starved by the workload they act on.

### Dump keys (all bracketed)

| Key | Byte | Kernel fn | Contents | HTTP |
|-----|------|-----------|----------|------|
| Ctrl-T | 0x14 | `gini_dump()` | proc table, PROC/ALARM/WAIT, SCHED/POLICY, MODETIME, CSR, CPU/REGS | `GET /procs` |
| Ctrl-V | 0x16 | `gini_vmdump()` | VMF + KA + `vmprint` of the running proc | `GET /vm` |
| Ctrl-A | 0x01 | `gini_vmdump_all()` | VP/VL leaves for every proc (COW view) | `GET /vmall` |
| Ctrl-E | 0x05 | `gini_faultdump()` | FLT page-fault ring | `GET /faults` |
| Ctrl-F | 0x06 | `gini_fsdump()` | superblock, BC/BUF, BA/BMAP, LOG | `GET /fs` |
| Ctrl-S | 0x13 | `gini_scdump()` | SC histogram + TRACE ring | `GET /sc` |
| Ctrl-R | 0x12 | `gini_trapdump()` | TC taxonomy + TR ring | `GET /traps` |
| Ctrl-L | 0x0c | `gini_lockdump()` | LOCKCPU + LOCK lines | `GET /locks` |
| Ctrl-W | 0x17 | `gini_shadowdump()` | SHADOW manifest | `GET /shadows` (agent re-stamps) |
| Ctrl-D | 0x04 | `gini_boarddump()` | the kernel board (see below) | `GET /board` (wait=1.5) |

Stock Ctrl-P (`procdump`, with a GINI-added ppid column) stays **unbracketed** so
it still shows on the human console.

### Control keys (no dump output)

| Key | Byte | Action | HTTP |
|-----|------|--------|------|
| Ctrl-] | 0x1d | `sched_quantum++` (cap 100) | `POST /control?quantum=N` = Ctrl-\ then N−1 × Ctrl-] |
| Ctrl-\ | 0x1c | `sched_quantum = 1` | (same route) |
| Ctrl-B | 0x02 | `sched_policy = 0` | `POST /control?policy=N` = Ctrl-B then N × Ctrl-G — **broken, see limits** |
| Ctrl-K | 0x0b | toggle `gini_shadow[sched_policy].enabled` | `POST /shadow/toggle` |
| Ctrl-X | 0x18 | `gini_shadow_reset()` — zero rejects+calls | *not exposed over HTTP* |
| Ctrl-Z | 0x1a | `gini_lockreset()` | `POST /locks/reset` |
| Ctrl-Q | 0x11 | `gini_path_toggle()` — arm/disarm board path trace | *not exposed over HTTP* |
| Ctrl-C | 0x03 | `gini_break()` — kill highest-pid user proc | `POST /interrupt`, `/break` |

### Multi-character entry (state machines inserted BEFORE `switch(c)`)

Digits are consumed in the UART interrupt — they never echo, never reach the
shell. Each fires on the first non-matching terminator character:

- **Ctrl-Y `<pid digits> <term>`** → `gini_kill(pid)`; refuses pid ≤ 2 on both
  sides. Agent: `POST /kill?pid=` sends `\x19{pid}\n`.
- **Ctrl-O `<pid> <space|,> <val> <term>`** → `gini_setprio`;
  **Ctrl-N** same shape → `gini_setticket`. Agent: `/control/priority`,
  `/control/tickets`.
- **Ctrl-G `<index digits> <term>`** → `gini_shadow_toggle(i)` for any shadow
  index 0–6. Agent: `POST /shadow/enable?i=N`. This state machine is why the
  switch's own `case C('G')` (policy++) is unreachable — see
  [os-known-issues](os-15-known-issues.md).

`gini_boardreset()` exists but is bound to no key and no route (dead code).

## How it is bolted into xv6

All of §4f–§4f4 and §4h5 of `gini_patch.py` (lines ~1581–1672, 2211–2219) insert
into `consoleintr` in `kernel/console.c`. `gini_obs_begin()/end()` print the
0x1e/0x1f pair *and* raise `gini_obs[cpuid()]`, so every subsystem call a dump
provokes is charged to `gini_edge_obs` instead of the workload (patch 1934–1953).
Stated limit: the UART interrupt that delivers the control byte fires before the
flag goes up, so ~3 events per poll are still charged as real.

## Wire format — record reference

Formats are the literal printf strings; parsers cited. All hex fields are `%p`.

### /procs (Ctrl-T)

```
<pid> <state> <name> <ppid>                         per proc; states: unused used sleep runble run zombie
PROC <pid> pri <p> tk <t> lv <l> wait <w>           sched fields; pri lower=higher; wait=slices RUNNABLE
ALARM <pid> <interval> <ticks> <handlerVA> <on>     sigalarm state (GINI-owned fields)
WAIT <pid> <chanAddr> <chanName>                    every proc with p->chan != 0 (includes the
                                                    sleep_prepare window: runnable-with-a-channel)
SCHED policy <n> quantum <n>                        the kernel's actual knobs
POLICY <id> <name>                                  data-driven roster (round-robin, priority, lottery)
MODETIME user <u> kernel <k> idle <i>               cumulative ticks by privilege source
CSR sstatus <p> sie <p> sip <p> stvec <p> scause <p> sepc <p>
CPU <ci> pid <pid>                                  per busy hart
REGS cpu <ci> pid <pid> pc sp ra s0 a0 a7 satp sz   live trapframe registers per hart
```
Parsers: `xv6.py` — `_PROC_RE` (ppid optional so stock procdump parses),
`parse_proc_sched`, `parse_alarms` (filtered to interval>0), `parse_waits`
("?" → unnamed, typically a pipe), `parse_sched`, `parse_policies`,
`parse_modetime` + `mode_split`, `parse_csr`, `parse_cpu_lines`, `parse_cpu_regs`.

Registered sleep-channel names (patch 2132–2158, registry `GINI_NCHAN=12`):
console input · the log · a child to exit · a disk block · an inode · room in the
UART buffer · a free disk descriptor · the next timer tick. Pipes are per-pipe
allocations and stay raw addresses by design.

### /vm (Ctrl-V)

```
VMF handled <n> fellthrough <n>                     vmfault-shadow counters
KA free <n> total <n> maxrun <n> shadow <n>         page allocator; maxrun = largest contiguous free run
page table <root>                                   then vmprint of the RUNNING proc:
..<i>: pte <p> pa <p>                               depth = number of ".." (Sv39 level 2/1/0); leaf at depth 3
```
Parser: `xv6_vm.py:178–226`. VA rebuilt `(L2<<30)|(L1<<21)|(idx<<12)`; raw PTE
kept so A/D and RSW bits survive.

### /vmall (Ctrl-A)

```
VP <pid> <name> <sz>                                per-proc header; sz = top of heap
VL <pid> <va> <pa> <flags>                          one leaf; flags = low 10 PTE bits (V R W X U G A D RSW8/9)
```
Parser: `parse_vmall` → `{pid: ProcVm}`. Derived: `shared_frames` (PAs mapped by
>1 pid, user+valid leaves only), `Pte.cow` = user ∧ ¬writable ∧ RSW≠0.

### /faults (Ctrl-E)

```
FLT <pid> <scause-decimal> <va> <epc> <seq>         ring of last 256, oldest first; scause ∈ {12,13,15}
```
`va` = stval, `epc` = sepc, `seq` = global event clock. Parser `parse_faults`
(drops seq); `os_events.py` keeps seq and drops unstamped events.

### /fs (Ctrl-F)

```
size = N nblocks = N ninodes = N nlog = N logstart = N inodestart = N bmapstart = N
BC hits <h> misses <m> evicts <e> nbuf <NBUF>       cumulative; NBUF = 30
BUF <slot> <blockno> <refcnt> <valid> <lastuse>     one per buffer; refcnt 0 = evictable; lastuse = tick of last hit
BA allocs <n> meangap <g> last <b> nblocks <n>      meangap = mean |gap| between successive allocations
BMAP <hex…>                                         on-disk free/used bitmap, LSB-first: bit i of byte n = block n*8+i
LOG start = <s> outstanding = <o> committing = <c> n = <n> block = {b, b, …}
```
Parsers: `xv6_fs.py` — `parse_superblock` (order-independent), `parse_bcache`,
`parse_balloc` + `_unpack_bitmap`, `parse_logheader` (`\bstart` boundary avoids
`logstart`). Phase is **derived**: committing → "committing"; blocks non-empty →
"building"; else "idle". There is no separate "installing" record.

### /sc (Ctrl-S)

```
SC <num> <count>                                    cumulative per-syscall counters, zero rows suppressed, 64 slots
TRACE <pid> <num> <a0> <ret> <seq>                  ring of last 256; a0 captured before dispatch, ret after
```
Parsers: `parse_sccounts`, `parse_sctrace`; names via `SYSCALL_NAMES` (1–22;
Builder-added syscalls from 23 via the `extra` map).

### /traps (Ctrl-R)

```
TC <kind> <name> <count>                            all 6 kinds: syscall pagefault timer device illegal other
TR <pid> <kind> <cause> <epc> <tval> <sstatus> <sie> <sip> <seq>
```
CSRs are captured **at trap time** (a poll-time read could only describe the
console interrupt the dump caused). Recorded from both `usertrap` and
`kerneltrap` — without the kernel-side hook the device bucket stays empty.
Parsers: `parse_trapcounts`, `_TR_RE` (CSR triple optional for older images),
`TrapEvent.came_from` decodes sstatus.SPP.

### /locks (Ctrl-L)

```
LOCKCPU <ncpu>
LOCK <name> <acquires> <spins>                      aggregated by NAME (64 "proc" locks share one row);
                                                    spins = failed test-and-set attempts
```
`GINI_NLOCK = 24`; a 25th distinct name goes uncounted rather than mis-counted.

### /shadows (Ctrl-W)

```
SHADOW <name> present=<0|1> enabled= active= faults= rejects= calls= sub=<sched|vm|fs> hash=<hex|baseline>
```
The kernel always emits `present=0 hash=baseline`; the **agent** re-stamps both
per subsystem (md5 vs pristine ref) and substitutes its own wedge-blame count
into `faults=`. Parser: `parse_shadow_manifest` → `ShadowStatus.verdict`
(not-started / wedged / off / rejected / never-runs / running).

### /board (Ctrl-D)

```
BOARDN <n>                                          14 subsystems (parser ignores this line)
BSUB <i> <name> <ticks>                             timer-sampled residency; ids 0..13 =
                                                    user trap syscall proc memory file pipe inode log bcache disk console plic other
BEDGE <i> <j> <n>                                   exact calls crossing i→j (workload)
BEOBS <i> <j> <n>                                   …provoked by GINI's own polling
BDOOR <asked> <couldnt> <seized>                    the three doors (see os-kernel-board)
BSAMP <n>                                           residency samples behind the numbers
BTRAIL <n> <s0> <s1> …                              last ≤64 REAL positions, oldest first
BARM <0|1>                                          path trace armed?
BPATH <seq> <from> <to> <pid>                       ordered hops, ≤128, only while armed, observer excluded
BUSER <kinstr> <entries>                            user instructions (thousands) + contributing entries;
                                                    two numbers, not a ratio — ratios cannot be differenced
```
Parser: `kernel_board.py` (`^…$`-anchored regexes; unknown lines ignored so the
dump can grow). `Window` differences cumulative samples; a decrease is treated as
a counter restart, not a negative rate.

## Limits and honesty

- **Cumulative vs instant vs ring.** Cumulative: SC, TC, MODETIME, edges,
  residency, doors, BUSER, BC counters, BA allocs/gapsum, lock counters, VMF/KA
  shadow counters, shadow calls/rejects. Instant: proc rows, PROC/ALARM/WAIT,
  SCHED, CPU/REGS, CSR, BUF, BMAP, KA free, LOG, superblock, SHADOW flags.
  Rings (last N, re-sent every poll): TRACE/TR/FLT 256, BPATH 128, BTRAIL 64.
- Board counters print through `(int)` casts and wrap negative past ~2.1e9; the
  `(\d+)` parser regexes then silently drop the line. Ring *indices* were widened
  to `uint64` for exactly this class of bug; the printed values were not.
- `POST /control?policy=N` is broken for N>0 (Ctrl-G interception) — see
  [os-known-issues](os-15-known-issues.md). Use `/shadow/enable?i=` semantics as
  the model: index-carrying entry, explicit terminator.
- The frontend differencers: board `Window.add/_delta`; `SyscallRate`/`TrapRate`
  (60 s rolling); `mode_split`; `EventWindow` dedup by seq high-water mark.
  Not differenced anywhere: cache hit/miss/evict, BA allocs (shown since-boot),
  lock counters (manual Reset → Ctrl-Z).

## Cross-references

[os-architecture](os-00-architecture.md) · [os-kernel-board](os-08-kernel-board.md) ·
[os-known-issues](os-15-known-issues.md)
